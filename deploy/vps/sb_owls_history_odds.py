#!/usr/bin/env python3
"""Sports-bot (SB): Owls Insight per-event HISTORY ODDS harvester (Phase 2, 2026-07-17).

Walks the Phase-1 index files (owls_history_index_{sport}.jsonl), and for every
unique event with oddsSnapshots > 0 pulls the full odds-snapshot history via
/api/v1/history/odds?eventId=…  Snapshots are multi-book US lines (draftkings,
caesars, betmgm, stations, …) across h2h/totals/spreads with recordedAt
timestamps (probed 2026-07-17) — the actual back data this bot exists to store.

Measured endpoint behavior (2026-07-17):
  - limit clamps at 5000/page (~16s, ~580KB); a 25.7k-snapshot game ≈ 6 pages.
  - market=/opening= filters are pathologically SLOW (95-100s) — never use
    them; pull raw pages.
  - eventIds contain spaces/@/parens — ALWAYS urlencoded.

Storage: one JSONL line per fetched page, gzip-appended per sport:
    owls_history_odds_{sport}.jsonl.gz
    {"eventId", "sport", "gameDate", "offset", "n", "fetched_at", "snapshots":[…]}
(appended gzip members are valid gzip; consumers dedupe on eventId+offset).

Resumable: completed eventIds appended to sb_history_odds.done (one per line);
rerun skips them. Order: newest gameDate first (PM-overlap era ~Feb-2026+ is
the valuable head; the tail can trickle in over weeks).

Budget: shared MVP key ~300k req/mo; EB's recorder needs ~6k/mo. Default
QUOTA_FLOOR=25000 keeps EB 4x headroom; harvester exits cleanly at the floor
and resumes next run/month. Optional SB_MAX_EVENTS caps a single run.

Key: /home/ubuntu/.eb_owls_key (0600, NEVER in git/chat).
Run detached:
  nohup python3 sb_owls_history_odds.py >> sb_history_odds.log 2>&1 &
"""
import glob, gzip, json, os, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

KEYFILE = os.environ.get("OWLS_KEY_PATH", "/home/ubuntu/.eb_owls_key")
OUTDIR = os.environ.get("SB_OUT_DIR", "/home/ubuntu/sports-odds")
DONE = os.path.join(OUTDIR, "sb_history_odds.done")
BASE = "https://api.owlsinsight.com/api/v1/history/odds"
PAGE_LIMIT = 5000
QUOTA_FLOOR = int(os.environ.get("SB_QUOTA_FLOOR", "25000"))
MAX_EVENTS = int(os.environ.get("SB_MAX_EVENTS", "0"))  # 0 = no cap
SLEEP_BETWEEN = 1.0


def key():
    k = open(KEYFILE, encoding="utf-8").read().strip()
    if not k:
        raise SystemExit("empty key file " + KEYFILE)
    return k


def load_targets():
    """Unique events with odds, newest gameDate first: [(eventId, sport, gameDate, nSnaps)]."""
    seen = {}
    for path in sorted(glob.glob(os.path.join(OUTDIR, "owls_history_index_*.jsonl"))):
        sport = os.path.basename(path)[len("owls_history_index_"):-len(".jsonl")]
        with open(path, encoding="utf-8") as f:
            for ln in f:
                try:
                    g = json.loads(ln)
                except Exception:
                    continue
                eid = g.get("eventId")
                n = g.get("oddsSnapshots") or 0
                if not eid or n <= 0:
                    continue
                # index dupes possible (moving-head walk) — last wins, same data
                seen[eid] = (sport, g.get("gameDate") or "", n)
    targets = [(eid, s, d, n) for eid, (s, d, n) in seen.items()]
    targets.sort(key=lambda t: t[2], reverse=True)
    return targets


def fetch_page(k, eid, off):
    url = (BASE + "?eventId=" + urllib.parse.quote(eid, safe="")
           + f"&limit={PAGE_LIMIT}&offset={off}")
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + k, "User-Agent": "sb-owls/1.0",
        "Accept": "application/json"})
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                rem = r.headers.get("X-Ratelimit-Remaining-Month")
                d = json.load(r)
                data = d.get("data")
                if d.get("success") and isinstance(data, dict):
                    return data.get("snapshots") or [], rem
                return None, rem
        except Exception as e:
            print(f"{eid} off={off} attempt={attempt} "
                  f"{type(e).__name__}: {e}", flush=True)
            time.sleep(10 * attempt)
    return None, None


def main():
    k = key()
    os.makedirs(OUTDIR, exist_ok=True)
    done = set()
    if os.path.exists(DONE):
        done = {ln.strip() for ln in open(DONE, encoding="utf-8") if ln.strip()}
    targets = [t for t in load_targets() if t[0] not in done]
    print(f"{datetime.now(timezone.utc).isoformat()} targets={len(targets)} "
          f"(done so far: {len(done)})", flush=True)
    t0 = time.monotonic()
    n_events = 0
    n_req = 0
    for eid, sport, gdate, n_expected in targets:
        out = os.path.join(OUTDIR, f"owls_history_odds_{sport}.jsonl.gz")
        off = 0
        pages = []
        while True:
            snaps, rem = fetch_page(k, eid, off)
            n_req += 1
            if rem is not None and int(rem) < QUOTA_FLOOR:
                print(f"QUOTA FLOOR hit (rem={rem} < {QUOTA_FLOOR}) — "
                      f"exiting cleanly; rerun to resume", flush=True)
                return 2
            if snaps is None:
                # event failed — do NOT mark done; move on, rerun retries it
                print(f"EVENT FAILED {eid} at off={off}", flush=True)
                pages = None
                break
            pages.append((off, snaps))
            off += len(snaps)
            if len(snaps) < PAGE_LIMIT:
                break
            time.sleep(SLEEP_BETWEEN)
        if pages is None:
            time.sleep(SLEEP_BETWEEN)
            continue
        fetched_at = datetime.now(timezone.utc).isoformat()
        with gzip.open(out, "at", encoding="utf-8") as f:
            for poff, snaps in pages:
                f.write(json.dumps({
                    "eventId": eid, "sport": sport, "gameDate": gdate,
                    "offset": poff, "n": len(snaps), "fetched_at": fetched_at,
                    "snapshots": snaps}, ensure_ascii=False) + "\n")
        with open(DONE, "a", encoding="utf-8") as f:
            f.write(eid + "\n")
        n_events += 1
        if n_events % 25 == 0:
            rate = n_events / max(time.monotonic() - t0, 1) * 3600
            print(f"{datetime.now(timezone.utc).isoformat()} events={n_events}"
                  f"/{len(targets)} req={n_req} ~{rate:.0f} ev/h", flush=True)
        if MAX_EVENTS and n_events >= MAX_EVENTS:
            print(f"SB_MAX_EVENTS={MAX_EVENTS} reached — exiting cleanly", flush=True)
            return 0
        time.sleep(SLEEP_BETWEEN)
    print(f"ALL DONE events={n_events} req={n_req} "
          f"dur={time.monotonic()-t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
