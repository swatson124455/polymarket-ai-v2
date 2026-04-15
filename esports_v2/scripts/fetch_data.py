"""
Fetch external data for EsportsBot v2 backtest.

Downloads:
  1. CS2 match data from PandaScore (free tier, saved as GRID-compatible JSON)
  2. Pinnacle closing odds from OddsPapi (last 90 days)

Output files:
  data/cs2/pandascore_cs2.json     — CS2 matches for GridLoader
  data/odds/pinnacle_odds.json     — Odds lookup for CLV enrichment

Usage::
    # Fetch both
    python -m esports_v2.scripts.fetch_data --output-dir data

    # CS2 only
    python -m esports_v2.scripts.fetch_data --output-dir data --cs2-only

    # Odds only
    python -m esports_v2.scripts.fetch_data --output-dir data --odds-only

API keys read from environment:
    PANDASCORE_API_KEY   — pandascore.co free tier
    ODDSPAPI_API_KEY     — oddspapi.io free tier
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def fetch_cs2(output_dir: Path, days_back: int = 730) -> int:
    """Fetch CS2 matches from PandaScore."""
    api_key = os.environ.get("PANDASCORE_API_KEY")
    if not api_key:
        print("ERROR: PANDASCORE_API_KEY not set")
        return 0

    from esports_v2.data.pandascore_loader import PandaScoreLoader

    loader = PandaScoreLoader(api_key=api_key)
    matches = loader.fetch_cs2_matches(days_back=days_back)

    if not matches:
        print("No CS2 matches fetched from PandaScore.")
        return 0

    out_path = output_dir / "cs2" / "pandascore_cs2.json"
    loader.save_json(matches, out_path)
    print(f"CS2: {len(matches)} matches saved to {out_path}")
    return len(matches)


def fetch_odds(output_dir: Path, days_back: int = 90) -> int:
    """Fetch Pinnacle odds from OddsPapi."""
    api_key = os.environ.get("ODDSPAPI_API_KEY")
    if not api_key:
        print("ERROR: ODDSPAPI_API_KEY not set")
        return 0

    from esports_v2.data.odds_loader import OddsPapiLoader

    loader = OddsPapiLoader(api_key=api_key)
    odds = loader.fetch_all_odds(days_back=days_back)

    if not odds:
        print("No odds fetched from OddsPapi.")
        return 0

    out_path = output_dir / "odds" / "pinnacle_odds.json"
    loader.save_odds(odds, out_path)
    print(f"Odds: {len(odds)} match odds saved to {out_path}")
    return len(odds)


def main():
    parser = argparse.ArgumentParser(description="Fetch external data for EsportsBot v2")
    parser.add_argument("--output-dir", default="data", help="Base output directory")
    parser.add_argument("--cs2-only", action="store_true", help="Only fetch CS2 matches")
    parser.add_argument("--odds-only", action="store_true", help="Only fetch odds")
    parser.add_argument("--cs2-days", type=int, default=730, help="CS2 history depth (days)")
    parser.add_argument("--odds-days", type=int, default=90, help="Odds history depth (days, max ~90)")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    fetch_both = not args.cs2_only and not args.odds_only

    if fetch_both or args.cs2_only:
        fetch_cs2(output_dir, days_back=args.cs2_days)

    if fetch_both or args.odds_only:
        fetch_odds(output_dir, days_back=args.odds_days)

    print("\nDone. Next steps:")
    print("  1. Run backtest with CS2 data:")
    print("     python -m esports_v2.scripts.run_backtest \\")
    print("       --lol-csv data/lol/2024_*.csv data/lol/2025_*.csv data/lol/2026_*.csv \\")
    print("       --cs2-json data/cs2/pandascore_cs2.json \\")
    print("       --odds-json data/odds/pinnacle_odds.json \\")
    print("       --output-dir output/backtest_v2 --skip-shuffle")


if __name__ == "__main__":
    main()
