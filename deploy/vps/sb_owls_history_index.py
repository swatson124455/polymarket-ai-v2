#!/usr/bin/env python3
"""Sports-bot (SB): Owls Insight ALL-SPORTS history GAMES INDEX harvester (2026-07-17).

Pulls the full archived-games index for every traditional sport Owls Insight
covers (probed 2026-07-17: soccer 157k, tennis 78k, mlb 22k, nba 14k, nhl 2.3k,
nfl 37 — cs2 is EB's, untouched). Index rows include eventId, teams, gameDate,
FINAL + PERIOD SCORES, and oddsSnapshots/propsSnapshots counts — so this file
is both a results archive and the shopping list for the per-event odds harvest
(Phase 2): only rows with oddsSnapshots > 0 are worth the slow odds calls.

Design (v2 — offset walk; the v1 month-window design is DEAD, do not revive):
  - Plain offset paging per sport, newest-first as served. Date-filtered
    queries (startDate/endDate) TIME OUT (120s+) whenever the window is
    EMPTY — measured 2026-07-17, same pathology as the broken closing-odds
    endpoint — so windowed scanning of unknown archive depth is unusable.
  - limit=100 is the page cap (500 times out, 1000 clamps to 100; measured).
  - Moving-head caveat: new games are archived at offset 0 during the walk,
    shifting rows across page boundaries → duplicates (harmless) and rare
    skips. Mitigation: after the main walk each sport gets a TOP-UP sweep of
    the first TOPUP_PAGES pages; consumers must dedupe on eventId.
  - Resumable: per-sport offset in .state JSON; rerun continues mid-sport.
  - Quota floor: aborts if X-Ratelimit-Remaining-Month < QUOTA_FLOOR
    (default 290000) so a bug here can never drain the shared MVP budget
    (~300k/mo; EB's recorder needs ~6k/mo).
  - ~1 req/1.5s, HTTP/1.1 via urllib (API hangs on HTTP/2 — probed 2026-07-17).

Key: /home/ubuntu/.eb_owls_key (0600, NEVER in git/chat).
Run detached:
  nohup python3 sb_owls_history_index.py >> sb_history_index.log 2>&1 &
"""
import json, os, time, urllib.request, urllib.error
from datetime import datetime, timezone

KEYFILE = os.environ.get("OWLS_KEY_PATH", "/home/ubuntu/.eb_owls_key")
OUTDIR = os.environ.get("SB_OUT_DIR", "/home/ubuntu/sports-odds")
# SB_STATE + SB_SPORTS let independent lanes run in PARALLEL (one process per
# lane, each with its OWN state file — the shared-file save_state() would race
# across processes). History queries are server-latency-bound (~intermittent
# 120s stalls measured), so parallel lanes cut wall-clock materially.
STATE = os.environ.get("SB_STATE", os.path.join(OUTDIR, "sb_history_index.state"))
BASE = "https://api.owlsinsight.com/api/v1/history/games"
LIMIT = 100
QUOTA_FLOOR = int(os.environ.get("SB_QUOTA_FLOOR", "290000"))
SPORTS = tuple(s.strip() for s in os.environ.get(
    "SB_SPORTS", "nfl,nhl,nba,mlb,tennis,soccer").split(",") if s.strip())
TOPUP_PAGES = 5  # re-sweep of the head after the main walk (moving-head skips)


def key():
    k = open(KEYFILE, encoding="utf-8").read().strip()
    if not k:
        raise SystemExit("empty key file " + KEYFILE)
    return k


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(tmp, STATE)


def page(k, sport, off):
    url = f"{BASE}?sport={sport}&limit={LIMIT}&offset={off}"
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
            print(f"{sport} off={off} attempt={attempt} "
                  f"{type(e).__name__}: {e}", flush=True)
            time.sleep(5 * attempt)
    return None, None


def walk(k, sport, start_off, max_pages=None):
    """Walk pages from start_off; append rows; return (rows, final_off, ok)."""
    out = os.path.join(OUTDIR, f"owls_history_index_{sport}.jsonl")
    st = load_state()
    off = start_off
    rows = 0
    pages = 0
    t0 = time.monotonic()
    while True:
        d, rem = page(k, sport, off)
        if rem is not None and int(rem) < QUOTA_FLOOR:
            print(f"QUOTA FLOOR hit (rem={rem} < {QUOTA_FLOOR}) — aborting "
                  f"cleanly; rerun to resume", flush=True)
            return rows, off, False
        if d is None:
            print(f"GAVE UP {sport} off={off} — rerun to resume", flush=True)
            return rows, off, False
        games = d.get("games") or []
        pag = d.get("pagination") or {}
        with open(out, "a", encoding="utf-8") as f:
            for g in games:
                f.write(json.dumps(g, ensure_ascii=False) + "\n")
        off += len(games)
        rows += len(games)
        pages += 1
        st[sport] = off
        save_state(st)
        if pages % 50 == 0 or not pag.get("hasMore"):
            print(f"{datetime.now(timezone.utc).isoformat()} {sport} "
                  f"off={off}/{pag.get('total')} rem={rem} "
                  f"dur={time.monotonic()-t0:.0f}s", flush=True)
        if not pag.get("hasMore") or not games:
            return rows, off, True
        if max_pages and pages >= max_pages:
            return rows, off, True
        time.sleep(1.5)


def main():
    k = key()
    os.makedirs(OUTDIR, exist_ok=True)
    st = load_state()
    for sport in SPORTS:
        done_key = sport + ":done"
        if st.get(done_key):
            continue
        start_off = int(st.get(sport, 0))
        rows, off, ok = walk(k, sport, start_off)
        if not ok:
            return 1
        st = load_state()
        st[done_key] = True
        save_state(st)
        print(f"SPORT DONE {sport}: +{rows} rows (walk end off={off})", flush=True)
    # top-up sweep: head pages again (dupes fine; closes moving-head skips)
    for sport in SPORTS:
        rows, off, ok = walk(k, sport, 0, max_pages=TOPUP_PAGES)
        if not ok:
            return 1
        print(f"TOPUP DONE {sport}: +{rows} head rows", flush=True)
    print("ALL DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
