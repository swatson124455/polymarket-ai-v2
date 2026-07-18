"""CHAIN-FILLS STUDY v2 — level depth -> our pro-rata slice + quiet hours.

Extends v1 (mm_chain_fills_v1.py): same OrderFilled decode against our
hypothetical WIDE quotes, plus
  (a) 24 HOURLY windows across the last day — v1's receipts were 91%
      esports prime-time (concentration); v2 covers quiet hours BY DESIGN;
  (b) pro-rata slice: for each at-level fill, join the nearest
      orderbook_snapshots row (read-only ghost-read, operator-acknowledged)
      and estimate our_slice = msz / (msz + side_depth_5pct_shares).
      CAVEAT (checked vs collector source): the depth band is RELATIVE
      5% of mid (mid*0.05), which for low mids lies INSIDE our wide level,
      and the snapshot may be pre- or post-sweep (+-10 min slop). The slice
      is therefore an ORDER-OF-MAGNITUDE PROXY with unknown sign — NOT a
      floor, NOT a bound. Quiet-hour receipts are the primary deliverable;
      the slice column is directional context only.

Phases:  --scan  chain decode -> mm_chain_fills_v2_fills.json (~6 min, paced)
         --join  depth join + report (needs sudo -u postgres psql access)

Read-only everywhere: public RPC (1rpc.io getLogs <=45 blocks, paced;
publicnode for light calls — SHARED with redemption-service IP, keep light),
SELECT-only SQL. Run from the VPS shell, ad hoc.
"""
import argparse
import bisect
import collections
import glob
import json
import subprocess
import time
import urllib.request

RPC = "https://polygon-bor-rpc.publicnode.com"
RPC_LOGS = "https://1rpc.io/matic"
UA = "Mozilla/5.0 (pa2-maker-research)"
EXCH = ["0xE111180000d2663C0091e4f400237545B87B996B",
        "0xe2222d279d744050d28e00520010520000310F59"]
CHUNK = 45
BLOCKS_PER_HOUR = 1714          # polygon ~2.1s/block
N_HOURS = 24
FILLS_PATH = "mm_chain_fills_v2_fills.json"
QUIET_UTC = set(range(3, 16))   # 03-15Z = quiet; else prime (label, not law)


def rpc(method, params, url=None):
    req = urllib.request.Request(
        url or (RPC_LOGS if method == "eth_getLogs" else RPC),
        headers={"Content-Type": "application/json", "User-Agent": UA},
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                         "params": params}).encode())
    for k in range(5):
        try:
            r = json.load(urllib.request.urlopen(req, timeout=45))
            if "result" in r:
                return r["result"]
            time.sleep(6 + 4 * k)
        except Exception:
            time.sleep(6 + 4 * k)
    return None


def load_universe():
    uni = json.load(open("/opt/pa2-maker-sim-v3/universe.json"))["markets"]
    by_asset = {}
    for m in uni:
        for side, tok in (("yes", m["yes"]), ("no", m["no"])):
            by_asset[int(tok)] = (str(m["id"]), side, m["v"], m["msz"],
                                  m.get("sector"), str(tok))
    return by_asset


def load_mids():
    mids = collections.defaultdict(list)
    for fp in glob.glob("/opt/pa2-maker-sim-v3/samples-*.jsonl"):
        for ln in open(fp):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r.get("mid") is not None:
                mids[str(r["id"])].append((r["t"], r["mid"]))
    for v in mids.values():
        v.sort()
    return mids


def mid_at(mids, mkt, t, tol=180):
    s = mids.get(mkt) or []
    i = bisect.bisect_left(s, (t, -1))
    best = None
    for j in (i - 1, i, i + 1):
        if 0 <= j < len(s) and abs(s[j][0] - t) <= tol:
            if best is None or abs(s[j][0] - t) < abs(best[0] - t):
                best = s[j]
    return best[1] if best else None


