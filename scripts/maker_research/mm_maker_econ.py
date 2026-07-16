"""Per-maker reward economics, ALL sectors — the term data alone couldn't give.

For every rewarded market (gamma sweep 2026-07-14): fetch the live CLOB book,
score all resting in-band liquidity with the OFFICIAL formula
    S(v, s) = ((v - s) / v)^2 * size        [docs.polymarket.com liquidity-rewards]
    Q_min:  mid in [0.10,0.90] -> max(min(Q1,Q2), max(Q1,Q2)/3); else min(Q1,Q2)
then compute the pool share a NEW min-size two-sided quote at the touch would
earn: share = Q_mine / (Q_mine + Q_comp * cf), with complement-book factor
cf=2 (conservative: competitors also quote the complement token, which we
don't observe) and cf=1 (optimistic). $/day = share * rewardsDailyRate.
Capital ~= rewardsMinSize (mint pairs at $1/share covers both sides).
"""
import json, re, urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "eb-mm-econ/1.0"}
BOOK = "https://clob.polymarket.com/book?token_id="

rows = json.load(open("mm_markets_raw.json"))
smap = {}
for line in open("sector_map.txt"):
    line = line.strip()
    if "|" in line:
        c, cat = line.split("|", 1)
        if c not in smap or (smap[c] == "unknown" and cat != "unknown"):
            smap[c] = cat or "unknown"

KW = [
    (r"nba|nfl|mlb|nhl|ncaa|premier-league|epl|serie-a|la-liga|bundesliga|ligue-1|ufc|atp|wta|wimbledon|pga|f1-|grand-prix|world-cup|fifa|uefa|copa|boxing|tennis|-vs-", "sports"),
    (r"lol-|league-of-legends|cs2|csgo|counter-strike|dota|valorant|esports|lck|lpl|lec|ewc", "esports"),
    (r"bitcoin|btc|ethereum|eth-|solana|xrp|doge|crypto", "crypto"),
    (r"trump|election|president|senate|congress|mayor|governor|primary|nominee|supreme-court|minister", "politics"),
    (r"temperature|highest-temp|rainfall|hurricane|snow|heat-|weather", "weather"),
    (r"fed-|interest-rate|cpi|inflation|gdp|recession|s-p-500|nasdaq|spy|wti|tariff|treasury", "finance"),
    (r"israel|gaza|ukraine|russia|iran|nato|ceasefire|hormuz|houthi|war-", "geopolitical"),
    (r"oscar|grammy|emmy|box-office|album|movie|netflix|spotify", "entertainment"),
]
def sector(m):
    s = smap.get(m["cid"], "unknown")
    if s and s != "unknown":
        return s
    text = ((m.get("slug") or "") + " " + (m.get("q") or "")).lower()
    for pat, lab in KW:
        if re.search(pat, text):
            return lab
    return "unknown"

def fnum(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d

# rewarded markets only, with usable mid + config
cands = []
for m in rows:
    if m["reward_daily"] <= 0 or not m["tok"]:
        continue
    v = fnum(m.get("reward_max_spread"))          # in CENTS per docs
    msz = fnum(m.get("reward_min_size"))
    bb, ba = m.get("bb"), m.get("ba")
    if not v or not msz or bb is None or ba is None or not (0 < bb < ba <= 1):
        continue
    m["sector"] = sector(m)
    m["v"] = v / 100.0                            # cents -> price units
    m["msz"] = msz
    m["mid"] = (bb + ba) / 2
    cands.append(m)
print("rewarded markets with config+mid:", len(cands), "of", sum(1 for r in rows if r["reward_daily"] > 0))

def S(v, s, size):
    if s >= v or v <= 0:
        return 0.0
    return ((v - s) / v) ** 2 * size

def econ(m):
    try:
        book = json.load(urllib.request.urlopen(
            urllib.request.Request(BOOK + m["tok"], headers=UA), timeout=15))
    except Exception:
        book = {}
    mid, v = m["mid"], m["v"]
    q1 = q2 = 0.0
    for side, acc in (("bids", "q1"), ("asks", "q2")):
        tot = 0.0
        for lv in (book.get(side) or []):
            try:
                p, sz = float(lv["price"]), float(lv["size"])
            except Exception:
                continue
            if not (0 < p < 1) or sz <= 0:
                continue
            tot += S(v, abs(p - mid), sz)
        if acc == "q1":
            q1 = tot
        else:
            q2 = tot
    if 0.10 <= mid <= 0.90:
        q_comp = max(min(q1, q2), max(q1, q2) / 3.0)
    else:
        q_comp = min(q1, q2)
    # my quote: min-size both sides AT the touch -> s = half the current spread
    s_mine = max((m["ba"] - m["bb"]) / 2, 0.001)
    q_mine = S(v, s_mine, m["msz"])               # per side; two-sided => Q_min = this
    if q_mine <= 0:
        m["skip"] = "touch outside band"
        return
    m["share_cons"] = q_mine / (q_mine + q_comp * 2.0)
    m["share_opt"] = q_mine / (q_mine + q_comp * 1.0)
    m["usd_day_cons"] = m["share_cons"] * m["reward_daily"]
    m["usd_day_opt"] = m["share_opt"] * m["reward_daily"]
    m["capital"] = m["msz"]                       # mint pairs: $1/share covers both sides

with ThreadPoolExecutor(max_workers=8) as ex:
    list(ex.map(econ, cands))

done = [m for m in cands if "usd_day_cons" in m]
print("scored:", len(done), "| skipped:", len(cands) - len(done))

by = defaultdict(list)
for m in done:
    by[m["sector"]].append(m)

print("\n%-14s %4s %9s %11s %11s %10s %9s %9s" % (
    "sector", "n", "pools$/d", "mine$/d_con", "mine$/d_opt", "capital$", "yld%/d_c", "med_shr%"))
tot_c = tot_o = tot_cap = 0.0
for sec, ms in sorted(by.items(), key=lambda kv: -sum(x["usd_day_cons"] for x in kv[1])):
    pools = sum(x["reward_daily"] for x in ms)
    c = sum(x["usd_day_cons"] for x in ms)
    o = sum(x["usd_day_opt"] for x in ms)
    cap = sum(x["capital"] for x in ms)
    shr = sorted(x["share_cons"] for x in ms)[len(ms) // 2]
    tot_c += c; tot_o += o; tot_cap += cap
    print("%-14s %4d %9.0f %11.2f %11.2f %10.0f %9.2f %9.1f" % (
        sec, len(ms), pools, c, o, cap, 100 * c / cap if cap else 0, 100 * shr))
print("%-14s %4d %9s %11.2f %11.2f %10.0f %9.2f" % (
    "TOTAL", len(done), "-", tot_c, tot_o, tot_cap, 100 * tot_c / tot_cap if tot_cap else 0))

# top individual farms (conservative $/day per $ capital)
done.sort(key=lambda x: -(x["usd_day_cons"] / max(x["capital"], 1)))
print("\ntop 12 farms by conservative yield:")
for m in done[:12]:
    print("  %5.2f$/d  cap$%-5.0f shr%4.1f%%  %-10s %s" % (
        m["usd_day_cons"], m["capital"], 100 * m["share_cons"], m["sector"], m["q"][:52]))
json.dump([{k: m.get(k) for k in ("id", "q", "sector", "reward_daily", "msz", "v", "mid",
            "share_cons", "share_opt", "usd_day_cons", "usd_day_opt", "capital")} for m in done],
          open("mm_maker_econ_out.json", "w"))
print("\nsaved mm_maker_econ_out.json")
