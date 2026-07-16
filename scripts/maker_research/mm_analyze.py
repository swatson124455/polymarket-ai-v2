"""Aggregate the sweep: sector spreads/depth/rewards/flow. 2026-07-14."""
import json, re, urllib.request
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from datetime import date

UA = {"User-Agent": "eb-mm-research/1.0"}
BOOK = "https://clob.polymarket.com/book?token_id="

rows = json.load(open("mm_markets_raw.json"))

# sector map from DB (dedup: prefer a non-unknown label)
smap = {}
for line in open("sector_map.txt"):
    line = line.strip()
    if not line or "|" not in line:
        continue
    cid, cat = line.split("|", 1)
    cat = cat or "unknown"
    if cid not in smap or (smap[cid] == "unknown" and cat != "unknown"):
        smap[cid] = cat

KW = [
    (r"nba|nfl|mlb|nhl|ncaa|premier-league|epl|serie-a|la-liga|bundesliga|ligue-1|ufc|atp|wta|wimbledon|open-golf|pga|f1-|grand-prix|world-cup|fifa|uefa|copa|boxing|tennis|-vs-", "sports"),
    (r"lol-|league-of-legends|cs2|csgo|counter-strike|dota|valorant|overwatch|esports|lck|lpl|lec|lcs|ewc", "esports"),
    (r"bitcoin|btc|ethereum|eth-|solana|xrp|doge|crypto|coinbase|binance", "crypto"),
    (r"trump|biden|election|president|senate|congress|mayor|governor|primary|nominee|impeach|cabinet|supreme-court|parliament|minister|chancellor", "politics"),
    (r"temperature|highest-temp|rainfall|hurricane|tornado|snow|heat-|weather", "weather"),
    (r"fed-|interest-rate|cpi|inflation|gdp|recession|s-p-500|nasdaq|stock|treasury|tariff", "finance"),
    (r"israel|gaza|ukraine|russia|china-taiwan|iran|nato|ceasefire|war-|strike-on", "geopolitical"),
    (r"oscar|grammy|emmy|box-office|album|taylor-swift|movie|netflix|spotify", "entertainment"),
    (r"openai|gpt-|claude|gemini|spacex|starship|apple-|tesla|ai-model", "tech"),
]
def classify(m):
    s = smap.get(m["cid"], "unknown")
    if s not in ("unknown", "", None):
        return s
    text = (m["slug"] + " " + m["q"]).lower()
    for pat, lab in KW:
        if re.search(pat, text):
            return lab
    return "unknown"

today = date(2026, 7, 14)
mkts = []
for m in rows:
    if m["bb"] is None or m["ba"] is None or not (0 < m["bb"] < m["ba"] <= 1):
        continue
    m["sector"] = classify(m)
    m["spread"] = round(m["ba"] - m["bb"], 4)
    m["mid"] = round((m["bb"] + m["ba"]) / 2, 4)
    try:
        y, mo, d = map(int, m["end"].split("-"))
        m["days_to_end"] = max(0, (date(y, mo, d) - today).days)
    except Exception:
        m["days_to_end"] = None
    mkts.append(m)
print("two-sided:", len(mkts), "of", len(rows))

# depth sample: top 30 per sector by vol24
by_sec = defaultdict(list)
for m in mkts:
    by_sec[m["sector"]].append(m)
sample = []
for sec, ms in by_sec.items():
    ms.sort(key=lambda x: -x["vol24"])
    sample.extend([x for x in ms[:30] if x["tok"]])
print("depth sample:", len(sample))

def touch(m):
    try:
        b = json.load(urllib.request.urlopen(
            urllib.request.Request(BOOK + m["tok"], headers=UA), timeout=20))
    except Exception:
        return
    def best(levels, kind):
        bl = None
        for lv in levels or []:
            try:
                p, s = float(lv["price"]), float(lv["size"])
            except Exception:
                continue
            if not (0 < p < 1) or s <= 0:
                continue
            if bl is None or (p > bl[0] if kind == "bid" else p < bl[0]):
                bl = (p, s)
        return bl
    bb, ba = best(b.get("bids"), "bid"), best(b.get("asks"), "ask")
    if bb:
        m["bid_touch_usd"] = round(bb[0] * bb[1], 2)
    if ba:
        m["ask_touch_usd"] = round(ba[0] * ba[1], 2)

with ThreadPoolExecutor(max_workers=8) as ex:
    list(ex.map(touch, sample))

def pct(vals, p):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    k = (len(vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(vals) - 1)
    return round(vals[f] + (vals[c] - vals[f]) * (k - f), 4)

out = {}
for sec, ms in sorted(by_sec.items(), key=lambda kv: -sum(x["vol24"] for x in kv[1])):
    act = [m for m in ms if m["vol24"] >= 1000]
    depths = [m.get("bid_touch_usd", 0) + m.get("ask_touch_usd", 0) for m in ms
              if "bid_touch_usd" in m or "ask_touch_usd" in m]
    rew = [m for m in ms if m["reward_daily"] > 0]
    mids_mid = [m for m in act if 0.10 <= m["mid"] <= 0.90]
    out[sec] = {
        "n": len(ms), "n_active_1k": len(act),
        "vol24_usd": round(sum(m["vol24"] for m in ms)),
        "spread_med_all": pct([m["spread"] for m in ms], 0.5),
        "spread_med_active": pct([m["spread"] for m in act], 0.5),
        "spread_p75_active": pct([m["spread"] for m in act], 0.75),
        "spread_med_active_mid_px": pct([m["spread"] for m in mids_mid], 0.5),
        "pct_active_spread_ge_2pt": round(100 * sum(1 for m in act if m["spread"] >= 0.02) / len(act), 1) if act else None,
        "touch_usd_med(top30)": pct(depths, 0.5),
        "days_to_end_med": pct([m["days_to_end"] for m in ms], 0.5),
        "n_rewarded": len(rew),
        "rewards_usd_day": round(sum(m["reward_daily"] for m in ms)),
        "neg_risk_pct": round(100 * sum(1 for m in ms if m["neg_risk"]) / len(ms), 1),
    }
print(json.dumps(out, indent=1))
json.dump({"summary": out, "markets": mkts}, open("mm_analysis.json", "w"))

# spread-by-price-bucket, active markets, all sectors pooled + per big sector
print("\nspread by mid bucket (active >=1k vol24): bucket n med_spread")
allact = [m for m in mkts if m["vol24"] >= 1000]
for lo in [0.02, 0.1, 0.2, 0.4, 0.6, 0.8, 0.9]:
    hi = {0.02: 0.1, 0.1: 0.2, 0.2: 0.4, 0.4: 0.6, 0.6: 0.8, 0.8: 0.9, 0.9: 0.98}[lo]
    b = [m["spread"] for m in allact if lo <= m["mid"] < hi]
    print(f"  {lo}-{hi}: n={len(b)} med={pct(b,0.5)}")
