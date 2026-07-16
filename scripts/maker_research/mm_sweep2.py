"""Live cross-sector MM sweep v2 — gamma pages (limit=100 cap), head+tail strata.
Writes markets JSON with conditionId for DB sector join. 2026-07-14."""
import json, time, urllib.request, urllib.parse

UA = {"User-Agent": "eb-mm-research/1.0"}
GAMMA = "https://gamma-api.polymarket.com/markets"

def get(url, timeout=25):
    for attempt in range(3):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout))
        except Exception as e:
            print("ERR", type(e).__name__, "attempt", attempt, url[:110])
            time.sleep(1.5 * (attempt + 1))
    return None

rows, seen = [], set()
for page in range(60):  # 6000 most active markets by 24h volume
    q = urllib.parse.urlencode({"active": "true", "closed": "false", "limit": 100,
                                "offset": page * 100, "order": "volume24hr", "ascending": "false"})
    data = get(f"{GAMMA}?{q}")
    if not data:
        break
    new = 0
    for m in data:
        mid = m.get("id")
        if mid in seen:
            continue
        seen.add(mid)
        new += 1
        try:
            bb, ba = m.get("bestBid"), m.get("bestAsk")
            bb = float(bb) if bb is not None else None
            ba = float(ba) if ba is not None else None
        except (TypeError, ValueError):
            bb = ba = None
        toks = []
        try:
            toks = json.loads(m.get("clobTokenIds") or "[]")
        except Exception:
            pass
        daily_reward = 0.0
        for r in (m.get("clobRewards") or []):
            try:
                daily_reward += float(r.get("rewardsDailyRate") or 0)
            except Exception:
                pass
        rows.append({
            "id": mid, "cid": m.get("conditionId"), "q": (m.get("question") or "")[:70],
            "gamma_cat": (m.get("category") or "").strip().lower(),
            "slug": (m.get("slug") or "")[:60],
            "bb": bb, "ba": ba,
            "vol24": float(m.get("volume24hr") or 0), "vol": float(m.get("volumeNum") or 0),
            "liq": float(m.get("liquidityNum") or 0), "neg_risk": bool(m.get("negRisk")),
            "end": (m.get("endDate") or "")[:10], "tok": toks[0] if toks else None,
            "reward_daily": daily_reward, "reward_max_spread": m.get("rewardsMaxSpread"),
            "reward_min_size": m.get("rewardsMinSize"),
        })
    if new == 0 or len(data) < 100:
        break
    time.sleep(0.25)

print("markets:", len(rows))
with open("mm_markets_raw.json", "w") as f:
    json.dump(rows, f)
# condition_id list for the DB sector join
with open("mm_cids.txt", "w") as f:
    for r in rows:
        if r["cid"]:
            f.write(r["cid"] + "\n")
print("cids written:", sum(1 for r in rows if r["cid"]))
