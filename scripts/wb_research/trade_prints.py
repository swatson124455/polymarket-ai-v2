#!/usr/bin/env python3
"""S230 trade-print logger (READ-ONLY). Every tick, for each active US
'highest temperature' family whose station's local time is 10:00-23:59:
fetch recent public trade prints (data-api) for every bucket market and
append NEW ones to JSONL. Companion to shadow_book.py: books say where you
COULD rest a bid; prints say whether resting orders actually get filled —
the maker-leg evidence for the leader-following review (WEATHER_STATUS 2c).

Dedup: a state file keeps max trade timestamp per market; each tick only
appends trades strictly newer than that cursor (same-second boundary fills
are almost always in the same fetch; residual risk accepted). Analysis
should still dedup on (transactionHash, asset, timestamp, price, size).
Fields kept per print: market_id, side (TAKER side per data-api), outcome,
price, size, timestamp, asset, transactionHash, fetched_at.
"""
import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, "/opt/polymarket-ai-v2-weather")
from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY  # noqa

CITY = {s.city_name.lower(): s for s in STATION_REGISTRY.values()}
OUT = ("/home/ubuntu/wb_research/trade_prints_%s.jsonl"
       % datetime.now(timezone.utc).strftime("%Y%m%d"))
STATE = "/home/ubuntu/wb_research/.trade_prints_state.json"
KEEP = ("side", "outcome", "price", "size", "timestamp", "asset",
        "transactionHash")


def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "wb-research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def psql(q):
    return subprocess.run(
        ["sudo", "-u", "postgres", "psql", "polymarket", "-At", "-c", q],
        capture_output=True, text=True, timeout=60).stdout.strip()


rows = psql("""SELECT json_agg(row_to_json(t)) FROM (
  SELECT id, question
  FROM markets
  WHERE category='weather' AND resolved IS NOT TRUE
    AND question LIKE 'Will the highest temperature%%'
    AND end_date_iso BETWEEN NOW() - INTERVAL '1 day' AND NOW() + INTERVAL '2 days') t""")
markets = json.loads(rows) if rows and rows != "null" else []

active = []
for m in markets:
    q = re.match(r"Will the highest temperature in (.+?) be (.+?) on (\w+) (\d+)\?",
                 m["question"])
    if not q:
        continue
    city = q.group(1).lower()
    st = CITY.get(city)
    if not st or st.temp_unit != "F" or not st.station_id.startswith("K"):
        continue
    now_loc = datetime.now(ZoneInfo(st.timezone))
    if now_loc.hour < 10:
        continue
    if f"{q.group(3)} {int(q.group(4))}" != f"{now_loc.strftime('%B')} {now_loc.day}":
        continue
    active.append(m["id"])

if not active:
    sys.exit(0)

try:
    state = json.load(open(STATE, encoding="utf-8"))
except Exception:
    state = {}

fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
new_count = 0
with open(OUT, "a") as out:
    for mid in active:
        try:
            raw = fetch("https://data-api.polymarket.com/trades?market=%s&limit=200"
                        % mid)
            trades = json.loads(raw)
        except Exception:
            time.sleep(0.05)
            continue
        last = state.get(mid, 0)
        mx = last
        for t in trades if isinstance(trades, list) else []:
            ts = t.get("timestamp") or 0
            if ts <= last:
                continue
            rec = {k: t.get(k) for k in KEEP}
            rec["market_id"] = mid
            rec["fetched_at"] = fetched_at
            out.write(json.dumps(rec) + "\n")
            new_count += 1
            mx = max(mx, ts)
        if mx > last:
            state[mid] = mx
        time.sleep(0.05)

# prune state entries for markets no longer active (bounded file)
state = {k: v for k, v in state.items()
         if k in set(active) or v > time.time() - 3 * 86400}
json.dump(state, open(STATE, "w"))
print("%s markets=%d new_prints=%d" % (fetched_at, len(active), new_count))
