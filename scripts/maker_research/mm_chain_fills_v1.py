"""CHAIN-FILLS STUDY v1 — real OrderFilled events vs our hypothetical quotes.
Replaces the fill-model assumption with receipts: (a) how much REAL maker
volume fills AT the price levels we would quote (our model assumes zero),
(b) what happened to the makers who stood exactly where we would stand.
Read-only public RPC from the VPS. Sampled windows, budget-bounded."""
import json, glob, bisect, collections, urllib.request, time

RPC = "https://polygon-bor-rpc.publicnode.com"      # light calls only
RPC_LOGS = "https://1rpc.io/matic"                   # getLogs (<=50 blocks)
UA = "Mozilla/5.0 (pa2-maker-research)"
EXCH = ["0xE111180000d2663C0091e4f400237545B87B996B",
        "0xe2222d279d744050d28e00520010520000310F59"]
TOPIC = None  # learned from first receipt-bearing block

def rpc(method, params, url=None):
    req = urllib.request.Request(url or (RPC_LOGS if method == "eth_getLogs" else RPC),
        headers={"Content-Type": "application/json", "User-Agent": UA},
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode())
    for k in range(5):
        try:
            r = json.load(urllib.request.urlopen(req, timeout=45))
            if "result" in r: return r["result"]
            time.sleep(6 + 4 * k)
        except Exception:
            time.sleep(6 + 4 * k)
    return None

head = int(rpc("eth_blockNumber", []), 16)
def blk_ts(n):
    b = rpc("eth_getBlockByNumber", [hex(n), False])
    return int(b["timestamp"], 16) if b else None

# our universe + hypothetical quotes from v5 samples (mid per market-minute)
uni = json.load(open("/opt/pa2-maker-sim-v3/universe.json"))["markets"]
by_asset = {}
for m in uni:
    for side, tok in (("yes", m["yes"]), ("no", m["no"])):
        by_asset[int(tok)] = (str(m["id"]), side, m["v"], m["msz"], m.get("sector"), m.get("pool"))
mids = collections.defaultdict(list)
for fp in glob.glob("/opt/pa2-maker-sim-v3/samples-*.jsonl"):
    for ln in open(fp):
        try: r = json.loads(ln)
        except Exception: continue
        if r.get("mid") is not None: mids[str(r["id"])].append((r["t"], r["mid"]))
for v in mids.values(): v.sort()
def mid_at(mkt, t, tol=180):
    s = mids.get(mkt) or []
    i = bisect.bisect_left(s, (t, -1))
    best = None
    for j in (i-1, i, i+1):
        if 0 <= j < len(s) and abs(s[j][0]-t) <= tol:
            if best is None or abs(s[j][0]-t) < abs(best[0]-t): best = s[j]
    return best[1] if best else None

CHUNK = 45                # blocks per getLogs (1rpc limit is 50)
N_CHUNKS = 16             # strided, paced
fills_all = 0
at_level = []             # (t, mkt, maker_side_buy, price, usd, outcome_slot)
through = usd_at = usd_through = 0.0
t0 = time.time()
first_topic_printed = False
for c in range(N_CHUNKS):
    if time.time() - t0 > 560: print("time cap at chunk", c); break
    b1 = head - 1200 - c * 700   # start ~40min back (outcomes exist), stride ~23min
    b0 = b1 - CHUNK + 1
    ts0, ts1 = blk_ts(b0), blk_ts(b1)
    if not ts0 or not ts1: continue
    time.sleep(3)
    logs = rpc("eth_getLogs", [{"fromBlock": hex(b0), "toBlock": hex(b1), "address": EXCH}])
    if logs is None: print("getLogs failed chunk", c); continue
    for lg in logs:
        tp = lg.get("topics") or []
        if len(tp) != 4: continue
        data = lg["data"][2:]
        w = [int(data[i:i+64], 16) for i in range(0, len(data), 64)]
        if len(w) < 5: continue
        maker_asset, taker_asset = w[0], w[1]
        m_amt, t_amt = w[2] / 1e6, w[3] / 1e6
        if m_amt <= 0 or t_amt <= 0: continue
        fills_all += 1
        if maker_asset == 0 and taker_asset in by_asset:
            tok, buy, price, usd = taker_asset, True, m_amt / t_amt, m_amt
        elif taker_asset == 0 and maker_asset in by_asset:
            tok, buy, price, usd = maker_asset, False, t_amt / m_amt, t_amt
        else:
            continue
        mkt, side, v, msz, sec, pool = by_asset[tok]
        bn = int(lg["blockNumber"], 16)
        ts = ts0 + (ts1 - ts0) * (bn - b0) / max(b1 - b0, 1)
        # our hypothetical WIDE quote on the YES token at that minute
        if side != "yes": continue
        mid = mid_at(mkt, ts)
        if mid is None: continue
        our_bid, our_ask = mid - v / 2, mid + v / 2
        lvl = our_bid if buy else our_ask
        if abs(price - lvl) <= 0.005:
            after = mid_at(mkt, ts + 1800, tol=600)
            out = None
            if after is not None:
                out = (after - price) * (1 if buy else -1)
            at_level.append((sec, out, usd))
            usd_at += usd
        elif (buy and price < our_bid - 0.005) or ((not buy) and price > our_ask + 0.005):
            through += 1; usd_through += usd
print("windows: %d chunks x %d blocks (~%.1fh); ALL exchange fills decoded: %d" % (min(c+1, N_CHUNKS), CHUNK, min(c+1, N_CHUNKS)*CHUNK*2.1/3600, fills_all))
print("\nfills on OUR 140-market universe (YES tokens, vs our hypothetical WIDE quotes):")
print("  AT our level (model says we NEVER get these): n=%d, $%.0f volume" % (len(at_level), usd_at))
print("  THROUGH our level (model already counts):     n=%d, $%.0f volume" % (through, usd_through))
r = usd_at / max(usd_through, 1)
print("  at:through volume ratio = %.2f  -> fill-model uplift factor if we capture even a pro-rata slice" % r)
outs = sorted(o for _, o, _ in at_level if o is not None)
if outs:
    n = len(outs)
    print("\nMAN-IN-OUR-SPOT (real makers filled AT our level, their 30-min outcome):")
    print("  n=%d  mean=%+.4f  adverse>1pt=%.1f%%  adverse>2pt=%.1f%%  median=%+.4f"
          % (n, sum(outs)/n, 100*sum(1 for x in outs if x < -0.01)/n,
             100*sum(1 for x in outs if x < -0.02)/n, outs[n//2]))
bysec = collections.defaultdict(lambda: [0, 0.0])
for sec, o, usd in at_level:
    bysec[sec][0] += 1; bysec[sec][1] += usd
print("\nat-our-level fills by sector:", {k: "n=%d/$%.0f" % (v[0], v[1]) for k, v in bysec.items()})
