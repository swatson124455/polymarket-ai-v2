#!/usr/bin/env python3
"""Hygiene audit of the forward-collected snapshot JSONL (read-only).

The snapshot log is the pipeline's primary data store — every backtest number
descends from it. This verifies, line by line, that what's stored is clean:

  HARD failures (exit 1 — data that could corrupt a backtest):
    - unparseable JSON lines
    - rows missing core fields (captured_at/match_key/home/away/starts/odds)
    - invalid odds (not > 1.0) or market_price outside (0,1) when non-null
    - PM-field incoherence (condition_id present but token/outcome missing)
    - stored yes_outcome matching NEITHER or BOTH of home/away (orientation)
    - one condition_id mapping to different token/outcome across rows
    - one match_key mapping to different TEAMS or a different DAY across rows
      (same-teams same-day start-TIME drift = schedule delay -> a NOTE)
    - match_key not reproducible from (home, away, starts)
    - exact duplicate (captured_at, match_key) rows (double-append)

  NOTES (reported, not failures):
    - rows predating GAP-B (no PM keys at all — expected history)
    - captured_at going backwards (append-order oddity; harmless to the
      reducer, which sorts, but worth eyes)
    - null-PM rows (unmatched odds — expected, correct-or-absent)

Usage:  python -m esports_v2.scripts.verify_snapshot_hygiene --snapshots <jsonl>
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, List

from esports_v2.model.closing_line import parse_ts
from esports_v2.model.team_match import same_team

CORE = ("captured_at", "match_key", "home", "away", "starts",
        "odds_a", "odds_b")
PM_KEYS = ("condition_id", "yes_token_id", "yes_outcome", "market_price")


def _mkey(home: str, away: str, starts) -> str:
    """Reproduce the collector's match_key (sorted lowercased pair + day)."""
    a, b = str(home or "").strip().lower(), str(away or "").strip().lower()
    if a > b:
        a, b = b, a
    return f"{a}||{b}||{str(starts or '')[:10]}"


