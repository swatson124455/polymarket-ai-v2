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
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze_shadow as az  # noqa: E402  (pure readout core, reused)

TRUST1 = 1783985376  # cohort-1: quote-fix redeploy epoch (2026-07-13 23:29 UTC)
TRUST2 = 1784143245  # cohort-2 fallback: watcher restart epoch (2026-07-15 19:20:45 UTC)


_COHORT_RE = re.compile(r"^cohort(\d+)$")


def _parse_epoch(key: str, blob: dict, strict: bool) -> float:
    """admitted_utc -> epoch seconds. strict: a group WITH addresses but an
    unparsable admitted_utc fails loud (a bad epoch silently WIDENS the trust
    window). Empty/absent group falls back to TRUST2 (unused — no members)."""
    try:
        return datetime.fromisoformat(str(blob.get("admitted_utc"))).timestamp()
    except (TypeError, ValueError):
        if blob.get("addresses") and strict:
            raise ValueError(
                f"roster group '{key}' has addresses but an unparsable "
                f"admitted_utc — fix the ledger before reading out")
        return TRUST2


def load_cohorts(roster: dict) -> list[tuple[str, list[str], float]]:
    """Ordered [(name, addrs, epoch), ...] from the LIVE roster JSON
    (chain_audit.json). Membership comes from the file, NEVER hardcoded
    (session-close review 2026-07-15 findings A/#9: an empty filter silently
    pooled cohort-2 into cohort-1's pre-registered readout). Groups:
      - cohort1  — from `cohort1_original`, epoch TRUST1 (the quote-fix
        redeploy), ALWAYS present, the pre-registered readout.
      - cohort2, cohort3, ... — keys `cohort<N>` (N>=2), each
        {addresses, admitted_utc}, own PARSED epoch, NEVER pooled. A missing/
        bad admitted_utc fails loud (cohort<N> generalization 2026-07-19 for
        wave promotions; supersedes the cohort2-only hardcode).
      - probe — optional, observation-only (e.g. 0xf705fa pre-graduation),
        own line, never pooled, never part of a cohort verdict.
    Fail-loud on inconsistency (empty required group, cross-group overlap,
    clean != union) — a wrong split must never produce a readout."""
    c1 = [str(a).lower() for a in roster.get("cohort1_original", [])]
    if not c1:
        raise ValueError("roster lacks a non-empty cohort1_original — refusing "
                         "a readout on an ambiguous cohort split")
    groups: list[tuple[str, list[str], float]] = [("cohort1", c1, TRUST1)]
    nums = []  # additional admitted cohorts: keys 'cohort<N>' with N>=2 only
    for k in roster:
        m = _COHORT_RE.match(k)
        if m and int(m.group(1)) >= 2:
            nums.append(int(m.group(1)))
    nums.sort()
    if not nums:
        raise ValueError("roster lacks a cohort2+ admitted group — refusing "
                         "a readout on an ambiguous cohort split")
    for n in nums:
        key = f"cohort{n}"
        blob = roster.get(key) or {}
        addrs = [str(a).lower() for a in blob.get("addresses", [])]
        if not addrs:
            # fail loud (restores HEAD's `if not c2: raise`): an empty admitted
            # cohort would reach cohort_readout with members="" -> filter_traders
            # treats "" as "all records" and the whole roster pools under this
            # label, able to fire a FALSE 'POWERED' go/no-go alert (the exact
            # silent-pooling class of 2026-07-15 finding A/#9). Verified defect,
            # adversarial review 2026-07-19.
            raise ValueError(
                f"roster group '{key}' present but has NO addresses — an empty "
                f"admitted cohort pools the whole roster under its label; "
                f"remove the key or populate its members")
        groups.append((key, addrs, _parse_epoch(key, blob, strict=True)))
    probe_blob = roster.get("probe") or {}
    probe = [str(a).lower() for a in probe_blob.get("addresses", [])]
    if probe:
        groups.append(("probe", probe,
                       _parse_epoch("probe", probe_blob, strict=True)))
    for name, addrs, _ in groups:
        # intra-group duplicate (adversarial review 2026-07-19, finding #1):
        # a dup passes the cross-group set() checks but inflates the label
        # count AND breaks the leave-one-out `rest` (a duplicated top trader
        # leaves rest=[] -> filter_traders("") -> whole-roster pooling in the
        # LOO line). Fail loud — a dup is a ledger typo, not a valid split.
        if len(addrs) != len(set(addrs)):
            raise ValueError(f"roster group '{name}' has a DUPLICATE address — "
                             f"fix the ledger before reading out")
    memberships = [set(a) for _, a, _ in groups]
    union = set().union(*memberships)
    if sum(len(m) for m in memberships) != len(union):
        raise ValueError("cohort/probe group OVERLAP — an address in two groups "
                         "would be pooled into two readouts; fix the ledger "
                         "before reading out")
    clean = {str(a).lower() for a in roster.get("clean", [])}
    if clean != union:
        raise ValueError(f"roster clean ({len(clean)}) != the union of "
                         f"cohort1_original + cohort<N> + probe ({len(union)}) "
                         f"— a roster change was made without extending the "
                         f"ledger; fix chain_audit.json before reading out")
    return groups


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
        cohorts = load_cohorts(json.load(f))
    recs = az.load_records(args.log)
    tokens = sorted({str(r["token_id"]) for r in recs if r.get("token_id")})
    outcomes = await fresh_outcomes(tokens)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    probe_n = next((len(a) for n, a, _ in cohorts if n == "probe"), 0)
    counts = "+".join(str(len(a)) for n, a, _ in cohorts if n != "probe")
    if probe_n:
        counts += f"+{probe_n}probe"
    lines = [f"===== shadow readout {stamp}  (fresh DB labels: "
             f"{len(outcomes) // 2} resolved markets among {len(tokens)} shadow "
             f"tokens; cohorts from {os.path.basename(args.roster)}: "
             f"{counts}) ====="]
    all_alerts: list[str] = []
    for name, members, trust in cohorts:
        label = f"{name}({len(members)})"
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
    def as_map(roster):
        return {n: (a, e) for n, a, e in load_cohorts(roster)}
    good = {"clean": ["0xA", "0xB", "0xC"], "cohort1_original": ["0xa", "0xb"],
            "cohort2": {"addresses": ["0xC"],
                        "admitted_utc": "2026-07-15T19:16:00+00:00"}}
    g = as_map(good)
    ok5 = (g["cohort1"][0] == ["0xa", "0xb"] and g["cohort2"][0] == ["0xc"]
           and g["cohort1"][1] == TRUST1 and g["cohort2"][1] > 1_784_000_000
           and "probe" not in g)
    print(f"  [cohorts] loaded from roster, cohort1=TRUST1, epoch parsed : {ok5}")
    ok &= ok5
    # probe group (2026-07-16): own membership+epoch, counted in the ledger
    withp = {"clean": ["0xA", "0xB", "0xC", "0xD"],
             "cohort1_original": ["0xa", "0xb"],
             "cohort2": {"addresses": ["0xC"],
                         "admitted_utc": "2026-07-15T19:16:00+00:00"},
             "probe": {"addresses": ["0xD"],
                       "admitted_utc": "2026-07-16T17:00:00+00:00"}}
    g = as_map(withp)
    ok5b = (g["probe"][0] == ["0xd"] and g["probe"][1] > g["cohort2"][1]
            and "0xd" not in g["cohort1"][0] and "0xd" not in g["cohort2"][0])
    print(f"  [cohorts] probe group parsed, disjoint from cohorts : {ok5b}")
    ok &= ok5b
    # cohort3 wave promotion (2026-07-19): Nth admitted cohort, own epoch,
    # ORDERED after cohort2, never pooled, probe stays separate + last
    wave = {"clean": ["0xA", "0xB", "0xC", "0xD", "0xE"],
            "cohort1_original": ["0xa", "0xb"],
            "cohort2": {"addresses": ["0xC"],
                        "admitted_utc": "2026-07-15T19:16:00+00:00"},
            "cohort3": {"addresses": ["0xD"],
                        "admitted_utc": "2026-07-19T13:00:00+00:00"},
            "probe": {"addresses": ["0xE"],
                      "admitted_utc": "2026-07-16T17:00:00+00:00"}}
    order = [n for n, _, _ in load_cohorts(wave)]
    g = as_map(wave)
    ok5c = (order == ["cohort1", "cohort2", "cohort3", "probe"]
            and g["cohort3"][0] == ["0xd"]
            and g["cohort3"][1] > g["cohort2"][1])
    print(f"  [cohorts] cohort3 wave: ordered, own epoch, not pooled : {ok5c}")
    ok &= ok5c
    for bad in ({"clean": ["0xA"], "cohort1_original": [], "cohort2": {}},
                {"clean": ["0xA", "0xB", "0xC", "0xD"],  # admit w/o ledger
                 "cohort1_original": ["0xa", "0xb"],
                 "cohort2": {"addresses": ["0xC"],
                             "admitted_utc": "2026-07-15T19:16:00+00:00"}},
                {"clean": ["0xA", "0xB"],  # NO cohort2+ admitted group
                 "cohort1_original": ["0xa", "0xb"]},
                {"clean": ["0xA", "0xB", "0xC"],  # EMPTY admitted cohort ->
                 "cohort1_original": ["0xa", "0xb"],  # would pool whole roster
                 "cohort2": {"addresses": [],
                             "admitted_utc": "2026-07-15T19:16:00+00:00"},
                 "cohort3": {"addresses": ["0xC"],
                             "admitted_utc": "2026-07-19T13:00:00+00:00"}},
                {"clean": ["0xA", "0xB", "0xC"],  # INTRA-GROUP DUPLICATE ->
                 "cohort1_original": ["0xa", "0xa", "0xb"],  # inflates+breaks LOO
                 "cohort2": {"addresses": ["0xC"],
                             "admitted_utc": "2026-07-15T19:16:00+00:00"}},
                {"clean": ["0xA", "0xB", "0xC"],  # OVERLAP: 0xC in two groups
                 "cohort1_original": ["0xa", "0xb"],
                 "cohort2": {"addresses": ["0xC"],
                             "admitted_utc": "2026-07-15T19:16:00+00:00"},
                 "probe": {"addresses": ["0xc"],
                           "admitted_utc": "2026-07-16T17:00:00+00:00"}},
                {"clean": ["0xA", "0xB", "0xC", "0xD"],  # cohort3 BAD epoch
                 "cohort1_original": ["0xa", "0xb"],
                 "cohort2": {"addresses": ["0xC"],
                             "admitted_utc": "2026-07-15T19:16:00+00:00"},
                 "cohort3": {"addresses": ["0xD"], "admitted_utc": "garbage"}},
                {"clean": ["0xA", "0xB", "0xC", "0xD"],  # probe w/ BAD epoch
                 "cohort1_original": ["0xa", "0xb"],
                 "cohort2": {"addresses": ["0xC"],
                             "admitted_utc": "2026-07-15T19:16:00+00:00"},
                 "probe": {"addresses": ["0xD"], "admitted_utc": "garbage"}}):
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
