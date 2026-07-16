#!/usr/bin/env python3
"""Durable shadow readout — FRESH DB labels + per-cohort split + trigger alert.

WHY (landmine 2026-07-15): `analyze_shadow.py --gamma-cache` reads a resolution
cache that goes STALE the moment new markets trade, so it silently reports
"0 resolved -> UNDERPOWERED" and MASKS the real edge signal. Caught 2026-07-15:
the readout claimed 0 resolved markets when the live DB already knew 10 (and the
early edge was NEGATIVE, which the stale cache hid). This runner rebuilds the
token->outcome map FRESH from the `markets` table every run, then produces the
pre-registered readout SEPARATELY for cohort-1 (all roster) and cohort-2 (the
deep-dive admits, own start epoch — never pooled), appends to a durable log, and
writes an ALERT file when a cohort crosses the power bar OR its edge is
convincingly negative before then.

READ-ONLY: DB reads + the shadow JSONL; appends to the durable readout log.
INVOCATION (VPS / cron):
    DATABASE_URL=... PYTHONPATH=<repo> venv/bin/python scripts/shadow_readout.py
    ... --self-test    # offline: cohort split + alert logic, no DB/log
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze_shadow as az  # noqa: E402  (pure readout core, reused)

TRUST1 = 1783985376  # cohort-1: quote-fix redeploy epoch (2026-07-13 23:29 UTC)
TRUST2 = 1784143245  # cohort-2 fallback: watcher restart epoch (2026-07-15 19:20:45 UTC)


def load_cohorts(roster: dict) -> tuple[list[str], list[str], float]:
    """(cohort1_addrs, cohort2_addrs, cohort2_epoch) from the LIVE roster JSON
    (chain_audit.json). Membership comes from the roster file, NEVER from
    hardcoded lists (session-close review 2026-07-15 findings A/#9: the
    cohort-1 line used an EMPTY filter = the whole 24-roster, silently pooling
    cohort-2 into cohort-1's pre-registered readout; and a hardcoded cohort-2
    list would silently diverge when future ADMITs extend the roster).
    Fail-loud on inconsistency — a wrong split must never produce a readout."""
    clean = [str(a).lower() for a in roster.get("clean", [])]
    c1 = [str(a).lower() for a in roster.get("cohort1_original", [])]
    c2_blob = roster.get("cohort2") or {}
    c2 = [str(a).lower() for a in c2_blob.get("addresses", [])]
    if not c1 or not c2:
        raise ValueError("roster lacks cohort1_original/cohort2 keys — refusing "
                         "a readout on an ambiguous cohort split")
    if set(clean) != set(c1) | set(c2):
        raise ValueError(f"roster clean ({len(clean)}) != cohort1_original "
                         f"({len(c1)}) + cohort2 ({len(c2)}) — a new admission "
                         f"was made without extending the cohort ledger; fix "
                         f"chain_audit.json before reading out")
    try:  # admitted_utc (ISO, tz-aware) -> epoch; fallback = restart constant
        epoch = datetime.fromisoformat(str(c2_blob.get("admitted_utc"))).timestamp()
    except (TypeError, ValueError):
        epoch = TRUST2
    return c1, c2, epoch


async def fresh_outcomes(tokens: list[str]) -> dict[str, int]:
    """token_id -> 1 (won) / 0 (lost) from the markets table — FRESH, not a
    stale cache. Only definitively-resolved YES/NO markets contribute."""
    from base_engine.data.database import Database
    from sqlalchemy import text
    db = Database()
    await db.init()
    try:
        async with db.get_session() as s:
            await s.execute(text("SET LOCAL statement_timeout='60s'"))
            rows = (await s.execute(text(
                "SELECT resolution, resolved, yes_token_id, no_token_id "
                "FROM markets WHERE yes_token_id = ANY(:t) OR no_token_id = ANY(:t)"),
                {"t": tokens})).fetchall()
    finally:
        await db.close()
    out: dict[str, int] = {}
    for r in rows:
        m = r._mapping
        if not (m["resolved"] and m["resolution"] in ("YES", "NO")):
            continue
        yt, nt = str(m["yes_token_id"]), str(m["no_token_id"])
        out[yt] = 1 if m["resolution"] == "YES" else 0
        out[nt] = 0 if m["resolution"] == "YES" else 1
    return out


def cohort_readout(records, outcomes, trust_after, traders, cfg) -> dict:
    recs = az.filter_traders(records, traders)
    recs, _ = az.repair_records(recs, cfg.max_chase, cfg.max_spread, trust_after)
    return az.analyze(recs, outcomes, cfg.fee, cfg.econ_floor, cfg.p_min,
                      cfg.min_markets)


def concentration(res: dict) -> tuple[Optional[str], float]:
    """(dominant_trader, their share of first-buys). STANDING OPERATOR RULE
    (2026-07-15): every readout must disclose sample concentration BEFORE its
    aggregate is presented — the pooled cohort-1 edge turned out to be
    effectively ONE trader's edge (0x84dbb7 = 1,171 of 1,627 records), which a
    bare pooled number silently hides. Protocol-14 bucket-concentration applied
    to traders."""
    by = res.get("by_trader") or {}
    tot = sum(sum(c.values()) for c in by.values())
    if not tot:
        return None, 0.0
    top = max(by, key=lambda t: sum(by[t].values()))
    return top, sum(by[top].values()) / tot


def fmt_line(label: str, res: dict, min_markets: int) -> str:
    s = (f"[{label}] first-buys={res['first_buys']} OK-rate={res['ok_rate']:.1%} "
         f"tax_med={res['tax_p50']:+.4f} lag_p50={res['lag_p50']:.1f}s")
    top, share = concentration(res)
    if top is not None:
        s += f" conc={top[:10]}…{share:.0%}"
    if "shadow_edge" in res:
        s += (f" | resolved={res['resolved_mkts']}/{min_markets} "
              f"edge={res['shadow_edge']:+.4f} P(>0)={res['shadow_edge_p']:.3f} "
              f":: {res['edge_verdict']}")
    return s


def alerts_for(label: str, res: dict, min_markets: int,
               neg_p_max: float = 0.10, neg_min_n: int = 10) -> list[str]:
    """Trigger: cohort crosses the power bar (>= min_markets resolved), OR its
    edge is convincingly NEGATIVE before then (P(>0) <= neg_p_max on >= neg_min_n).
    Every alert carries the concentration disclosure — a verdict must never be
    run blind to who dominates the sample (standing operator rule 2026-07-15)."""
    out = []
    if "shadow_edge" not in res:
        return out
    top, share = concentration(res)
    conc = (f"; CONCENTRATION {top[:10]}…={share:.0%} — verdict requires the "
            f"per-trader breakdown + leave-one-out" if top else "")
    n = res["resolved_mkts"]
    if n >= min_markets:
        out.append(f"{label}: resolved {n} >= {min_markets} — POWERED; run the "
                   f"pre-registered verdict (edge={res['shadow_edge']:+.4f} "
                   f"P(>0)={res['shadow_edge_p']:.3f}){conc}")
    if (n >= neg_min_n and res["shadow_edge"] < 0
            and res["shadow_edge_p"] <= neg_p_max):
        out.append(f"{label}: edge NEGATIVE firming (edge={res['shadow_edge']:+.4f} "
                   f"P(>0)={res['shadow_edge_p']:.2f} on {n} mkts){conc}")
    return out


async def run(args) -> int:
    with open(args.roster) as f:
        c1, c2, c2_epoch = load_cohorts(json.load(f))
    recs = az.load_records(args.log)
    tokens = sorted({str(r["token_id"]) for r in recs if r.get("token_id")})
    outcomes = await fresh_outcomes(tokens)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    lines = [f"===== shadow readout {stamp}  (fresh DB labels: "
             f"{len(outcomes) // 2} resolved markets among {len(tokens)} shadow "
             f"tokens; cohorts from {os.path.basename(args.roster)}: "
             f"{len(c1)}+{len(c2)}) ====="]
    all_alerts: list[str] = []
    for label, trust, members in ((f"cohort1({len(c1)})", TRUST1, c1),
                                  (f"cohort2({len(c2)})", c2_epoch, c2)):
        res = cohort_readout(recs, outcomes, trust, ",".join(members), args)
        lines.append(fmt_line(label, res, args.min_markets))
        all_alerts += alerts_for(label, res, args.min_markets)
        # standing rule: when one trader dominates the sample, ALSO show the
        # cohort WITHOUT them — the pooled number alone is misleading
        top, share = concentration(res)
        if top and share >= args.conc_threshold and len(members) > 1:
            rest = [a for a in members if a.lower() != top.lower()]
            loo = cohort_readout(recs, outcomes, trust, ",".join(rest), args)
            lines.append("  " + fmt_line(f"{label} minus {top[:10]}… (LOO)",
                                         loo, args.min_markets))
    block = "\n".join(lines)
    print(block)
    with open(args.out, "a") as f:
        f.write(block + "\n")
    if all_alerts:
        with open(args.alert, "w") as f:
            f.write(stamp + "\n" + "\n".join(all_alerts) + "\n")
        print("*** ALERT:", "; ".join(all_alerts))
    else:
        # the ALERT file's EXISTENCE is the documented trigger signal — a
        # stale one from a prior day must not persist (review finding C)
        try:
            os.remove(args.alert)
        except FileNotFoundError:
            pass
        print("(no trigger — still accruing; a steward session should relay the "
              "line above to the operator)")
    return 0


def _self_test() -> int:
    print("SELF-TEST — shadow_readout cohort split + alerts (offline)\n")
    ok = True
    recs = [{"trader": "0xA", "token_id": "1", "verdict": "OK", "first_buy": True,
             "whale_price": 0.5, "shadow_fill": 0.5, "detect_lag_s": 3.0}]
    ok1 = (len(az.filter_traders(recs, "0xa")) == 1
           and az.filter_traders(recs, "0xB") == [])
    print(f"  [split] cohort filter case-insensitive : {ok1}"); ok &= ok1
    # powered trigger
    ok2 = any("POWERED" in a for a in alerts_for(
        "c", {"shadow_edge": 0.01, "shadow_edge_p": 0.9, "resolved_mkts": 35}, 30))
    print(f"  [alert] resolved >= min -> POWERED : {ok2}"); ok &= ok2
    # negative-firming trigger
    ok3 = any("NEGATIVE" in a for a in alerts_for(
        "c", {"shadow_edge": -0.05, "shadow_edge_p": 0.05, "resolved_mkts": 12}, 30))
    print(f"  [alert] negative firms before power bar : {ok3}"); ok &= ok3
    # no trigger while accruing positive-ish underpowered
    ok4 = alerts_for("c", {"shadow_edge": 0.01, "shadow_edge_p": 0.6,
                           "resolved_mkts": 12}, 30) == []
    print(f"  [alert] underpowered+noisy -> no trigger : {ok4}"); ok &= ok4
    # cohorts from the roster file, fail-loud on ledger drift
    good = {"clean": ["0xA", "0xB", "0xC"], "cohort1_original": ["0xa", "0xb"],
            "cohort2": {"addresses": ["0xC"],
                        "admitted_utc": "2026-07-15T19:16:00+00:00"}}
    c1, c2, ep = load_cohorts(good)
    ok5 = (c1 == ["0xa", "0xb"] and c2 == ["0xc"] and ep > 1_784_000_000
           and "0xc" not in c1)  # cohort-1 line can never include cohort-2
    print(f"  [cohorts] loaded from roster, disjoint, epoch parsed : {ok5}")
    ok &= ok5
    for bad in ({"clean": ["0xA"], "cohort1_original": [], "cohort2": {}},
                {"clean": ["0xA", "0xB", "0xC", "0xD"],  # admit w/o ledger
                 "cohort1_original": ["0xa", "0xb"],
                 "cohort2": {"addresses": ["0xc"], "admitted_utc": None}}):
        try:
            load_cohorts(bad)
            ok6 = False
        except ValueError:
            ok6 = True
        print(f"  [cohorts] inconsistent ledger -> refuses readout : {ok6}")
        ok &= ok6
    # concentration disclosure (standing operator rule 2026-07-15)
    dom = {"by_trader": {"0xwhale": {"OK": 9}, "0xother": {"OK": 1}}}
    top, share = concentration(dom)
    ok7 = top == "0xwhale" and abs(share - 0.9) < 1e-9
    print(f"  [conc] dominant trader + share computed : {ok7}"); ok &= ok7
    ok8 = concentration({"by_trader": {}}) == (None, 0.0)
    print(f"  [conc] empty cohort -> no top : {ok8}"); ok &= ok8
    # concentration string reaches the line and the alert
    line = fmt_line("c", {**dom, "first_buys": 10, "ok_rate": 1.0,
                          "tax_p50": 0.01, "lag_p50": 2.0}, 30)
    al = alerts_for("c", {**dom, "shadow_edge": 0.03, "shadow_edge_p": 0.99,
                          "resolved_mkts": 35}, 30)
    ok9 = "conc=0xwhale" in line and any("CONCENTRATION" in a for a in al)
    print(f"  [conc] disclosed in line AND in every alert : {ok9}"); ok &= ok9
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Durable per-cohort shadow readout "
                                             "with fresh DB labels + alerts")
    ap.add_argument("--log", default="/opt/pa2-shared/mirror3_shadow.jsonl")
    ap.add_argument("--roster", default="/opt/pa2-shared/mb_copyable_data/chain_audit.json",
                    help="live roster JSON; cohort membership comes from its "
                         "cohort1_original/cohort2 keys (NEVER hardcoded)")
    # deep_dive/ is polymarket-owned (mb_copyable_data itself is root-owned —
    # the cron runs as polymarket and must be able to write here)
    ap.add_argument("--out", default="/opt/pa2-shared/mb_copyable_data/deep_dive/shadow_readout_log.txt")
    ap.add_argument("--alert", default="/opt/pa2-shared/mb_copyable_data/deep_dive/shadow_readout_ALERT.txt")
    ap.add_argument("--fee", type=float, default=0.02)
    ap.add_argument("--econ-floor", type=float, default=0.02, dest="econ_floor")
    ap.add_argument("--p-min", type=float, default=0.95, dest="p_min")
    ap.add_argument("--min-markets", type=int, default=30, dest="min_markets")
    ap.add_argument("--max-chase", type=float, default=0.02, dest="max_chase")
    ap.add_argument("--max-spread", type=float, default=0.05, dest="max_spread")
    ap.add_argument("--conc-threshold", type=float, default=0.50,
                    dest="conc_threshold",
                    help="top-trader share of first-buys above which a leave-"
                         "one-out line is ALSO printed (standing operator rule)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        raise SystemExit(_self_test())
    raise SystemExit(asyncio.run(run(args)))
