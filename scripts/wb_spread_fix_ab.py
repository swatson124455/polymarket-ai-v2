#!/usr/bin/env python3
"""
WB Spread-Fix A/B — Phase-2 keystone prototype for WB_REBUILD_PLAN.md.

Proves the calibration fix WITHOUT touching the deployed engine. For today's live
markets, computes each bucket's P(YES) three ways and shows the over-sharp single-
degree spikes collapse to physically-honest levels:

  CURRENT   : empirical_bucket_probabilities — raw ensemble spread, NO inflation
              (what the bot uses at n>=50 -> always). This is the overconfidence bug.
  PARAM-VIF : fit_distribution + bucket_probabilities — the engine's EXISTING
              variance-inflation (S154), currently bypassed by the empirical path.
  FLOORED   : honest spread = max(ensemble_std*VIF, FLOOR) where FLOOR is the real
              forecast error at this lead. The model can never claim more precision
              than it has delivered. (FLOOR is a CLI arg pending snapshot-measured RMSE.)

Headline = mean PEAK bucket probability per method. A correct ~1C-error forecast
on 1-degree buckets should peak around 0.30-0.40, not 0.50-0.98.

READ-ONLY. Run on VPS. Usage:
    PYTHONPATH=/opt/polymarket-ai-v2 python scripts/wb_spread_fix_ab.py [n_groups] [floor_C]
"""
import asyncio
import json
import math
import os
import ssl
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.weather.engine.base_engine.weather.market_mapper import WeatherMarketMapper
from bots.weather.engine.base_engine.weather.forecast_client import WeatherForecastClient
from bots.weather.engine.base_engine.weather.probability_engine import WeatherProbabilityEngine

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"
_CTX = ssl.create_default_context()


def _http(url):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": _UA}), timeout=30, context=_CTX).read())


def _adapt(m):
    yes = no = ""
    toks = m.get("clobTokenIds")
    if toks:
        a = json.loads(toks) if isinstance(toks, str) else toks
        if isinstance(a, list) and len(a) >= 2:
            yes, no = str(a[0]), str(a[1])
    yp = 0.0
    pr = m.get("outcomePrices")
    if pr:
        a = json.loads(pr) if isinstance(pr, str) else pr
        if a:
            try: yp = float(a[0])
            except (TypeError, ValueError): yp = 0.0
    return {"id": str(m.get("id", "")), "question": m.get("question") or "",
            "yes_token_id": yes, "no_token_id": no, "yes_price": yp}


def _fetch_forward(pages=6):
    now = datetime.now(timezone.utc); out = []
    for p in range(pages):
        try:
            d = _http(f"https://gamma-api.polymarket.com/events?active=true&closed=false"
                      f"&tag_slug=daily-temperature&limit=100&offset={p*100}")
        except Exception:
            break
        if not isinstance(d, list) or not d:
            break
        for ev in d:
            for m in ev.get("markets") or []:
                end = m.get("endDate") or ev.get("endDate") or ""
                try:
                    if (datetime.fromisoformat(end.replace("Z", "+00:00")) - now).total_seconds() > 0:
                        out.append(_adapt(m))
                except Exception:
                    pass
        if len(d) < 100:
            break
    return out


def _peak_and_spikes(probs):
    if not probs:
        return None, 0
    vals = list(probs.values())
    return max(vals), sum(1 for v in vals if v >= 0.50)


async def main():
    n_groups = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 15
    floor_c = float(sys.argv[2]) if len(sys.argv) > 2 else 1.4

    raw = _fetch_forward()
    groups = [g for g in WeatherMarketMapper().group_markets(raw) if g.buckets]
    groups.sort(key=lambda g: (g.city, g.target_date))
    sample = groups[:n_groups]

    fc = WeatherForecastClient(); pe = WeatherProbabilityEngine()
    vif = getattr(pe, "_variance_inflation_factor", None)
    print(f"engine _variance_inflation_factor = {vif}   FLOOR = {floor_c} C "
          f"(= {floor_c*1.8:.1f} F for US)\n")
    print(f"  {'city':<15} {'lead':>4} {'memσ':>5} | "
          f"{'CUR pk':>7} {'VIF pk':>7} {'FLR pk':>7} | spikes>=0.5 C/V/F")

    agg = {"cur": [], "vif": [], "flr": []}
    spk = {"cur": 0, "vif": 0, "flr": 0}
    for g in sample:
        try:
            f = await fc.get_combined_forecast(g.station, g.target_date)
        except Exception:
            continue
        if not f or not f.ensemble_members:
            continue
        mem = [x for x in f.ensemble_members if isinstance(x, (int, float)) and math.isfinite(x)]
        if len(mem) < 50:
            continue
        lead = f.lead_time_hours
        mean = sum(mem) / len(mem)
        std = math.sqrt(sum((x - mean) ** 2 for x in mem) / (len(mem) - 1))
        unit = (g.temp_unit or g.station.temp_unit or "C").upper()
        floor = floor_c * (1.8 if unit == "F" else 1.0)

        cur = pe.empirical_bucket_probabilities(mem, g.buckets, g.station.station_id, lead)
        try:
            loc, sc, sh = pe.fit_distribution(mem, lead, g.station.station_id)
            vifp = pe.bucket_probabilities(loc, sc, sh, g.buckets, lead)
        except ValueError:
            vifp = {}
        flr_scale = max(std * (vif or 1.0), floor)
        flr = pe.bucket_probabilities(mean, flr_scale, 0.0, g.buckets, lead)

        pc, sc_c = _peak_and_spikes(cur)
        pv, sc_v = _peak_and_spikes(vifp)
        pf, sc_f = _peak_and_spikes(flr)
        if pc is not None: agg["cur"].append(pc); spk["cur"] += sc_c
        if pv is not None: agg["vif"].append(pv); spk["vif"] += sc_v
        if pf is not None: agg["flr"].append(pf); spk["flr"] += sc_f
        print(f"  {g.city:<15} {lead:>4.0f} {std:>5.2f} | "
              f"{(pc or 0):>7.3f} {(pv or 0):>7.3f} {(pf or 0):>7.3f} | "
              f"{sc_c}/{sc_v}/{sc_f}")

    def _m(k):
        v = agg[k]
        return sum(v) / len(v) if v else float("nan")
    print("\n" + "=" * 60)
    print("SUMMARY — mean PEAK bucket probability (lower = less overconfident)")
    print("=" * 60)
    print(f"  CURRENT  (no inflation, the bug): {_m('cur'):.3f}   spikes>=0.5: {spk['cur']}")
    print(f"  PARAM-VIF (engine fix, bypassed): {_m('vif'):.3f}   spikes>=0.5: {spk['vif']}")
    print(f"  FLOORED  (honest spread, floor={floor_c}C): {_m('flr'):.3f}   spikes>=0.5: {spk['flr']}")
    print("\n  A correct ~1C-error forecast on 1-degree buckets should peak ~0.30-0.40.")
    print("  If FLOORED collapses the spikes toward that, the fix is confirmed at the")
    print("  distribution level; the forward snapshot then confirms it against outcomes.")


if __name__ == "__main__":
    asyncio.run(main())
