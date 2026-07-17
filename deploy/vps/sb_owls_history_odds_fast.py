#!/usr/bin/env python3
"""Sports-bot (SB): PARALLEL per-event history-odds harvester (deadline race, 2026-07-17).

WHY this exists alongside the serial sb_owls_history_odds.py: the Owls Insight
sub is being cancelled in ~2-3 days, so the leisurely serial crawl (days-weeks)
can't finish. This version runs N threaded workers against a shared,
value-ordered queue to grab the highest-value odds first before access ends.

Priority order (operator: "recent + major leagues first"): gameDate DESC, then
oddsSnapshots DESC. The index has no league field, so snapshot-count is the
best available proxy for a major/liquid game. Highest-value events are fetched
first; if the sub closes mid-run, the lost tail is the oldest/thinnest games.

Runs against the CURRENT index (does NOT wait for Phase 1 to finish). Loops:
after draining the queue it reloads the index to pick up games the still-running
index lanes have since added, and exits when a reload yields nothing new.

Safety (matches the serial version):
  - Full event (all pages) written to its per-sport gz shard BEFORE the eventId
    is marked done → resumable, no half-events marked complete.
  - Shared QUOTA_FLOOR (default 25000): the first worker to see remaining-month
    below it trips a stop Event; all workers finish their current write and
    exit cleanly. EB's ~6k/mo recorder is never starved.
  - Per-call 180s timeout; 3 retries with backoff; a failed event is NOT marked
    done (a rerun retries it).

Output: owls_history_odds_{sport}.jsonl.gz (same schema/dir as the serial
version — dedupe on eventId+offset). done file: sb_history_odds.done (SHARED
with the serial version; either can resume the other's work).

Env: SB_WORKERS (default 12), SB_QUOTA_FLOOR (25000), SB_OUT_DIR, SB_MAX_EVENTS.
Run detached:
  nohup python3 sb_owls_history_odds_fast.py >> sb_history_odds_fast.log 2>&1 &
"""
import glob, gzip, json, os, threading, time
import urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

KEYFILE = os.environ.get("OWLS_KEY_PATH", "/home/ubuntu/.eb_owls_key")
OUTDIR = os.environ.get("SB_OUT_DIR", "/home/ubuntu/sports-odds")
DONE = os.path.join(OUTDIR, "sb_history_odds.done")
BASE = "https://api.owlsinsight.com/api/v1/history/odds"
PAGE_LIMIT = 5000
WORKERS = int(os.environ.get("SB_WORKERS", "8"))  # 8 = clean (0 429); 20+ trips the concurrency cap
QUOTA_FLOOR = int(os.environ.get("SB_QUOTA_FLOOR", "25000"))
MAX_EVENTS = int(os.environ.get("SB_MAX_EVENTS", "0"))  # 0 = no cap
# Some events have MILLIONS of snapshots (soccer "special bets" 3.9M, Bosnia@
# Canada 3.0M) = ~800 pages = HOURS each — they'd wedge every worker. Cap pages
# per event; for oversized events grab the TAIL (closing-line window, the
# valuable part), not the opening. 6 pages = 30k snapshots.
MAX_PAGES = int(os.environ.get("SB_MAX_PAGES", "6"))
_CAP_SNAPS = MAX_PAGES * PAGE_LIMIT
TRUNC_LOG = os.path.join(OUTDIR, "sb_history_odds.truncated")

_stop = threading.Event()          # tripped at quota floor
_write_lock = threading.Lock()     # guards gz handles + done file + counters
_throttle_lock = threading.Lock()  # paces request STARTS to avoid burst-429s
_last_req = [0.0]
MIN_INTERVAL = float(os.environ.get("SB_MIN_INTERVAL", "0.17"))  # ~350/min < 400 cap
_gz = {}                           # sport -> open gzip handle
_done_local = set()                # eventIds finished this process (dedupe)
_n_events = 0
_n_req = 0
_rem_seen = None


def key():
    k = open(KEYFILE, encoding="utf-8").read().strip()
    if not k:
        raise SystemExit("empty key file " + KEYFILE)
    return k


def load_targets():
    """Unique odds-bearing events, value-ordered (recent first, then snapshots).
    Returns [(eventId, sport, gameDate, nSnaps)]."""
    seen = {}
    for path in sorted(glob.glob(os.path.join(OUTDIR, "owls_history_index_*.jsonl"))):
        sport = os.path.basename(path)[len("owls_history_index_"):-len(".jsonl")]
        try:
            fh = open(path, encoding="utf-8")
        except OSError:
            continue
        with fh:
            for ln in fh:
                try:
                    g = json.loads(ln)
                except Exception:
                    continue
                eid = g.get("eventId")
                n = g.get("oddsSnapshots") or 0
                if not eid or n <= 0:
                    continue
                seen[eid] = (sport, g.get("gameDate") or "", n)
    targets = [(eid, s, d, n) for eid, (s, d, n) in seen.items()]
    # Order: FULLY-capturable events (<= _CAP_SNAPS) first, by oddsSnapshots DESC
    # (liquid/major-game proxy — no league field; major games carry far more
    # snapshots), tie-break gameDate DESC. Then the mega-outliers (> _CAP_SNAPS,
    # millions of snapshots) LAST — they only get a partial tail-capture, so they
    # must not delay the bulk of real major games. The whole archive is the
    # Feb–Jul-2026 era so everything is already "recent"; snapshot-count is what
    # separates a Polymarket-relevant game from the obscure lower-league tail.
    normal = [t for t in targets if not t[3] or t[3] <= _CAP_SNAPS]
    mega = [t for t in targets if t[3] and t[3] > _CAP_SNAPS]
    normal.sort(key=lambda t: (t[3], t[2]), reverse=True)
    mega.sort(key=lambda t: (t[3], t[2]), reverse=True)
    return normal + mega


