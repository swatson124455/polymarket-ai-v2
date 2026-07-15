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
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze_shadow as az  # noqa: E402  (pure readout core, reused)

# cohort-2 = the 8 chain-deep-dive ADMITs (2026-07-15); own start epoch
COHORT2 = [
    "0x0e5bd76779e74304d08e759072abf126d87da593",
    "0x4ad6cadefae3c28f5b2caa32a99ebba3a614464c",
    "0x7744bfd749a70020d16a1fcbac1d064761c9999e",
    "0xa2f1fecf1cc7db65a46588f764b6691533052d22",
    "0xbaa2bcb5439e985ce4ccf815b4700027d1b92c73",
    "0xc660ae71765d0d9eaf5fa8328c1c959841d2bd28",
    "0xd1acd3925d895de9aec98ff95f3a30c5279d08d5",
    "0xe25b9180f5687aa85bd94ee309bb72a464320f1b",
]
TRUST1 = 1783985376  # cohort-1: quote-fix redeploy epoch (2026-07-13 23:29 UTC)
TRUST2 = 1784143245  # cohort-2: watcher restart epoch (2026-07-15 19:20:45 UTC)


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


def fmt_line(label: str, res: dict, min_markets: int) -> str:
    s = (f"[{label}] first-buys={res['first_buys']} OK-rate={res['ok_rate']:.1%} "
         f"tax_med={res['tax_p50']:+.4f} lag_p50={res['lag_p50']:.1f}s")
    if "shadow_edge" in res:
        s += (f" | resolved={res['resolved_mkts']}/{min_markets} "
              f"edge={res['shadow_edge']:+.4f} P(>0)={res['shadow_edge_p']:.3f} "
              f":: {res['edge_verdict']}")
    return s


def alerts_for(label: str, res: dict, min_markets: int,
               neg_p_max: float = 0.10, neg_min_n: int = 10) -> list[str]:
    """Trigger: cohort crosses the power bar (>= min_markets resolved), OR its
    edge is convincingly NEGATIVE before then (P(>0) <= neg_p_max on >= neg_min_n)."""
    out = []
    if "shadow_edge" not in res:
        return out
    n = res["resolved_mkts"]
    if n >= min_markets:
        out.append(f"{label}: resolved {n} >= {min_markets} — POWERED; run the "
                   f"pre-registered verdict (edge={res['shadow_edge']:+.4f} "
                   f"P(>0)={res['shadow_edge_p']:.3f})")
    if (n >= neg_min_n and res["shadow_edge"] < 0
            and res["shadow_edge_p"] <= neg_p_max):
        out.append(f"{label}: edge NEGATIVE firming (edge={res['shadow_edge']:+.4f} "
                   f"P(>0)={res['shadow_edge_p']:.2f} on {n} mkts)")
    return out


async def run(args) -> int:
    recs = az.load_records(args.log)
    tokens = sorted(set(str(r["token_id"]) for r in recs if str(r.get("token_id"))))
    outcomes = await fresh_outcomes(tokens)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    lines = [f"===== shadow readout {stamp}  (fresh DB labels: "
             f"{len(outcomes) // 2} resolved markets among {len(tokens)} shadow tokens) ====="]
    all_alerts: list[str] = []
    for label, trust, traders in (("cohort1-all", TRUST1, ""),
                                  ("cohort2", TRUST2, ",".join(COHORT2))):
        res = cohort_readout(recs, outcomes, trust, traders, args)
        lines.append(fmt_line(label, res, args.min_markets))
        all_alerts += alerts_for(label, res, args.min_markets)
    block = "\n".join(lines)
    print(block)
    with open(args.out, "a") as f:
        f.write(block + "\n")
    if all_alerts:
        with open(args.alert, "w") as f:
            f.write(stamp + "\n" + "\n".join(all_alerts) + "\n")
        print("*** ALERT:", "; ".join(all_alerts))
    else:
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
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Durable per-cohort shadow readout "
                                             "with fresh DB labels + alerts")
    ap.add_argument("--log", default="/opt/pa2-shared/mirror3_shadow.jsonl")
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
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        raise SystemExit(_self_test())
    raise SystemExit(asyncio.run(run(args)))
