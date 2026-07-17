#!/usr/bin/env python3
"""Sports-bot (SB): Owls Insight ALL-SPORTS history GAMES INDEX harvester (2026-07-17).

Pulls the full archived-games index for every traditional sport Owls Insight
covers (probed 2026-07-17: soccer 157k, tennis 78k, mlb 22k, nba 14k, nhl 2.3k,
nfl 37 — cs2 is EB's, untouched). Index rows include eventId, teams, gameDate,
FINAL + PERIOD SCORES, and oddsSnapshots/propsSnapshots counts — so this file
is both a results archive and the shopping list for the per-event odds harvest
(Phase 2): only rows with oddsSnapshots > 0 are worth the slow odds calls.

Design:
  - Month windows (startDate/endDate, verified supported) instead of one big
    offset walk: a fixed PAST window is a static set, so offset paging inside
    it can't skip rows while new games are being archived at the head.
  - Resumable: completed sport-months recorded in .done file; a partial month
    restarts at offset 0 (duplicate rows possible — consumers dedupe on
    eventId; append-only file never rewritten).
  - Quota floor: aborts if X-Ratelimit-Remaining-Month drops below QUOTA_FLOOR
    (default 290000) so a bug here can never drain the shared MVP budget
    (~300k/mo; EB's recorder needs ~6k/mo).
  - ~1 req/s, HTTP/1.1 via urllib (API hangs on HTTP/2 — probed 2026-07-17).

Key: /home/ubuntu/.eb_owls_key (0600, NEVER in git/chat).
Run detached:
  nohup python3 sb_owls_history_index.py >> sb_history_index.log 2>&1 &
"""
import json, os, time, urllib.request, urllib.error
from datetime import datetime, timezone

KEYFILE = os.environ.get("OWLS_KEY_PATH", "/home/ubuntu/.eb_owls_key")
OUTDIR = os.environ.get("SB_OUT_DIR", "/home/ubuntu/sports-odds")
DONE = os.path.join(OUTDIR, "sb_history_index.done")
BASE = "https://api.owlsinsight.com/api/v1/history/games"
LIMIT = 100
QUOTA_FLOOR = int(os.environ.get("SB_QUOTA_FLOOR", "290000"))
SPORTS = ("nfl", "nhl", "nba", "mlb", "tennis", "soccer")  # smallest first
START_YM = (2023, 1)  # archive depth unknown; empty months cost 1 req each


def key():
    k = open(KEYFILE, encoding="utf-8").read().strip()
    if not k:
        raise SystemExit("empty key file " + KEYFILE)
    return k


def month_windows():
    """Yield (\"YYYY-MM-DD\", \"YYYY-MM-DD\") first..last day, START_YM..now."""
    now = datetime.now(timezone.utc)
    y, m = START_YM
    while (y, m) <= (now.year, now.month):
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        # endDate = first day of next month; observed filter is inclusive by
        # gameDate day, so overlap dupes at the boundary are fine (dedupe key
        # is eventId).
        yield f"{y:04d}-{m:02d}-01", f"{ny:04d}-{nm:02d}-01"
        y, m = ny, nm


def page(k, sport, start, end, off):
    url = (f"{BASE}?sport={sport}&startDate={start}&endDate={end}"
           f"&limit={LIMIT}&offset={off}")
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + k, "User-Agent": "sb-owls/1.0",
        "Accept": "application/json"})
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                rem = r.headers.get("X-Ratelimit-Remaining-Month")
                d = json.load(r)
                if d.get("success") and isinstance(d.get("data"), dict):
                    return d["data"], rem
                return None, rem
        except Exception as e:
            print(f"{sport} {start} off={off} attempt={attempt} "
                  f"{type(e).__name__}: {e}", flush=True)
            time.sleep(5 * attempt)
    return None, None


def main():
    k = key()
    os.makedirs(OUTDIR, exist_ok=True)
    done = set()
    if os.path.exists(DONE):
        done = {ln.strip() for ln in open(DONE, encoding="utf-8") if ln.strip()}
        print(f"resuming: {len(done)} sport-months already done", flush=True)
    t0 = time.monotonic()
    n_req = 0
    for sport in SPORTS:
        out = os.path.join(OUTDIR, f"owls_history_index_{sport}.jsonl")
        sport_rows = 0
        for start, end in month_windows():
            tag = f"{sport}:{start}"
            if tag in done:
                continue
            off = 0
            while True:
                d, rem = page(k, sport, start, end, off)
                n_req += 1
                if rem is not None and int(rem) < QUOTA_FLOOR:
                    print(f"QUOTA FLOOR hit (rem={rem} < {QUOTA_FLOOR}) — "
                          f"aborting cleanly; rerun to resume", flush=True)
                    return 2
                if d is None:
                    print(f"GAVE UP {tag} off={off} — rerun to resume", flush=True)
                    return 1
                games = d.get("games") or []
                pag = d.get("pagination") or {}
                with open(out, "a", encoding="utf-8") as f:
                    for g in games:
                        f.write(json.dumps(g, ensure_ascii=False) + "\n")
                off += len(games)
                sport_rows += len(games)
                if not pag.get("hasMore") or not games:
                    break
                time.sleep(1)
            with open(DONE, "a", encoding="utf-8") as f:
                f.write(tag + "\n")
            if off:
                print(f"{datetime.now(timezone.utc).isoformat()} {tag} "
                      f"games={off} total_{sport}={sport_rows} req={n_req} "
                      f"rem={rem} dur={time.monotonic()-t0:.0f}s", flush=True)
            time.sleep(1)
        print(f"SPORT DONE {sport}: {sport_rows} rows -> {out}", flush=True)
    print(f"ALL DONE req={n_req} dur={time.monotonic()-t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