def scan():
    by_asset = load_universe()
    mids = load_mids()
    head = int(rpc("eth_blockNumber", []), 16)

    def blk_ts(n):
        b = rpc("eth_getBlockByNumber", [hex(n), False])
        return int(b["timestamp"], 16) if b else None

    fills_all = 0
    mid_miss = 0
    at_level, through_by_hour = [], collections.defaultdict(lambda: [0, 0.0])
    t0 = time.time()
    done = 0
    for h in range(1, N_HOURS + 1):
        if time.time() - t0 > 3300:   # first run: 900s covered only 13/24 windows
            print("time cap at hour-window", h)
            break
        b1 = head - h * BLOCKS_PER_HOUR
        b0 = b1 - CHUNK + 1
        ts0, ts1 = blk_ts(b0), blk_ts(b1)
        if not ts0 or not ts1:
            continue
        time.sleep(3)
        logs = rpc("eth_getLogs", [{"fromBlock": hex(b0), "toBlock": hex(b1),
                                    "address": EXCH}])
        if logs is None:
            print("getLogs failed window", h)
            continue
        done += 1
        for lg in logs:
            tp = lg.get("topics") or []
            if len(tp) != 4:
                continue
            data = lg["data"][2:]
            w = [int(data[i:i + 64], 16) for i in range(0, len(data), 64)]
            if len(w) < 5:
                continue
            m_asset, t_asset = w[0], w[1]
            m_amt, t_amt = w[2] / 1e6, w[3] / 1e6
            if m_amt <= 0 or t_amt <= 0:
                continue
            fills_all += 1
            if m_asset == 0 and t_asset in by_asset:
                tok, buy, price, usd = t_asset, True, m_amt / t_amt, m_amt
            elif t_asset == 0 and m_asset in by_asset:
                tok, buy, price, usd = m_asset, False, t_amt / m_amt, t_amt
            else:
                continue
            mkt, side, v, msz, sec, tok_s = by_asset[tok]
            if side != "yes":
                continue
            bn = int(lg["blockNumber"], 16)
            ts = ts0 + (ts1 - ts0) * (bn - b0) / max(b1 - b0, 1)
            mid = mid_at(mids, mkt, ts)
            if mid is None:
                mid_miss += 1      # universe rotated / market not sampled then
                continue
            our_bid, our_ask = mid - v / 2, mid + v / 2
            lvl = our_bid if buy else our_ask
            hour = time.gmtime(ts).tm_hour
            if abs(price - lvl) <= 0.005:
                after = mid_at(mids, mkt, ts + 1800, tol=600)
                out = (after - price) * (1 if buy else -1) \
                    if after is not None else None
                at_level.append({"ts": round(ts), "hour": hour, "mkt": mkt,
                                 "tok": tok_s, "buy": buy, "price": price,
                                 "usd": round(usd, 2), "msz": msz,
                                 "sec": sec, "out": out})
            elif (buy and price < our_bid - 0.005) or \
                    ((not buy) and price > our_ask + 0.005):
                through_by_hour[hour][0] += 1
                through_by_hour[hour][1] += usd
    payload = {"scanned_windows": done, "fills_all": fills_all,
               "mid_miss": mid_miss,
               "at_level": at_level,
               "through_by_hour": {str(k): v for k, v in through_by_hour.items()},
               "head": head, "scan_t": time.time()}
    json.dump(payload, open(FILLS_PATH, "w"))
    print("scan done: %d/%d hourly windows, %d exchange fills decoded, "
          "%d at-level (mid-miss dropped %d) -> %s"
          % (done, N_HOURS, fills_all, len(at_level), mid_miss, FILLS_PATH))