def load_done():
    if os.path.exists(DONE):
        return {ln.strip() for ln in open(DONE, encoding="utf-8") if ln.strip()}
    return set()


def gz_handle(sport):
    h = _gz.get(sport)
    if h is None:
        h = gzip.open(os.path.join(OUTDIR, f"owls_history_odds_{sport}.jsonl.gz"),
                      "at", encoding="utf-8")
        _gz[sport] = h
    return h


def _throttle():
    """Space request STARTS >= MIN_INTERVAL apart across all threads, so a
    cluster of returning workers can't burst past the per-second rate cap."""
    with _throttle_lock:
        now = time.monotonic()
        wait = _last_req[0] + MIN_INTERVAL - now
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _last_req[0] = now


def fetch_page(k, eid, off):
    url = (BASE + "?eventId=" + urllib.parse.quote(eid, safe="")
           + f"&limit={PAGE_LIMIT}&offset={off}")
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + k, "User-Agent": "sb-owls/1.0",
        "Accept": "application/json"})
    # Generous attempt budget: 429s are throttle-not-fatal, honor Retry-After.
    for attempt in range(1, 9):
        if _stop.is_set():
            return None, None
        _throttle()
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                rem = r.headers.get("X-Ratelimit-Remaining-Month")
                d = json.load(r)
                data = d.get("data")
                if d.get("success") and isinstance(data, dict):
                    return data.get("snapshots") or [], rem
                return None, rem
        except urllib.error.HTTPError as e:
            if e.code == 429:
                ra = e.headers.get("Retry-After")
                try:
                    delay = int(ra) if ra else 3
                except (TypeError, ValueError):
                    delay = 3
                time.sleep(min(max(delay, 1), 20))
                continue                     # honor Retry-After; bounded by the 8-attempt loop
            if attempt >= 8:
                print(f"{eid} off={off} FAIL HTTP {e.code}", flush=True)
            else:
                time.sleep(5 * attempt)
        except Exception as e:
            if attempt >= 8:
                print(f"{eid} off={off} FAIL {type(e).__name__}: {e}", flush=True)
            else:
                time.sleep(5 * attempt)
    return None, None


def handle_event(k, target):
    global _n_events, _n_req, _rem_seen
    if _stop.is_set():
        return
    eid, sport, gdate, nsnap = target
    # Oversized event: start at the TAIL so we capture the closing-line window
    # (most recent MAX_PAGES pages) rather than the opening. Snapshots are
    # returned oldest-first, so a high start offset = the latest snapshots.
    truncated = nsnap and nsnap > _CAP_SNAPS
    start_off = (((nsnap - _CAP_SNAPS) // PAGE_LIMIT) * PAGE_LIMIT) if truncated else 0
    off = start_off
    pages = []
    for _ in range(MAX_PAGES):
        if _stop.is_set():
            return
        snaps, rem = fetch_page(k, eid, off)
        with _write_lock:
            _n_req += 1
            if rem is not None:
                _rem_seen = rem
        if rem is not None and int(rem) < QUOTA_FLOOR:
            _stop.set()
            print(f"QUOTA FLOOR (rem={rem} < {QUOTA_FLOOR}) — stopping all "
                  f"workers cleanly", flush=True)
            return
        if snaps is None:
            return                       # failed — do not mark done
        pages.append((off, snaps))
        off += len(snaps)
        if len(snaps) < PAGE_LIMIT:
            break
    fetched_at = datetime.now(timezone.utc).isoformat()
    with _write_lock:
        h = gz_handle(sport)
        for poff, snaps in pages:
            h.write(json.dumps({
                "eventId": eid, "sport": sport, "gameDate": gdate,
                "offset": poff, "n": len(snaps), "fetched_at": fetched_at,
                "truncated": bool(truncated), "index_snapshots": nsnap,
                "snapshots": snaps}, ensure_ascii=False) + "\n")
        h.flush()
        with open(DONE, "a", encoding="utf-8") as f:
            f.write(eid + "\n")
        if truncated:
            with open(TRUNC_LOG, "a", encoding="utf-8") as f:
                f.write(f"{eid}\t{nsnap}\tcaptured_tail_{MAX_PAGES}p_from_{start_off}\n")
        _done_local.add(eid)
        _n_events += 1
        if _n_events % 50 == 0:
            print(f"{fetched_at} events={_n_events} req={_n_req} "
                  f"rem={_rem_seen}", flush=True)
        if MAX_EVENTS and _n_events >= MAX_EVENTS:
            _stop.set()


def main():
    k = key()
    os.makedirs(OUTDIR, exist_ok=True)
    t0 = time.monotonic()
    rounds = 0
    while not _stop.is_set():
        rounds += 1
        done = load_done() | _done_local
        targets = [t for t in load_targets() if t[0] not in done]
        if not targets:
            print(f"round {rounds}: no new targets — done", flush=True)
            break
        print(f"{datetime.now(timezone.utc).isoformat()} round {rounds}: "
              f"{len(targets)} targets, {WORKERS} workers "
              f"(done so far {len(done)})", flush=True)
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for t in targets:
                ex.submit(handle_event, k, t)
        # executor context waits for all submitted tasks
    for h in _gz.values():
        try:
            h.close()
        except Exception:
            pass
    print(f"EXIT events={_n_events} req={_n_req} rounds={rounds} "
          f"rem={_rem_seen} dur={time.monotonic()-t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
