#!/usr/bin/env python3
"""Dump every joined (closing line + result) record for LABEL AUDITING.

The sharp-line metrics are only as good as the ``home_won`` labels. This prints
each joined record on one line — teams in line order, the closing odds, the
no-vig fair prob of the home/odds_a side, the result winner NAME, and the
derived ``home_won`` bool — so a human can verify the labels against known
real-world results before trusting any aggregate metric (CLAUDE.md forbidden
pattern 8/9: never present unvalidated numbers).

    python -m esports_v2.scripts.dump_joined \
        --snapshots <snapshots.jsonl> --bulk <results.jsonl>

Read-only. Writes nothing.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from esports_v2.model.closing_line import iter_snapshots_jsonl, reduce_to_closing_lines
from esports_v2.model.results_join import join_closing_lines_to_results, load_bulk_results
from esports_v2.model.sharp_eval import no_vig_prob_a


def main() -> int:
    ap = argparse.ArgumentParser(description="Dump joined records for label audit")
    ap.add_argument("--snapshots", required=True)
    ap.add_argument("--bulk", required=True)
    ap.add_argument("--day-window", type=int, default=0)
    args = ap.parse_args()

    closing = reduce_to_closing_lines(iter_snapshots_jsonl(args.snapshots))
    results = load_bulk_results(Path(args.bulk))
    joined, stats = join_closing_lines_to_results(closing, results,
                                                  day_window=args.day_window)
    print(f"closing_lines={stats.n_lines} joined={stats.joined} "
          f"no_result={stats.no_result_match} ambiguous={stats.ambiguous_multi_winner}")
    for r in sorted(joined, key=lambda x: (x.day, x.match_key)):
        pa = no_vig_prob_a(r.odds_a, r.odds_b, "simple")
        pa_s = "n/a " if pa is None else f"{pa:.3f}"
        pm = "" if r.market_price is None else f"  PM_yes={r.market_price} yes_outcome={r.yes_outcome!r}"
        print(f"{r.day} [{r.game:8s}] {r.home!r} vs {r.away!r}  "
              f"odds=({r.odds_a},{r.odds_b}) fairP(home)={pa_s}  "
              f"WINNER={r.winner!r} home_won={r.home_won}  "
              f"result_id={r.result_match_id}{pm}")

    # Un-joined closing lines, so misses are visible too (name-form gaps etc).
    joined_keys = {r.match_key for r in joined}
    missed = [cl for k, cl in closing.items() if k not in joined_keys]
    if missed:
        print(f"\n--- {len(missed)} closing lines with NO result match ---")
        for cl in sorted(missed, key=lambda c: str(c.starts)):
            print(f"  {str(cl.starts)[:10]} {cl.home!r} vs {cl.away!r} ({cl.league_name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