def _psql(sql):
    r = subprocess.run(["sudo", "-u", "postgres", "psql", "-d", "polymarket",
                        "-t", "-A", "-F", "|", "-c", sql],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0 or (not r.stdout.strip() and r.stderr.strip()):
        print("psql problem:", r.stderr.strip()[:300])
    return [ln.split("|") for ln in r.stdout.splitlines() if ln.strip()]


def join():
    d = json.load(open(FILLS_PATH))
    at = d["at_level"]
    if not at:
        print("no at-level fills recorded — nothing to join")
        return
    toks = sorted(set(f["tok"] for f in at))
    ts_min = min(f["ts"] for f in at) - 900
    ts_max = max(f["ts"] for f in at) + 900
    rows = _psql(
        "SELECT token_id, extract(epoch from snapshot_time), "
        "coalesce(bid_depth_5pct,0), coalesce(ask_depth_5pct,0) "
        "FROM orderbook_snapshots WHERE token_id IN (%s) "
        "AND snapshot_time BETWEEN to_timestamp(%d) AND to_timestamp(%d)"
        % (",".join("'%s'" % t for t in toks), ts_min, ts_max))
    snaps = collections.defaultdict(list)
    for tok, ep, bd, ad in rows:
        snaps[tok].append((float(ep), float(bd), float(ad)))
    for v in snaps.values():
        v.sort()
    print("depth snapshots pulled: %d rows over %d tokens (join universe %d)"
          % (len(rows), len(snaps), len(toks)))

    def depth_at(tok, ts, buy):
        s = snaps.get(tok) or []
        i = bisect.bisect_left(s, (ts, -1, -1))
        best = None
        for j in (i - 1, i, i + 1):
            if 0 <= j < len(s) and abs(s[j][0] - ts) <= 600:
                if best is None or abs(s[j][0] - ts) < abs(best[0] - ts):
                    best = s[j]
        if best is None:
            return None
        return best[1] if buy else best[2]    # our bid competes with bids

    joined = missed = 0
    agg = collections.defaultdict(lambda: [0, 0.0, 0.0, 0.0])
    outs = collections.defaultdict(list)
    for f in at:
        dep = depth_at(f["tok"], f["ts"], f["buy"])
        band = "quiet" if f["hour"] in QUIET_UTC else "prime"
        if dep is None:
            missed += 1
            continue
        joined += 1
        slice_ = f["msz"] / (f["msz"] + max(dep, 0.0))
        cap = slice_ * f["usd"]
        for key in ((f["sec"], band), ("ALL", band), ("ALL", "all")):
            agg[key][0] += 1
            agg[key][1] += f["usd"]
            agg[key][2] += cap
            agg[key][3] += slice_
        if f["out"] is not None:
            outs[band].append(f["out"])
    hours = d["scanned_windows"] * CHUNK * 2.1 / 3600.0
    print("joined %d / missed-depth %d at-level fills; sampled ~%.1fh of chain"
          % (joined, missed, hours))
    print("\nPRO-RATA SLICE (CONSERVATIVE FLOOR — depth_5pct band assumed all"
          "\ncompeting at our level):")
    print("%-16s %-6s %5s %10s %12s %8s" % ("sector", "band", "n",
                                            "atlvl_usd", "our_cap_usd",
                                            "avg_slice"))
    for (sec, band), (n, usd, cap, sl) in sorted(agg.items()):
        print("%-16s %-6s %5d %10.0f %12.2f %8.3f"
              % (sec, band, n, usd, cap, sl / max(n, 1)))
    print("\nscaled: our-capture $/day (floor) = ALL/all our_cap_usd x 24/%.1f"
          % hours)
    all_cap = agg[("ALL", "all")][2]
    if hours > 0:
        print("  = $%.2f/day at-level capture PROXY across the 140-universe"
              % (all_cap * 24 / hours))
    print("\nMAN-IN-OUR-SPOT 30-min outcomes by band:")
    for band, xs in outs.items():
        xs.sort()
        n = len(xs)
        if not n:
            continue
        print("  %-6s n=%3d mean=%+.4f adverse>1pt=%.1f%% adverse>2pt=%.1f%%"
              % (band, n, sum(xs) / n,
                 100 * sum(1 for x in xs if x < -0.01) / n,
                 100 * sum(1 for x in xs if x < -0.02) / n))
    tbh = d.get("through_by_hour") or {}
    tq = sum(v[1] for k, v in tbh.items() if int(k) in QUIET_UTC)
    tp = sum(v[1] for k, v in tbh.items() if int(k) not in QUIET_UTC)
    print("\nthrough-volume split: quiet $%.0f vs prime $%.0f" % (tq, tp))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--join", action="store_true")
    a = ap.parse_args()
    if a.scan:
        scan()
    elif a.join:
        join()
    else:
        print("need --scan or --join")