def audit(lines) -> Dict:
    r: Dict = {k: 0 for k in (
        "n_lines", "json_bad", "missing_core", "bad_odds", "bad_price",
        "pm_incoherent", "orient_bad", "key_mismatch", "dup_rows",
        "cid_conflicts", "match_conflicts", "pre_gapb", "pm_null_rows",
        "pm_priced_rows", "ts_backwards", "starts_drift")}
    r["examples"] = []
    seen_rows = set()
    cid_map: Dict[str, tuple] = {}
    match_map: Dict[str, tuple] = {}
    prev_ts = None

    def ex(kind: str, detail: str):
        if len(r["examples"]) < 12:
            r["examples"].append(f"{kind}: {detail}")

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        r["n_lines"] += 1
        try:
            d = json.loads(raw)
        except (ValueError, TypeError):
            r["json_bad"] += 1
            ex("json_bad", raw[:80])
            continue

        if any(d.get(k) in (None, "") for k in CORE):
            r["missing_core"] += 1
            ex("missing_core", str({k: d.get(k) for k in CORE})[:100])
            continue

        try:
            oa, ob = float(d["odds_a"]), float(d["odds_b"])
            odds_ok = oa > 1.0 and ob > 1.0
        except (ValueError, TypeError):
            odds_ok = False
        if not odds_ok:
            r["bad_odds"] += 1
            ex("bad_odds", f"{d['match_key']} {d.get('odds_a')}/{d.get('odds_b')}")

        mp = d.get("market_price")
        if mp is not None:
            try:
                if not (0.0 < float(mp) < 1.0):
                    raise ValueError
            except (ValueError, TypeError):
                r["bad_price"] += 1
                ex("bad_price", f"{d['match_key']} price={mp!r}")

        if "condition_id" not in d:
            r["pre_gapb"] += 1
        else:
            cid = d.get("condition_id")
            if cid is None:
                r["pm_null_rows"] += 1
            else:
                if not d.get("yes_token_id") or not d.get("yes_outcome"):
                    r["pm_incoherent"] += 1
                    ex("pm_incoherent", f"{d['match_key']} cid set, token/outcome missing")
                else:
                    if mp is not None:
                        r["pm_priced_rows"] += 1
                    yo = d["yes_outcome"]
                    is_h = same_team(yo, d["home"], None)
                    is_a = same_team(yo, d["away"], None)
                    if is_h == is_a:
                        r["orient_bad"] += 1
                        ex("orient_bad", f"{d['match_key']} yes={yo!r}")
                    prev = cid_map.get(cid)
                    cur = (d["yes_token_id"], d["yes_outcome"])
                    if prev is None:
                        cid_map[cid] = cur
                    elif prev != cur:
                        r["cid_conflicts"] += 1
                        ex("cid_conflict", f"{cid[:14]} {prev} != {cur}")

        if _mkey(d["home"], d["away"], d["starts"]) != d["match_key"]:
            r["key_mismatch"] += 1
            ex("key_mismatch", d["match_key"])

        ident = (d["match_key"], d["captured_at"])
        if ident in seen_rows:
            r["dup_rows"] += 1
            ex("dup_row", str(ident))
        seen_rows.add(ident)

        prev_m = match_map.get(d["match_key"])
        cur_m = (d["home"], d["away"], str(d["starts"]))
        if prev_m is None:
            match_map[d["match_key"]] = cur_m
        elif prev_m != cur_m:
            # Same teams + same day with a different start TIME is schedule
            # drift (matches get delayed; measured live 2026-07-12) — a NOTE,
            # handled by the reducer's latest-seen-starts rule. A change of
            # TEAMS or DAY under one key is real identity corruption — HARD.
            same_teams = {prev_m[0], prev_m[1]} == {cur_m[0], cur_m[1]}
            same_day = str(prev_m[2])[:10] == str(cur_m[2])[:10]
            if same_teams and same_day:
                r["starts_drift"] += 1
            else:
                r["match_conflicts"] += 1
                ex("match_conflict", f"{d['match_key']} {prev_m} != {cur_m}")

        ts = parse_ts(d["captured_at"])
        if ts is not None and prev_ts is not None and ts < prev_ts:
            r["ts_backwards"] += 1
        if ts is not None:
            prev_ts = ts

    r["distinct_matches"] = len(match_map)
    r["distinct_pm_markets"] = len(cid_map)
    return r


HARD = ("json_bad", "missing_core", "bad_odds", "bad_price", "pm_incoherent",
        "orient_bad", "key_mismatch", "dup_rows", "cid_conflicts",
        "match_conflicts")


def render(r: Dict) -> str:
    out = [f"Snapshot hygiene audit — {r['n_lines']} lines, "
           f"{r['distinct_matches']} distinct matches, "
           f"{r['distinct_pm_markets']} distinct PM markets, "
           f"{r['pm_priced_rows']} PM-priced rows"]
    bad = {k: r[k] for k in HARD if r[k]}
    out.append("HARD failures: " + (str(bad) if bad else "NONE — clean"))
    out.append(f"Notes: pre-GAP-B rows (no PM keys, expected history)={r['pre_gapb']}  "
               f"unmatched-PM rows (nulls, expected)={r['pm_null_rows']}  "
               f"start-time drift rows (delays, reducer-handled)={r['starts_drift']}  "
               f"timestamp-backwards={r['ts_backwards']}")
    for e in r["examples"]:
        out.append(f"  ex {e}")
    out.append("VERDICT: " + ("FAIL" if bad else "PASS"))
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Snapshot JSONL hygiene audit")
    ap.add_argument("--snapshots", required=True)
    args = ap.parse_args()
    with open(args.snapshots, "r", encoding="utf-8", errors="strict") as f:
        r = audit(f)
    print(render(r))
    return 1 if any(r[k] for k in HARD) else 0


if __name__ == "__main__":
    sys.exit(main())
