#!/usr/bin/env python3
"""esports_silo — scheduled collection pass (item #9).

One pass = append-only odds collection + Polymarket snapshot collection. Thin glue over the
two collectors' `run_once`; scheduled by the systemd timer / cron in `esports_silo/deploy/`.
Collectors need network + DB, so this runs on the operator's box (use `--dry-run` to probe).

Prediction generation is deliberately NOT wired in here yet: per D2 the signal's calibrator is
unfitted and `SILO_ENTRY_HALT` is on, so a scheduled pass only COLLECTS (odds + snapshots).
Once the gate clears and the calibrator is fitted on forward data, add the prediction pass
(pipeline.process_market over live snapshots) behind an explicit `--predict` flag.
"""
from __future__ import annotations

import argparse
import asyncio


async def run_pass(dry_run: bool) -> None:
    # lazy imports: collectors pull aiohttp/asyncpg; keep this module importable without them
    try:
        from ..collectors import odds_collector, polymarket_collector
    except ImportError:
        from collectors import odds_collector, polymarket_collector  # type: ignore

    # odds first (the signal's input), then Polymarket snapshots (the comparison price).
    await odds_collector.run_once(dry_run)
    await polymarket_collector.run_once(dry_run)


def main() -> None:
    ap = argparse.ArgumentParser(description="esports_silo scheduled collection pass")
    ap.add_argument("--once", action="store_true", help="run a single pass then exit")
    ap.add_argument("--dry-run", action="store_true", help="probe only; no DB writes")
    args = ap.parse_args()
    if not args.once:
        raise SystemExit("only --once is implemented; schedule it via the systemd timer / cron "
                         "in esports_silo/deploy/")
    asyncio.run(run_pass(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
