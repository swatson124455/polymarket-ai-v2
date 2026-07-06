#!/usr/bin/env python3
"""
WB Forecast-Skill / Dispersion Diagnostic — Phase-2 FIX-FINDER for WB_REBUILD_PLAN.md.

The calibration backtest showed the model is massively overconfident with ~0
apparent discrimination. Two very different root causes:
  (A) the forecast CENTER is wrong       -> hard problem (better inputs/models)
  (B) the forecast center is RIGHT but the spread is too narrow (underdispersion)
      -> EASY problem: widen the distribution to match real error and the exact-
         degree bucket probabilities become calibrated, discrimination returns.

This separates them. For a sample of the bot's stations it pulls the SAME ensemble
the bot uses (GFS + ECMWF IFS + AIFS via Open-Meteo) for the last few days, and
scores the ensemble MEAN high against ERA5 actual high:
  - MAE / bias / RMSE of the point forecast  -> is the center good?
  - corr(predicted, actual)                  -> does it track reality at all?
  - RMSE / mean_ensemble_std  = DISPERSION RATIO. >1 => underdispersed; this ratio
    IS roughly the variance-inflation factor the bucket model needs.

NOTE on as-of: Open-Meteo `past_days` is the archived forecast for recent days;
treat absolute MAE as an optimistic bound, but the dispersion RATIO is the headline
(spread-vs-error is robust to small as-of effects). The forward snapshot confirms.

READ-ONLY. Run on VPS (uses STATION_REGISTRY for the bot's exact coords).
Usage: PYTHONPATH=/opt/polymarket-ai-v2 python scripts/wb_forecast_skill.py [n_cities]
"""
import asyncio
import json
import math
import os
import ssl
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"
_CTX = ssl.create_default_context()


def _get(url):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": _UA}), timeout=40, context=_CTX).read())


def _ensemble_pred(lat, lon):
    """Return {date: (mean, std)} from the bot's 3-model ensemble (past 7d), celsius."""
    url = (f"https://ensemble-api.open-meteo.com/v1/ensemble?latitude={lat}&longitude={lon}"
           f"&models=gfs025,ecmwf_ifs025,ecmwf_aifs025&daily=temperature_2m_max"
           f"&past_days=7&forecast_days=1&temperature_unit=celsius&timezone=UTC")
    d = _get(url).get("daily", {})
    times = d.get("time", [])
    member_keys = [k for k in d if k.startswith("temperature_2m_max_member")]
    out = {}
    for i, dt in enumerate(times):
        vals = [d[k][i] for k in member_keys
                if i < len(d[k]) and isinstance(d[k][i], (int, float)) and math.isfinite(d[k][i])]
        if len(vals) >= 5:
            mean = sum(vals) / len(vals)
            var = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
            out[dt] = (mean, math.sqrt(var))
    return out


def _era5_truth(lat, lon, start, end):
    url = (f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}"
           f"&daily=temperature_2m_max&start_date={start}&end_date={end}"
           f"&temperature_unit=celsius&timezone=UTC")
    d = _get(url).get("daily", {})
    return dict(zip(d.get("time", []), d.get("temperature_2m_max", [])))


async def main():
    n_cities = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 25
    from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY

    stations = list(STATION_REGISTRY.values())[:n_cities]
    errs, spreads, preds, actuals = [], [], [], []
    per_city = []
    for st in stations:
        try:
            pred = _ensemble_pred(st.latitude, st.longitude)
            if not pred:
                continue
            dates = sorted(pred)
            truth = _era5_truth(st.latitude, st.longitude, dates[0], dates[-1])
        except Exception as e:
            print(f"  {st.city_name}: fetch error {str(e)[:60]}")
            continue
        c_err = []
        for dt in dates:
            if dt not in truth or truth[dt] is None:
                continue
            mean, std = pred[dt]
            e = mean - truth[dt]
            errs.append(e); spreads.append(std); preds.append(mean); actuals.append(truth[dt])
            c_err.append(e)
        if c_err:
            mae_c = sum(abs(x) for x in c_err) / len(c_err)
            per_city.append((st.city_name, len(c_err), sum(c_err)/len(c_err), mae_c))

    if not errs:
        print("no data")
        return
    n = len(errs)
    bias = sum(errs) / n
    mae = sum(abs(e) for e in errs) / n
    rmse = math.sqrt(sum(e * e for e in errs) / n)
    mean_spread = sum(spreads) / n
    # Pearson corr(pred, actual)
    mp = sum(preds) / n; ma = sum(actuals) / n
    cov = sum((p - mp) * (a - ma) for p, a in zip(preds, actuals)) / n
    sp = math.sqrt(sum((p - mp) ** 2 for p in preds) / n)
    sa = math.sqrt(sum((a - ma) ** 2 for a in actuals) / n)
    corr = cov / (sp * sa) if sp > 0 and sa > 0 else float("nan")
    ratio = rmse / mean_spread if mean_spread > 0 else float("nan")

    print("=" * 64)
    print(f"WB FORECAST-SKILL / DISPERSION — {len(per_city)} cities, {n} city-days (°C)")
    print("=" * 64)
    print(f"  POINT FORECAST (ensemble mean vs ERA5 actual high):")
    print(f"    MAE  = {mae:.2f} °C     bias = {bias:+.2f} °C     RMSE = {rmse:.2f} °C")
    print(f"    corr(pred, actual) = {corr:.3f}   (1.0 = perfect tracking)")
    print(f"  DISPERSION:")
    print(f"    mean ensemble spread (std) = {mean_spread:.2f} °C")
    print(f"    RMSE / spread RATIO        = {ratio:.2f}")
    print()
    print("  READ:")
    if corr >= 0.85 and mae <= 2.5:
        print(f"    * CENTER IS GOOD (corr {corr:.2f}, MAE {mae:.1f}°C). The forecast tracks reality.")
    elif corr >= 0.7:
        print(f"    * Center is decent (corr {corr:.2f}, MAE {mae:.1f}°C) — usable, improvable.")
    else:
        print(f"    * Center is WEAK (corr {corr:.2f}) — forecast itself needs work, not just spread.")
    if ratio > 1.3:
        print(f"    * UNDERDISPERSED by ~{ratio:.1f}x: real error ({rmse:.1f}°C) >> claimed spread "
              f"({mean_spread:.1f}°C).")
        print(f"      THIS is the overconfidence. Fix = widen bucket-model spread ~{ratio:.1f}x "
              f"(variance-inflation), which the empirical path currently skips.")
    elif ratio < 0.8:
        print(f"    * OVERDISPERSED ({ratio:.2f}) — spread too wide; different problem.")
    else:
        print(f"    * Dispersion roughly OK ({ratio:.2f}); overconfidence is elsewhere.")
    print()
    print("  worst-MAE cities:")
    for city, k, b, m in sorted(per_city, key=lambda x: -x[3])[:8]:
        print(f"    {city:<18} n={k:<3} bias={b:+.1f} MAE={m:.1f}°C")


if __name__ == "__main__":
    asyncio.run(main())
