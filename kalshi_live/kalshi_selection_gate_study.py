#!/usr/bin/env python3
"""SELECTION-GATE STUDY — offline, read-only, on the FROZEN book dataset.

Question: MIN_DEPTH_SYM / MAX_SPREAD_TICKS reject wide/one-sided books. How much REWARD-SIDE
capture does that rejection give up, and are the rejected markets earnable under R3?

Method: replay `kalshi_live/concentration_samples.jsonl` (full yl/nl level lists, per snapshot)
through (a) the deployed gate order, and (b) the recorder's CFTC scoring core
(`scripts/maker_kalshi_recorder.side_share`, same core the throttle A/B uses), sized with the
DEPLOYED quote sizes (`_capped_join`, i.e. MAX_MARKET_CAPITAL/2 per side, floor 1, cap JOIN_SIZE)
— NOT the recorder's own 100 ct.

R3 handled per canon: a snapshot whose EXTERNAL book misses Target Size on either side pays
NOBODY, so it is EXCLUDED from both numerator and denominator. Inclusion rate is reported.

WHAT THIS CANNOT SEE — repeat with every number:
  * FILL RATE / ADVERSE SELECTION. Reward side only. The gate exists as a FILL-side guard
    ("a one-sided book adverse-selects us then won't let the passive exit fill"), and nothing
    here measures that. A reward gain from relaxing it is NOT a net gain.
  * Sample: 27 snapshots over 23 MINUTES (02:25-02:48Z 07-23), gas only, ~13 markets.
    Highly autocorrelated — treat as ~1 market-state, not 353 independent draws.
  * Canon §M7(d): this model class over-predicts receipts by ~2-6x. Ratios between arms are
    more trustworthy than levels.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

for _k, _v in {
    "KALSHI_JOIN_SIZE": "20", "KALSHI_MAX_MARKET_CAPITAL": "15",
    "KALSHI_MAX_TOTAL_CAPITAL": "85", "KALSHI_MAX_ACTIVATE_CAPITAL": "15",
    "KALSHI_MAX_PRICE_DOLLARS": "0.96", "KALSHI_MIN_PRICE_DOLLARS": "0.04",
    "KALSHI_MAX_SPREAD_TICKS": "8", "KALSHI_MIN_DEPTH_SYM": "0.25",
    "KALSHI_TRADING_MODE": "dry_run",
}.items():
    os.environ[_k] = _v

import maker_kalshi_quoter as Q            # noqa: E402
import maker_kalshi_recorder as REC        # noqa: E402

TICK = 0.01
SAMPLES = os.path.join(HERE, "concentration_samples.jsonl")


def gate(row, sym_min, spread_max):
    """Deployed gate order, from FLAT. Returns (reason, info)."""
    yl = [(float(p), float(s)) for p, s in row["yl"] if float(s) > 0]
    nl = [(float(p), float(s)) for p, s in row["nl"] if float(s) > 0]
    by = max((p for p, _ in yl), default=None)
    bn = max((p for p, _ in nl), default=None)
    dy = sum(s for _, s in yl)
    dn = sum(s for _, s in nl)
    info = {"best_y": by, "best_n": bn, "dy": dy, "dn": dn, "target": row["target"]}
    if by is None or bn is None:
        return "one_side_no_bids", info
    if not (Q.MIN_PRICE_DOLLARS < by <= Q.MAX_PRICE_DOLLARS) or \
       not (Q.MIN_PRICE_DOLLARS < bn <= Q.MAX_PRICE_DOLLARS):
        return "price_bounds", info
    if by + bn >= 1.0:
        return "crossed_book", info
    target = row["target"]
    void = dy < target or dn < target
    addable = Q.MAX_ACTIVATE_CAPITAL / max(by, bn, 0.01)
    if not ((dy + addable >= target) and (dn + addable >= target)):
        return "unqualifiable_R3", info
    info["spread_ticks"] = (1.0 - by - bn) / TICK
    info["sym"] = min(dy, dn) / max(dy, dn, 1e-9)
    if not void:
        if info["spread_ticks"] > spread_max:
            return "sel_spread", info
        if info["sym"] < sym_min:
            return "sel_sym", info
    return ("pass_activate" if void else "pass_join"), info


def score(row, info):
    """(payout_fraction, included, capital_usd) for a JOIN at both references, deployed sizes.
    included=False => R3 excludes this snapshot: it pays NOBODY, num and denom both skip it."""
    yl = [(float(p), float(s)) for p, s in row["yl"] if float(s) > 0]
    nl = [(float(p), float(s)) for p, s in row["nl"] if float(s) > 0]
    by, bn = info["best_y"], info["best_n"]
    ry, _, _ = REC.qualifying_walk(yl, row["target"])
    rn, _, _ = REC.qualifying_walk(nl, row["target"])
    if ry is None or rn is None:
        return 0.0, False, 0.0                    # R3: external book not two-sided -> excluded
    cy = Q._capped_join(by, bn)
    cn = Q._capped_join(bn, by)
    sy, _, _, _ = REC.side_share(yl, [(by, cy)], row["target"], row["df"], TICK)
    sn, _, _, _ = REC.side_share(nl, [(bn, cn)], row["target"], row["df"], TICK)
    return (sy + sn) / 2.0, True, by * cy + bn * cn


def main():
    snaps = [json.loads(l) for l in open(SAMPLES) if l.strip()]
    print(f"frozen dataset: {len(snaps)} snapshots  {snaps[0]['ts'][:19]} -> {snaps[-1]['ts'][:19]}"
          f"  ({sum(len(s['rows']) for s in snaps)} market-snapshots)")

    for sym_min, spread_max, label in ((0.25, 8, "LIVE  sym>=0.25"),
                                       (0.20, 8, "      sym>=0.20"),
                                       (0.15, 8, "      sym>=0.15"),
                                       (0.10, 8, "      sym>=0.10"),
                                       (0.00, 8, "      sym OFF   ")):
        # DENOMINATOR = every R3-INCLUDED snapshot of the period (per R4/R3), NOT only the
        # snapshots where our gate admitted us. Scoring only the admitted ones credits a market
        # we quote 2/27 of the time as if we rested all period — a ~13x overstatement. Snapshots
        # where the gate kept us out score ZERO for us but still count in the denominator.
        per_mkt = {}
        reasons = {}
        admitted_per_snap = []
        for s in snaps:
            adm = 0
            for row in s["rows"]:
                r, info = gate(row, sym_min, spread_max)
                reasons[r] = reasons.get(r, 0) + 1
                admitted = r.startswith("pass")
                adm += 1 if admitted else 0
                f, inc, cap = score(row, info) if info["best_y"] is not None \
                    and info["best_n"] is not None else (0.0, False, 0.0)
                if not inc:
                    continue                      # R3-excluded snapshot: pays nobody, skip both
                d = per_mkt.setdefault(row["t"], {"num": 0.0, "den": 0, "n_quoted": 0,
                                                  "cap": [], "pool": row["pool"],
                                                  "days": _days(row)})
                d["den"] += 1
                if admitted:
                    d["num"] += f
                    d["n_quoted"] += 1
                    d["cap"].append(cap)
            admitted_per_snap.append(adm)
        tot_day = 0.0
        cap_needed = 0.0
        lines = []
        for t, d in sorted(per_mkt.items()):
            if not d["n_quoted"]:
                continue
            frac = d["num"] / d["den"]                # R4 mean over ALL R3-included snapshots
            per_period = frac * d["pool"]
            per_day = per_period / d["days"]
            tot_day += per_day
            # capital is only committed while we are actually resting there
            c = (sum(d["cap"]) / len(d["cap"])) * (d["n_quoted"] / d["den"])
            cap_needed += c
            lines.append(f"        {t:<28} quoted={d['n_quoted']:2d}/{d['den']:2d}R3 "
                         f"frac={frac:.4f} ${per_period:6.2f}/period ${per_day:6.2f}/day  "
                         f"duty-wtd cap=${c:5.2f}")
        print(f"\n{label}  admitted/snapshot mean={sum(admitted_per_snap)/len(admitted_per_snap):.2f} "
              f"(min {min(admitted_per_snap)}, max {max(admitted_per_snap)})   "
              f"markets scored={len(lines)}  capital=${cap_needed:.2f}  MODELLED ${tot_day:.2f}/day")
        print("        reasons:", dict(sorted(reasons.items(), key=lambda kv: -kv[1])))
        for ln in lines:
            print(ln)


def _days(row):
    from datetime import datetime, timezone

    def pi(s):
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return max((pi(row["end"]) - pi(row["start"])).total_seconds() / 86400, 1 / 24)


if __name__ == "__main__":
    main()
