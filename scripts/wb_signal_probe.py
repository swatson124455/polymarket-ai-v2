#!/usr/bin/env python3
"""
WB Signal Probe — Phase-2 harness for WB_REBUILD_PLAN.md.

Runs WeatherBot's REAL forecasting engine (global ECMWF/GEFS/AIFS ensembles ->
skew-normal/empirical bucket probabilities) on today's LIVE Polymarket
daily-temperature markets, OUTSIDE the trading loop, and prints the model's
implied P(YES) next to the market's implied P(YES) for every bucket.

WHY: the bot's realized P&L was generated through a broken market-discovery
path (stale `temperature` tag-slug), so it never fairly tested the signal.
This isolates the signal: same engine, clean current markets, no trading.

READ-ONLY. No DB writes, no orders, no position/exposure state, no kill-switch.
Pure model-vs-market comparison. Outcomes are NOT known yet for forward markets;
this answers "does our forecast disagree with the market in a sane, exploitable
way?" — the precursor to a resolution-scored backtest.

Usage:
    python scripts/wb_signal_probe.py [N_GROUPS]      # default 12 city-date groups

Components reused verbatim from the live bot (bots/weather_bot.py):
    WeatherMarketMapper.group_markets   -> city/date/station + bucket bounds
    WeatherForecastClient.get_combined_forecast(station, date) -> ensemble members
    WeatherProbabilityEngine.empirical_bucket_probabilities / fit_distribution
"""
import asyncio
import json
import math
import ssl
import sys
import urllib.request
from datetime import datetime, timezone

# Same import path the live bot uses (bots/weather_bot.py:38,46,47)
from bots.weather.engine.base_engine.weather.market_mapper import WeatherMarketMapper
from bots.weather.engine.base_engine.weather.forecast_client import WeatherForecastClient
from bots.weather.engine.base_engine.weather.probability_engine import WeatherProbabilityEngine

_GAMMA = "https://gamma-api.polymarket.com/events"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
_CTX = ssl.create_default_context()


def _http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=30, context=_CTX) as r:
        return json.loads(r.read().decode())


def _fetch_forward_markets(slug="daily-temperature", pages=8):
    """Return adapted forward (endDate>now) market dicts ready for group_markets()."""
    now = datetime.now(timezone.utc)
    out = []
    for page in range(pages):
        try:
            data = _http_json(f"{_GAMMA}?active=true&closed=false&tag_slug={slug}"
                              f"&limit=100&offset={page * 100}")
        except Exception as e:
            print(f"  [fetch] page {page} error: {e}")
            break
        if not isinstance(data, list) or not data:
            break
        for ev in data:
            for m in ev.get("markets") or []:
                end = m.get("endDate") or ev.get("endDate") or ""
                try:
                    lead_h = (datetime.fromisoformat(end.replace("Z", "+00:00")) - now).total_seconds() / 3600
                except Exception:
                    continue
                if lead_h <= 0:
                    continue  # forward-only; skip resolved/stale
                out.append(_adapt(m))
        if len(data) < 100:
            break
    return out


def _adapt(market):
    """Gamma market dict -> the shape group_markets()/parse_market() expects."""
    yes_tok = no_tok = ""
    toks = market.get("clobTokenIds")
    if toks:
        arr = json.loads(toks) if isinstance(toks, str) else toks
        if isinstance(arr, list) and len(arr) >= 2:
            yes_tok, no_tok = str(arr[0]), str(arr[1])
    yes_price = 0.0
    prices = market.get("outcomePrices")
    if prices:
        arr = json.loads(prices) if isinstance(prices, str) else prices
        if isinstance(arr, list) and arr:
            try:
                yes_price = float(arr[0])
            except (TypeError, ValueError):
                yes_price = 0.0
    return {
        "id": str(market.get("id", "")),
        "question": market.get("question") or market.get("title") or "",
        "yes_token_id": yes_tok,
        "no_token_id": no_tok,
        "yes_price": yes_price,
    }


def _bucket_label(b):
    u = b.temp_unit
    if b.bucket_type == "at_or_below":
        return f"<={b.high_bound:.0f}{u}"
    if b.bucket_type == "at_or_higher":
        return f">={b.low_bound:.0f}{u}"
    if b.bucket_type == "range":
        return f"{b.low_bound:.0f}-{b.high_bound:.0f}{u}"
    return f"={b.low_bound:.0f}{u}"


