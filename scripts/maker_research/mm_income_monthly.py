"""Multi-month REAL maker income from payment records — measurement only.

Extends the verified mm_real_cohort.py income leg (data-api activity,
type=REWARD/MAKER_REBATE, every entry a Polygon tx) from 30 days to ~6
months, bucketed by calendar month (UTC), to separate the steady-state
subsidy from promo spikes (World Cup incentive: Jun 11 - Jul 19, 2026).

Known bias, reported not hidden: the cohort is wallets flagged as makers
ACTIVE IN THE LAST 30 DAYS, so months before a wallet started understate
the historical total. Per-active-wallet stats are reported alongside.

Guards: GET-only, public API, no keys; runs from the LOCAL machine (never
the VPS IP the recorder arms depend on); 0.15s sleep between requests;
hard caps: MAX_REQS total requests, MAX_WALL seconds wall clock.
"""
import json, sys, time, urllib.request
from collections import defaultdict

UA = {"User-Agent": "pa2-maker-income-history/1.0"}
DAYS_BACK = 200                      # ~6.5 months; bucket boundary is monthly
CUT = time.time() - DAYS_BACK * 86400
MAX_REQS = 8000
MAX_WALL = 45 * 60
MAX_OFFSET = 20000                   # per wallet x type; early-break on age
T0 = time.time()
reqs = 0


def api(url):
    global reqs
    if reqs >= MAX_REQS or time.time() - T0 > MAX_WALL:
        raise RuntimeError("budget exhausted: reqs=%d wall=%.0fs" % (reqs, time.time() - T0))
    reqs += 1
    time.sleep(0.15)
    for _ in range(3):
        try:
            return json.load(urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=20))
        except Exception:
            time.sleep(2)
    return []


wallets = [w.strip() for w in open(sys.argv[1])
           if w.strip().startswith("0x")]
print("cohort wallets: %d, window: last %d days" % (len(wallets), DAYS_BACK), flush=True)

month_tot = defaultdict(lambda: defaultdict(float))   # month -> type -> $
month_wallets = defaultdict(set)                      # month -> active wallets
wallet_month = defaultdict(lambda: defaultdict(float))  # (w) -> month -> $
truncated = []                                        # wallets that hit MAX_OFFSET

try:
    for i, w in enumerate(wallets):
        for typ in ("REWARD", "MAKER_REBATE"):
            off = 0
            while off <= MAX_OFFSET:
                acts = api("https://data-api.polymarket.com/activity?user=%s&type=%s&limit=500&offset=%d"
                           % (w, typ, off))
                if not isinstance(acts, list) or not acts:
                    break
                oldest = None
                for a in acts:
                    try:
                        ts, usd = float(a["timestamp"]), float(a["usdcSize"])
                    except Exception:
                        continue
                    oldest = ts if oldest is None else min(oldest, ts)
                    if ts >= CUT:
                        m = time.strftime("%Y-%m", time.gmtime(ts))
                        month_tot[m][typ] += usd
                        month_wallets[m].add(w)
                        wallet_month[w][m] += usd
                if len(acts) < 500 or (oldest is not None and oldest < CUT):
                    break
                off += 500
            else:
                truncated.append((w, typ))
        if (i + 1) % 10 == 0:
            print("  %d/%d wallets, %d reqs, %.0fs" % (i + 1, len(wallets), reqs,
                                                       time.time() - T0), flush=True)
except RuntimeError as e:
    print("STOPPED EARLY — %s (results below are PARTIAL)" % e, flush=True)

print("\n=== maker cohort income by month (payment records, %d wallets) ===" % len(wallets))
print("%-8s %12s %14s %12s %8s %12s" % ("month", "REWARD$", "MAKER_REBATE$",
                                        "TOTAL$", "active", "med$/active"))
for m in sorted(month_tot):
    tot = month_tot[m]["REWARD"] + month_tot[m]["MAKER_REBATE"]
    act = month_wallets[m]
    per = sorted(wallet_month[w][m] for w in act)
    med = per[len(per) // 2] if per else 0.0
    print("%-8s %12.0f %14.0f %12.0f %8d %12.0f"
          % (m, month_tot[m]["REWARD"], month_tot[m]["MAKER_REBATE"], tot, len(act), med))
if truncated:
    print("TRUNCATED (hit offset cap, month totals are lower bounds): %s"
          % ", ".join("%s/%s" % (w[:8], t) for w, t in truncated[:10]))
print("requests used: %d, wall: %.0fs" % (reqs, time.time() - T0))

with open(sys.argv[2], "w") as f:
    json.dump({"generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "days_back": DAYS_BACK, "wallets": len(wallets), "requests": reqs,
               "months": {m: {"reward": month_tot[m]["REWARD"],
                              "rebate": month_tot[m]["MAKER_REBATE"],
                              "total": month_tot[m]["REWARD"] + month_tot[m]["MAKER_REBATE"],
                              "active_wallets": len(month_wallets[m])}
                          for m in sorted(month_tot)},
               "truncated": truncated}, f, indent=1)
print("json written:", sys.argv[2])
