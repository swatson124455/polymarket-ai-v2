#!/usr/bin/env python3
"""End-to-end sharp-line backtest driver: snapshots -> closing -> join -> metrics.

Ties together the four sharp-line steps on data that lives on disk:

  1. reduce  PinnOdds snapshot JSONL  -> closing line per match  (closing_line)
  2. join    closing lines + free RESULTS -> who won              (results_join)
  3. (orientation is a live-CLOB backfill, applied only on the PM-edge path)
  4. metrics sharp-line hit-rate / Brier / line-movement          (sharp_eval)

Run it once the forward-collector's snapshot JSONL has been copied off the VPS
AND the free results cover the same window (see the coverage note it prints).

    python -m esports_v2.scripts.eval_sharp_line \
        --snapshots data/odds/pinnodds_snapshots.jsonl \
        --bulk data/esports_matches_bulk.jsonl \
        --cs2 data/cs2/pandascore_cs2.json \
        --de-vig simple            # or: shin  (OPEN operator decision)

Read-only: reduces/joins/reports. Writes nothing, deploys nothing. EB is halted.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from esports_v2.model.closing_line import iter_snapshots_jsonl, reduce_to_closing_lines
from esports_v2.model.results_join import (
    join_closing_lines_to_results,
    load_bulk_results,
    load_pandascore_cs2,
)
from esports_v2.model.sharp_eval import edge_backtest_from_joined, evaluate_sharp_line


def main() -> int:
    ap = argparse.ArgumentParser(description="EB sharp-line backtest driver")
    ap.add_argument("--snapshots", required=True, help="PinnOdds snapshot JSONL")
    ap.add_argument("--bulk", default="data/esports_matches_bulk.jsonl",
                    help="Oracle/GRID results JSONL")
    ap.add_argument("--cs2", default="data/cs2/pandascore_cs2.json",
                    help="PandaScore CS2 results JSON")
    ap.add_argument("--de-vig", choices=("simple", "shin"), default="simple",
                    help="De-vig method (OPEN operator decision; simple=fleet default)")
    ap.add_argument("--day-window", type=int, default=0,
                    help="+/- N days when matching a line to a result (default 0)")
    args = ap.parse_args()

    # ── Step 1: reduce snapshots -> closing lines ────────────────────────────
    snaps = list(iter_snapshots_jsonl(args.snapshots))
    closing = reduce_to_closing_lines(snaps)
    print(f"[1] snapshots read: {len(snaps)}   distinct closing lines: {len(closing)}")
    if not snaps:
        print(f"    !! no snapshots at {args.snapshots} — copy the VPS JSONL first.")
        return 1

    # ── Step 2: join to free results ─────────────────────────────────────────
    results = []
    if Path(args.bulk).exists():
        results += load_bulk_results(args.bulk)
    if Path(args.cs2).exists():
        results += load_pandascore_cs2(args.cs2)
    print(f"[2] free result rows loaded: {len(results)}")

    joined, stats = join_closing_lines_to_results(
        closing, results, day_window=args.day_window,
    )
    print(f"    joined+labeled: {stats.joined}   "
          f"no_result_match: {stats.no_result_match}   "
          f"ambiguous_multi_winner: {stats.ambiguous_multi_winner}   "
          f"winner_not_a_team: {stats.winner_not_a_team}")

    if stats.joined == 0:
        print("\n    Coverage is 0. Most likely cause: the forward-collected odds")
        print("    window does not overlap the free results' date range (results")
        print("    end well before collection began). The pipeline is correct; it")
        print("    needs results that cover the SAME window the odds were collected.")
        return 0

    # ── Step 4: sharp-line metrics ───────────────────────────────────────────
    report = evaluate_sharp_line(joined, method=args.de_vig)
    print("\n" + report.summary())

    # Sanity guard against impossible numbers (CLAUDE.md forbidden pattern #9).
    if report.favorite_hit_rate == 1.0 and report.n >= 30:
        print("\n  !! favorite hit-rate is exactly 1.0 on N>=30 — statistically")
        print("     implausible; check the join for label leakage before trusting.")

    # ── Step 4b: sharp-vs-Polymarket edge backtest (GAP B) ───────────────────
    # Runs only once joined records carry a captured PM price; otherwise reports
    # the gap WITHOUT any live-CLOB orientation calls (guard below).
    n_with_pm = sum(1 for r in joined if r.market_price is not None)
    print(f"\n[4b] joined records with captured PM price: {n_with_pm}")
    if n_with_pm == 0:
        print("     (no PM price in the joined window yet — the forward-collector's")
        print("     GAP-B capture must overlap the results window. Skipping the edge")
        print("     backtest; no CLOB orientation calls made.)")
        return 0
    edge = edge_backtest_from_joined(joined)  # resolves orientation via live CLOB
    print("\n" + edge.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