async def main():
    # snapshot mode: `--snapshot PATH` captures ALL forward buckets to JSONL for
    # later resolution-scoring (Method 2, clean). Otherwise prints a sample table.
    snapshot_path = None
    if "--snapshot" in sys.argv:
        i = sys.argv.index("--snapshot")
        snapshot_path = sys.argv[i + 1] if i + 1 < len(sys.argv) else "wb_snapshot.jsonl"
    pos = [a for a in sys.argv[1:] if a.isdigit()]
    n_groups = int(pos[0]) if pos else (400 if snapshot_path else 12)
    snap_ts = datetime.now(timezone.utc).isoformat()
    snap_records = []

    print("Fetching live forward daily-temperature markets from Gamma...")
    raw = _fetch_forward_markets()
    print(f"  {len(raw)} forward bucket-markets fetched")

    mapper = WeatherMarketMapper()
    groups = [g for g in mapper.group_markets(raw) if g.buckets]
    groups.sort(key=lambda g: (g.city, g.target_date))
    print(f"  {len(groups)} city-date groups mapped to a station "
          f"(unmapped cities dropped by lookup_station)\n")
    sample = groups[:n_groups]

    fc = WeatherForecastClient()
    pe = WeatherProbabilityEngine()

    all_edges = []   # (abs_edge, edge, city, date, label, market_p, model_p, lead)
    covered = 0
    for g in sample:
        try:
            forecast = await fc.get_combined_forecast(g.station, g.target_date)
        except Exception as e:
            print(f"=== {g.city} {g.target_date} — forecast ERROR: {e}")
            continue
        if not forecast or not forecast.ensemble_members:
            print(f"=== {g.city} {g.target_date} — no ensemble returned")
            continue
        members = forecast.ensemble_members
        n_clean = sum(1 for m in members if isinstance(m, (int, float)) and math.isfinite(m))
        lead = forecast.lead_time_hours

        if n_clean >= 50:
            probs = pe.empirical_bucket_probabilities(
                members, g.buckets, g.station.station_id, lead)
            method = "empirical"
        else:
            try:
                loc, scale, shape = pe.fit_distribution(members, lead, g.station.station_id)
                probs = pe.bucket_probabilities(loc, scale, shape, g.buckets, lead)
                method = "parametric"
            except ValueError as e:
                print(f"=== {g.city} {g.target_date} — fit failed: {e}")
                continue
        if not probs:
            print(f"=== {g.city} {g.target_date} — degenerate (engine returned no probs)")
            continue

        covered += 1
        det = forecast.deterministic_high
        print(f"=== {g.city:<16} {g.target_date}  lead={lead:>4.0f}h  "
              f"n={n_clean:<3} det_high={det:.1f}{g.station.temp_unit} "
              f"spread={forecast.model_spread:.1f}  [{method}] ===")
        print(f"    {'bucket':<12} {'market_YES':>10} {'model_YES':>10} {'edge':>8}")
        for b in sorted(g.buckets, key=lambda x: (x.low_bound if x.low_bound is not None
                                                  else (x.high_bound or 0))):
            mp = probs.get(b.market_id)
            if mp is None:
                continue
            edge = mp - b.yes_price
            if snapshot_path:
                snap_records.append({
                    "snap_ts": snap_ts, "market_id": b.market_id,
                    "yes_token_id": b.token_id, "city": g.city,
                    "target_date": str(g.target_date), "bucket": _bucket_label(b),
                    "bucket_type": b.bucket_type, "low_bound": b.low_bound,
                    "high_bound": b.high_bound, "temp_unit": b.temp_unit,
                    "lead_h": round(lead, 1), "model_prob": round(mp, 4),
                    "market_price": round(b.yes_price, 4),
                })
            else:
                flag = "  <<" if (0.05 <= b.yes_price <= 0.95 and abs(edge) >= 0.05) else ""
                print(f"    {_bucket_label(b):<12} {b.yes_price:>10.3f} {mp:>10.3f} {edge:>+8.3f}{flag}")
            all_edges.append((abs(edge), edge, g.city, str(g.target_date),
                              _bucket_label(b), b.yes_price, mp, lead))
        if not snapshot_path:
            print()
        elif covered % 10 == 0:
            print(f"  ...{covered} groups, {len(snap_records)} buckets captured")

    if snapshot_path:
        with open(snapshot_path, "w") as fh:
            for r in snap_records:
                fh.write(json.dumps(r) + "\n")
        print(f"\nSNAPSHOT written: {snapshot_path}")
        print(f"  {len(snap_records)} buckets across {covered} groups, ts={snap_ts}")
        print("  score after these resolve with: scripts/wb_snapshot_score.py")

    # ---- summary: the actual question ----
    print("=" * 64)
    print("SUMMARY — model vs market disagreement")
    print("=" * 64)
    print(f"  groups sampled: {len(sample)}   with usable forecast+probs: {covered}")
    if not all_edges:
        print("  no comparable buckets — nothing to assess")
        return
    n = len(all_edges)
    mean_abs = sum(e[0] for e in all_edges) / n
    # meaningful = non-tail market price + material edge (the only tradeable kind)
    meaningful = [e for e in all_edges if 0.05 <= e[5] <= 0.95 and e[0] >= 0.05]
    print(f"  buckets compared: {n}   mean |edge|: {mean_abs:.3f}")
    print(f"  non-tail buckets (market 0.05-0.95): {sum(1 for e in all_edges if 0.05 <= e[5] <= 0.95)}")
    print(f"  meaningful disagreements (non-tail & |edge|>=0.05): {len(meaningful)}")
    print("\n  TOP 15 meaningful disagreements (|edge| desc):")
    print(f"    {'city':<16} {'date':<11} {'bucket':<11} {'mkt':>6} {'model':>6} {'edge':>7} {'lead':>5}")
    for ae, edge, city, date, lab, mktp, modp, lead in sorted(meaningful, reverse=True)[:15]:
        print(f"    {city:<16} {date:<11} {lab:<11} {mktp:>6.3f} {modp:>6.3f} {edge:>+7.3f} {lead:>4.0f}h")
    if not meaningful:
        print("    (none — model and market agree everywhere outside the tails)")


if __name__ == "__main__":
    asyncio.run(main())
