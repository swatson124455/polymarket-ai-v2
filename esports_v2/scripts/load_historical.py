"""
Load historical match data and process through Rating Trinity.

Usage:
    python -m esports_v2.scripts.load_historical \
        --lol-csv data/2024_LoL.csv data/2025_LoL.csv \
        --cs2-json data/grid_cs2.json \
        --cs2-csv data/hltv_results.csv \
        --output-dir output/ratings

This script:
  1. Loads LoL data from Oracle's Elixir CSVs
  2. Loads CS2 data from GRID JSON and/or HLTV CSVs
  3. Merges and sorts all matches chronologically
  4. Processes through Trinity (Elo + Glicko-2 + OpenSkill)
  5. Outputs rating snapshots and feature vectors
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import List

from esports_v2.data.normalizer import RawMatch, raw_to_match_result
from esports_v2.data.oracle_loader import OracleElixirLoader
from esports_v2.data.grid_loader import GridLoader, HLTVResultsLoader
from esports_v2.ratings.trinity import Trinity, TrinityPrediction

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def load_all_matches(
    lol_csvs: List[str],
    cs2_jsons: List[str],
    cs2_csvs: List[str],
) -> List[RawMatch]:
    """Load matches from all sources, return sorted by date."""
    all_matches: List[RawMatch] = []

    # LoL from Oracle's Elixir
    if lol_csvs:
        loader = OracleElixirLoader()
        for csv_path in lol_csvs:
            matches = loader.load_csv(csv_path)
            all_matches.extend(matches)
            logger.info(f"LoL: {len(matches)} matches from {csv_path}")

    # CS2 from GRID
    if cs2_jsons:
        loader = GridLoader()
        for json_path in cs2_jsons:
            matches = loader.load_json(json_path)
            all_matches.extend(matches)
            logger.info(f"CS2 (GRID): {len(matches)} matches from {json_path}")

    # CS2 from HLTV
    if cs2_csvs:
        loader = HLTVResultsLoader()
        for csv_path in cs2_csvs:
            matches = loader.load_csv(csv_path)
            all_matches.extend(matches)
            logger.info(f"CS2 (HLTV): {len(matches)} matches from {csv_path}")

    # Sort by date
    all_matches.sort(key=lambda m: m.match_date or "")
    logger.info(f"Total: {len(all_matches)} matches loaded")
    return all_matches


def run_trinity(matches: List[RawMatch], trinity: Trinity) -> List[dict]:
    """
    Process all matches through Trinity, return feature records.

    Each record contains the pre-match prediction features + match outcome.
    """
    records = []
    for raw in matches:
        mr = raw_to_match_result(raw)
        prediction = trinity.process_match(mr)
        record = {
            "match_id": raw.match_id,
            "game": raw.game,
            "team_a": raw.team_a,
            "team_b": raw.team_b,
            "winner": raw.winner,
            "match_date": raw.match_date,
            "event_name": raw.event_name,
            "event_tier": raw.event_tier,
            "is_lan": raw.is_lan,
            **prediction.to_feature_dict(),
            "high_agreement": prediction.high_agreement,
            "should_abstain": prediction.should_abstain,
        }
        records.append(record)

    return records


def print_summary(trinity: Trinity, records: List[dict]) -> None:
    """Print summary statistics."""
    for game in sorted(trinity.get_games()):
        game_records = [r for r in records if r["game"] == game]
        n = len(game_records)
        if n == 0:
            continue

        # Prediction accuracy (using pre-match trinity_mean)
        correct = 0
        for r in game_records:
            pred_a_wins = r["trinity_mean"] > 0.5
            actual_a_wins = r["winner"] == r["team_a"]
            if pred_a_wins == actual_a_wins:
                correct += 1
        accuracy = correct / n if n > 0 else 0

        # Spread distribution
        spreads = [r["trinity_spread"] for r in game_records]
        avg_spread = sum(spreads) / n
        high_agree = sum(1 for s in spreads if s < 0.05) / n
        abstain = sum(1 for s in spreads if s > 0.15) / n

        # Top-rated teams
        elo_ratings = trinity.get_elo_ratings(game)
        top_teams = sorted(elo_ratings.items(), key=lambda x: x[1].rating, reverse=True)[:10]

        print(f"\n{'='*60}")
        print(f" {game.upper()} — {n} matches processed")
        print(f"{'='*60}")
        print(f"  Trinity accuracy: {accuracy:.1%} (pre-match prediction)")
        print(f"  Avg spread:       {avg_spread:.4f}")
        print(f"  High agreement:   {high_agree:.1%} (spread < 0.05)")
        print(f"  Abstain rate:     {abstain:.1%} (spread > 0.15)")
        print(f"\n  Top 10 Elo ratings:")
        for team, rating in top_teams:
            print(f"    {team:30s}  {rating.rating:.0f}  ({rating.matches_played} matches)")


def main():
    parser = argparse.ArgumentParser(description="Load historical esports data through Rating Trinity")
    parser.add_argument("--lol-csv", nargs="*", default=[], help="Oracle's Elixir LoL CSV files")
    parser.add_argument("--cs2-json", nargs="*", default=[], help="GRID CS2 JSON files")
    parser.add_argument("--cs2-csv", nargs="*", default=[], help="HLTV CS2 CSV files")
    parser.add_argument("--output-dir", default=None, help="Output directory for ratings/features")
    parser.add_argument("--elo-k", type=float, default=32.0, help="Elo K-factor")
    parser.add_argument("--glicko-tau", type=float, default=0.5, help="Glicko-2 tau")
    args = parser.parse_args()

    if not args.lol_csv and not args.cs2_json and not args.cs2_csv:
        print("No data files specified. Use --lol-csv, --cs2-json, or --cs2-csv.")
        sys.exit(1)

    # Load all matches
    all_matches = load_all_matches(args.lol_csv, args.cs2_json, args.cs2_csv)

    if not all_matches:
        print("No matches loaded.")
        sys.exit(1)

    # Process through Trinity
    trinity = Trinity(elo_k=args.elo_k, glicko_tau=args.glicko_tau)
    records = run_trinity(all_matches, trinity)

    # Print summary
    print_summary(trinity, records)

    # Save output if requested
    if args.output_dir:
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        # Save features
        features_path = out_dir / "trinity_features.json"
        with open(features_path, "w") as f:
            json.dump(records, f, indent=2, default=str)
        print(f"\nFeatures saved to {features_path} ({len(records)} records)")

        # Save final ratings per game
        for game in trinity.get_games():
            elo = {k: v.to_dict() for k, v in trinity.get_elo_ratings(game).items()}
            glicko = {k: v.to_dict() for k, v in trinity.get_glicko_ratings(game).items()}
            openskill = {k: v.to_dict() for k, v in trinity.get_openskill_ratings(game).items()}
            ratings = {"elo": elo, "glicko2": glicko, "openskill": openskill}
            ratings_path = out_dir / f"ratings_{game}.json"
            with open(ratings_path, "w") as f:
                json.dump(ratings, f, indent=2)
            print(f"Ratings saved to {ratings_path}")


if __name__ == "__main__":
    main()
