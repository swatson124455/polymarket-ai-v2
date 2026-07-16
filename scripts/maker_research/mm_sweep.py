"""Live cross-sector market-making sweep (read-only, public keyless APIs).

Pages gamma /markets (active, by volume), takes verified-correct bestBid/bestAsk
fields, then fetches CLOB books for a per-sector sample to get depth at touch.
Outputs JSON + printed summary. 2026-07-14.
"""
import json, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict

UA = {"User-Agent": "eb-mm-research/1.0"}
GAMMA = "https://gamma-api.polymarket.com/markets"
BOOK = "https://clob.polymarket.com/book?token_id="

def get(url, timeout=20):
    try:
        return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout))
    except Exception as e:
        print("ERR", type(e).__name__, url[:90])
        return None

# ---- 1. page gamma ----
rows = []
for page in range(10):  # 10 x 250 = up to 2500 markets by volume
    q = urllib.parse.urlencode({"active": "true", "closed": "false", "limit": 250,
                                "offset": page * 250, "order": "volumeNum", "ascending": "false"})
    data = get(f"{GAMMA}?{q}")
    if not data:
        break
    rows.extend(data)
    if len(data) < 250:
        break
    time.sleep(0.3)
print("gamma markets fetched:", len(rows))

def sector(m):
    c = (m.get("category") or "").strip().lower()
    if c:
        return c
    # fallback: crude slug keywords
    s = (m.get("slug") or "").lower()
    for k, v in [("nba-", "sports"), ("nfl-", "sports"), ("mlb-", "sports"), ("nhl-", "sports"),
                 ("epl-", "sports"), ("-vs-", "sports"), ("bitcoin", "crypto"), ("ethereum", "crypto"),
                 ("election", "politics"), ("president", "politics"), ("temperature", "weather"),
                 ("lol-", "esports"), ("cs2", "esports"), ("dota", "esports"), ("valorant", "esports")]:
        if k in s:
            return v
    return "unknown"

mkts = []
for m in rows:
    try:
        bb, ba = m.get("bestBid"), m.get("bestAsk")
        if bb is None or ba is None:
            continue
        bb, ba = float(bb), float(ba)
        if not (0 < bb < 1 and 0 < ba <= 1 and ba > bb):
            continue
        toks = json.loads(m.get("clobTokenIds") or "[]")
        rw = m.get("clobRewards") or []
        daily_reward = 0.0
        for r in rw:
            try:
                daily_reward += float(r.get("rewardsDailyRate") or 0)
            except Exception:
                pass
        mkts.append({
            "id": m.get("id"), "q": (m.get("question") or "")[:70], "sector": sector(m),
            "bb": bb, "ba": ba, "spread": round(ba - bb, 4), "mid": round((bb + ba) / 2, 4),
            "vol": float(m.get("volumeNum") or 0), "vol24": float(m.get("volume24hr") or 0),
            "liq": float(m.get("liquidityNum") or 0), "neg_risk": bool(m.get("negRisk")),
            "tok": toks[0] if toks else None,
            "reward_daily": daily_reward,
            "reward_max_spread": m.get("rewardsMaxSpread"), "reward_min_size": m.get("rewardsMinSize"),
        })
    except Exception:
        continue
print("usable two-sided books:", len(mkts))

# ---- 2. depth sample: top-40 by 24h volume per sector ----
by_sec = defaultdict(list)
for m in mkts:
    by_sec[m["sector"]].append(m)
sample = []
for sec, ms in by_sec.items():
    ms.sort(key=lambda x: -x["vol24"])
    sample.extend([m for m in ms[:40] if m["tok"]])
print("depth sample size:", len(sample))

def touch(m):
    b = get(BOOK + m["tok"])
    if not isinstance(b, dict):
        return
    def best(levels, kind):
        best_lv = None
        for lv in levels or []:
            try:
                p, s = float(lv["price"]), float(lv["size"])
            except Exception:
                continue
            if not (0 < p < 1) or s <= 0:
                continue
            if best_lv is None or (p > best_lv[0] if kind == "bid" else p < best_lv[0]):
                best_lv = (p, s)
        return best_lv
    bb = best(b.get("bids"), "bid")
    ba = best(b.get("asks"), "ask")
    if bb:
        m["bid_touch_usd"] = round(bb[0] * bb[1], 2)
    if ba:
        m["ask_touch_usd"] = round(ba[0] * ba[1], 2)

with ThreadPoolExecutor(max_workers=8) as ex:
    list(ex.map(touch, sample))

# ---- 3. aggregate ----
def pct(vals, p):
    if not vals:
        return None
    vals = sorted(vals)
    k = (len(vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(vals) - 1)
    return round(vals[f] + (vals[c] - vals[f]) * (k - f), 4)

summary = {}
for sec, ms in sorted(by_sec.items(), key=lambda kv: -len(kv[1])):
    sp = [m["spread"] for m in ms]
    sp_active = [m["spread"] for m in ms if m["vol24"] > 1000]
    depths = [m.get("bid_touch_usd", 0) + m.get("ask_touch_usd", 0) for m in ms
              if "bid_touch_usd" in m or "ask_touch_usd" in m]
    rewarded = [m for m in ms if m["reward_daily"] > 0]
    summary[sec] = {
        "n_markets": len(ms),
        "n_active24h_gt1k": len(sp_active),
        "spread_med": pct(sp, 0.5), "spread_p25": pct(sp, 0.25), "spread_p75": pct(sp, 0.75),
        "spread_med_active": pct(sp_active, 0.5),
        "pct_spread_le_2pt": round(100 * sum(1 for s in sp if s <= 0.02) / len(sp), 1) if sp else None,
        "touch_depth_usd_med": pct(depths, 0.5) if depths else None,
        "touch_depth_usd_p75": pct(depths, 0.75) if depths else None,
        "vol24_total_usd": round(sum(m["vol24"] for m in ms)),
        "n_with_rewards": len(rewarded),
        "reward_daily_usd_total": round(sum(m["reward_daily"] for m in ms)),
        "reward_max_spread_typ": pct([float(m["reward_max_spread"]) for m in rewarded
                                      if m.get("reward_max_spread") not in (None, "")], 0.5) if rewarded else None,
    }

print(json.dumps(summary, indent=1))
with open("mm_sweep_out.json", "w") as f:
    json.dump({"summary": summary, "markets": mkts}, f)
print("saved mm_sweep_out.json; total markets:", len(mkts))
