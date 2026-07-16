"""Refinement pass: top candidates re-scored with BOTH token books (true
Q_one/Q_two per docs: bids(primary)+asks(complement) / asks(primary)+bids(complement)),
fresh mids, and lifetime flags. Kills the empty-primary-book '100% share' artifact."""
import json, urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date

UA = {"User-Agent": "eb-mm-econ/1.0"}
BOOK = "https://clob.polymarket.com/book?token_id="
GAMMA1 = "https://gamma-api.polymarket.com/markets/"

prev = json.load(open("mm_maker_econ_out.json"))
prev.sort(key=lambda x: -x["usd_day_cons"])
top = prev[:130]
print("refining top", len(top), "by conservative $/day")

def get(url):
    try:
        return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15))
    except Exception:
        return None

def S(v, s, size):
    return ((v - s) / v) ** 2 * size if v > 0 and s < v else 0.0

def side_score(book, side, mid, v):
    tot = 0.0
    for lv in (book.get(side) or []) if isinstance(book, dict) else []:
        try:
            p, sz = float(lv["price"]), float(lv["size"])
        except Exception:
            continue
        if 0 < p < 1 and sz > 0:
            tot += S(v, abs(p - mid), sz)
    return tot

today = date(2026, 7, 14)

def refine(m):
    g = get(GAMMA1 + str(m["id"]))
    if not g:
        m["ref_skip"] = "gamma fetch failed"
        return
    try:
        toks = json.loads(g.get("clobTokenIds") or "[]")
        bb, ba = float(g["bestBid"]), float(g["bestAsk"])
    except Exception:
        m["ref_skip"] = "no fresh touch"
        return
    if len(toks) < 2 or not (0 < bb < ba <= 1):
        m["ref_skip"] = "no token pair / bad touch"
        return
    mid = (bb + ba) / 2
    v = m["v"]
    b_pri = get(BOOK + toks[0]) or {}
    b_cmp = get(BOOK + toks[1]) or {}
    mid_c = 1 - mid
    # docs: Q_one = scored bids(primary) + scored asks(complement); Q_two = mirror
    q1 = side_score(b_pri, "bids", mid, v) + side_score(b_cmp, "asks", mid_c, v)
    q2 = side_score(b_pri, "asks", mid, v) + side_score(b_cmp, "bids", mid_c, v)
    q_comp = max(min(q1, q2), max(q1, q2) / 3.0) if 0.10 <= mid <= 0.90 else min(q1, q2)
    s_mine = max((ba - bb) / 2, 0.001)
    q_mine = S(v, s_mine, m["msz"])
    if q_mine <= 0:
        m["ref_skip"] = "touch outside band"
        return
    m["ref_share"] = q_mine / (q_mine + q_comp)
    m["ref_usd_day"] = m["ref_share"] * m["reward_daily"]
    m["ref_mid"] = round(mid, 3)
    try:
        e = (g.get("endDate") or "")[:10]
        y, mo, d = map(int, e.split("-"))
        m["days_left"] = (date(y, mo, d) - today).days
    except Exception:
        m["days_left"] = None

with ThreadPoolExecutor(max_workers=8) as ex:
    list(ex.map(refine, top))

done = [m for m in top if "ref_usd_day" in m]
print("refined:", len(done), "| skipped:", len(top) - len(done))

by = defaultdict(lambda: [0, 0.0, 0.0, 0.0])
for m in done:
    b = by[m["sector"]]
    b[0] += 1
    b[1] += m["usd_day_cons"]          # naive pass
    b[2] += m["ref_usd_day"]           # refined (both books)
    b[3] += m["capital"]
print("\n%-14s %4s %12s %12s %10s  (top-130 subset only)" % ("sector", "n", "naive$/d", "refined$/d", "capital$"))
for sec, b in sorted(by.items(), key=lambda kv: -kv[1][2]):
    print("%-14s %4d %12.2f %12.2f %10.0f" % (sec, b[0], b[1], b[2], b[3]))
print("%-14s %4d %12.2f %12.2f %10.0f" % ("TOTAL", len(done),
      sum(b[1] for b in by.values()), sum(b[2] for b in by.values()),
      sum(b[3] for b in by.values())))

done.sort(key=lambda x: -x["ref_usd_day"])
print("\ntop 15 refined farms ($/day, share, days_left):")
for m in done[:15]:
    print("  %7.2f$/d shr%5.1f%% cap$%-5.0f dleft=%-4s %-11s %s" % (
        m["ref_usd_day"], 100 * m["ref_share"], m["capital"],
        str(m["days_left"]), m["sector"], m["q"][:48]))

sustainable = [m for m in done if (m["days_left"] or 0) >= 2]
print("\nexcluding markets ending <2 days: %d markets, refined $%.0f/day on $%.0f capital"
      % (len(sustainable), sum(m["ref_usd_day"] for m in sustainable),
         sum(m["capital"] for m in sustainable)))
json.dump(done, open("mm_maker_econ_refined.json", "w"))
