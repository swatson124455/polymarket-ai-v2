#!/usr/bin/env python3
"""Backfill weather_climatology table from 10-year ERA5 reanalysis.

Fetches daily max temperatures (2016-present) for each station in the registry,
computes recency-weighted (mean, std) per day-of-year, and upserts into
weather_climatology for SAMOS EMOS normalization.

Usage:
    PYTHONPATH=/opt/polymarket-ai-v2 python scripts/backfill_climatology.py [--years 10] [--decay 0.85]

One-time operation (~30s for 40 stations). Re-run monthly to pick up new stations.
"""

import asyncio
import os
import sys
from collections import defaultdict
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Recency weighting: years 0-2 ago = full weight, then decay per year after that
DEFAULT_DECAY = 0.85
DEFAULT_YEARS = 10
FULL_WEIGHT_YEARS = 3  # most recent N years get weight 1.0


def compute_weighted_climatology(
    records: list,
    current_year: int,
    decay: float = DEFAULT_DECAY,
) -> dict:
    """Compute recency-weighted (mean, std) per day-of-year.

    Args:
        records: list of (date_iso, temp, year) tuples
        current_year: reference year for decay calculation
        decay: decay rate for years beyond FULL_WEIGHT_YEARS ago

    Returns:
        {day_of_year: (clim_mean, clim_std, n_years)}
    """
    # Group by DOY
    doy_data = defaultdict(list)  # doy → [(temp, weight, year)]
    for date_str, temp, year in records:
        try:
            d = date.fromisoformat(date_str)
            doy = d.timetuple().tm_yday
            age = current_year - year
            if age < FULL_WEIGHT_YEARS:
                w = 1.0
            else:
                w = decay ** (age - FULL_WEIGHT_YEARS + 1)
            doy_data[doy].append((temp, w, year))
        except (ValueError, TypeError):
            continue

    result = {}
    for doy, entries in doy_data.items():
        if len(entries) < 3:
            continue

        years_present = len(set(yr for _, _, yr in entries))
        temps = [t for t, _, _ in entries]
        weights = [w for _, w, _ in entries]
        total_w = sum(weights)

        if total_w < 0.01:
            continue

        # Weighted mean
        w_mean = sum(t * w for t, w in zip(temps, weights)) / total_w

        # Weighted std (with floor)
        w_var = sum(w * (t - w_mean) ** 2 for t, w in zip(temps, weights)) / total_w
        w_std = max(w_var ** 0.5, 1.0)  # Floor at 1.0 to prevent overconfident normalization

        result[doy] = (round(w_mean, 2), round(w_std, 2), years_present)

    return result


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Backfill weather climatology from ERA5")
    parser.add_argument("--years", type=int, default=DEFAULT_YEARS, help="Years of history (default 10)")
    parser.add_argument("--decay", type=float, default=DEFAULT_DECAY, help="Decay rate for old years (default 0.85)")
    parser.add_argument("--station", type=str, default=None, help="Single station ID to backfill (default: all)")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()

    from base_engine.data.database import Database
    from base_engine.weather.forecast_client import WeatherForecastClient
    from base_engine.weather.station_registry import STATION_REGISTRY
    from config.settings import settings

    db = Database()
    await db.init()
    client = WeatherForecastClient()

    current_year = date.today().year

    stations = STATION_REGISTRY
    if args.station:
        if args.station not in stations:
            print(f"Station {args.station} not found in registry")
            return
        stations = {args.station: stations[args.station]}

    print(f"Backfilling climatology for {len(stations)} stations ({args.years} years, decay={args.decay})")
    print(f"Weight schedule: years 0-{FULL_WEIGHT_YEARS - 1} ago = 1.0, then {args.decay}^(age-{FULL_WEIGHT_YEARS - 1})")

    total_inserted = 0
    failed = []

    try:
        for sid, station in sorted(stations.items()):
            print(f"  {sid} ({station.city_name})...", end=" ", flush=True)

            records = await client.fetch_climate_archive(
                latitude=station.latitude,
                longitude=station.longitude,
                temp_unit=station.temp_unit,
                years=args.years,
            )

            if not records:
                print("FAILED (no data)")
                failed.append(sid)
                continue

            clim = compute_weighted_climatology(records, current_year, args.decay)

            if not clim:
                print(f"FAILED (insufficient data: {len(records)} records)")
                failed.append(sid)
                continue

            # Upsert into weather_climatology
            inserted = 0
            async with db.get_session() as session:
                from sqlalchemy import text
                for doy, (c_mean, c_std, n_yr) in clim.items():
                    await session.execute(text("""
                        INSERT INTO weather_climatology (station_id, day_of_year, clim_mean, clim_std, n_years, updated_at)
                        VALUES (:sid, :doy, :mean, :std, :n, NOW())
                        ON CONFLICT (station_id, day_of_year) DO UPDATE SET
                            clim_mean = EXCLUDED.clim_mean,
                            clim_std = EXCLUDED.clim_std,
                            n_years = EXCLUDED.n_years,
                            updated_at = NOW()
                    """), {"sid": sid, "doy": doy, "mean": c_mean, "std": c_std, "n": n_yr})
                    inserted += 1
                await session.commit()

            total_inserted += inserted
            print(f"OK ({inserted} DOYs, {len(records)} daily records, "
                  f"mean_std={sum(s for _, s, _ in clim.values()) / len(clim):.1f})")

        print(f"\nDone: {total_inserted} rows upserted across {len(stations) - len(failed)} stations")
        if failed:
            print(f"Failed: {failed}")

    finally:
        await client.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
