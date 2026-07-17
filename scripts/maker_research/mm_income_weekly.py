"""Weekly re-bucket of maker payment records, bins anchored at the WC promo
start (2026-06-11 00:00Z), REWARD vs MAKER_REBATE separated. Step-at-promo
vs smooth-ramp discriminator. Same guards as mm_income_monthly.py."""
import json, sys, time, urllib.request, calendar
from collections import defaultdict
UA = {"User-Agent": "pa2-maker-income-history/1.0"}
ANCHOR = calendar.timegm((2026, 6, 11, 0, 0, 0))
CUT = ANCHOR - 6 * 7 * 86400
reqs = 0; T0 = time.time()
def api(url):
    global reqs
    if reqs >= 4000 or time.time() - T0 > 1800: raise RuntimeError("budget")
    reqs += 1; time.sleep(0.15)
    for _ in range(3):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20))
        except Exception: time.sleep(2)
    return []
wallets = [w.strip() for w in open(sys.argv[1]) if w.strip().startswith("0x")]
wk = defaultdict(lambda: defaultdict(float))
for i, w in enumerate(wallets):
    for typ in ("REWARD", "MAKER_REBATE"):
        off = 0
        while off <= 20000:
            acts = api("https://data-api.polymarket.com/activity?user=%s&type=%s&limit=500&offset=%d" % (w, typ, off))
            if not isinstance(acts, list) or not acts: break
            oldest = None
            for a in acts:
                try: ts, usd = float(a["timestamp"]), float(a["usdcSize"])
                except Exception: continue
                oldest = ts if oldest is None else min(oldest, ts)
                if ts >= CUT:
                    wk[int((ts - ANCHOR) // (7 * 86400))][typ] += usd
            if len(acts) < 500 or (oldest is not None and oldest < CUT): break
            off += 500
    if (i + 1) % 20 == 0: print("  %d/%d" % (i + 1, len(wallets)), flush=True)
print("\nweek bins anchored at WC promo start (Jun 11). w0 = Jun 11-17.")
print("%-6s %-13s %10s %12s %10s" % ("bin", "week starts", "REWARD$", "REBATE$", "TOTAL$"))
for b in sorted(wk):
    start = ANCHOR + b * 7 * 86400
    lab = time.strftime("%m-%d", time.gmtime(start))
    r, rb = wk[b]["REWARD"], wk[b]["MAKER_REBATE"]
    print("w%-5d %-13s %10.0f %12.0f %10.0f" % (b, lab, r, rb, r + rb))
print("reqs:", reqs)
