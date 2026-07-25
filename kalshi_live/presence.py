"""TIME-IN-BOOK, measured from order history. READ-ONLY.

LIP scores a snapshot every second and SUMS them, so reward is proportional to
size x seconds resting, not to a quote's quality at one instant. Every capture
number we have assumes 100% presence. This measures the real thing.

Per order: rested from created_time to last_update_time (cancel time for canceled,
final fill for executed). Per market: the UNION of those intervals = seconds we had
at least one order resting, compared to the market's own open->close life.
"""
import sys, os, json, collections, statistics as st
import datetime as dt
sys.path.insert(0, "/opt/pa2-maker-kalshi-live")
import maker_kalshi_quoter as q

c = q.KalshiOrderClient()

def iso(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))

orders = []
for stt in ("canceled", "executed", "resting"):
    try:
        for o in (c.get_orders(status=stt).get("orders") or []):
            orders.append(o)
    except Exception as e:
        print("ERR", stt, repr(e)[:120])
print(f"orders pulled: {len(orders)}")

recs = []
for o in orders:
    try:
        a, b = iso(o["created_time"]), iso(o["last_update_time"])
        life = (b - a).total_seconds()
        if life < 0:
            continue
        recs.append({"t": o["ticker"], "a": a, "b": b, "life": life,
                     "ct": float(o.get("initial_count_fp") or 0),
                     "status": o.get("status")})
    except Exception:
        pass
print(f"orders with usable timestamps: {len(recs)} across {len({r['t'] for r in recs})} markets")

lives = [r["life"] for r in recs]
print(f"\nORDER LIFETIME (seconds resting), n={len(lives)}:")
s = sorted(lives)
for lbl, v in (("p25", s[len(s)//4]), ("median", st.median(s)),
               ("p75", s[3*len(s)//4]), ("p90", s[int(len(s)*.9)]), ("max", max(s))):
    print(f"   {lbl:6s} {v:9.0f}s = {v/60:7.1f} min")
print(f"   mean {st.mean(s):.0f}s = {st.mean(s)/60:.1f} min")
short = sum(1 for x in lives if x < 120)
print(f"   orders resting < 1 quote cycle (120s): {short}/{len(lives)} ({short/len(lives)*100:.1f}%)")

# contract-seconds = the actual LIP score currency (size x time)
cs = sum(r["ct"] * r["life"] for r in recs)
print(f"\ntotal contract-seconds resting: {cs:,.0f}  (= {cs/3600:,.0f} contract-hours)")

# per-market UNION coverage vs the market's own life
by = collections.defaultdict(list)
for r in recs:
    by[r["t"]].append((r["a"], r["b"]))
rows = []
for t, ivs in by.items():
    ivs.sort()
    merged = []
    for a, b in ivs:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    cov = sum((b - a).total_seconds() for a, b in merged)
    rows.append({"ticker": t, "covered_s": cov, "first": merged[0][0], "last": merged[-1][1],
                 "n_orders": len(ivs)})
rows.sort(key=lambda r: -r["covered_s"])
print(f"\nPER-MARKET presence (union of resting intervals), {len(rows)} markets:")
print("  fetching each market's open->close to get the FRACTION...")
out = []
for r in rows[:25]:
    try:
        m = q.public_get(f"/trade-api/v2/markets/{r['ticker']}").get("market") or {}
        o, cl = m.get("open_time"), m.get("close_time")
        if o and cl:
            life = (iso(cl) - iso(o)).total_seconds()
            r["market_life_s"] = life
            r["presence_pct"] = 100.0 * r["covered_s"] / life if life > 0 else None
    except Exception as e:
        r["err"] = repr(e)[:60]
    out.append(r)
    p = r.get("presence_pct")
    print(f"   {r['ticker']:34s} covered {r['covered_s']/3600:6.2f}h  "
          f"life {r.get('market_life_s',0)/3600:7.2f}h  presence "
          f"{(f'{p:5.1f}%' if p is not None else '   ?  ')}  orders={r['n_orders']}")
ps = [r["presence_pct"] for r in out if r.get("presence_pct") is not None]
if ps:
    print(f"\nPRESENCE across {len(ps)} markets: median {st.median(ps):.1f}%  "
          f"mean {st.mean(ps):.1f}%  min {min(ps):.1f}%  max {max(ps):.1f}%")
json.dump(out, open("/tmp/presence.json", "w"), default=str, indent=1)
