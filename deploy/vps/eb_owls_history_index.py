#!/usr/bin/env python3
"""One-shot pull of the Owls Insight CS2 history GAMES INDEX (EB, 2026-07-17).

Pages /api/v1/history/games?sport=cs2 (limit 100) into a JSONL of game rows
(~20.7k games ≈ 210 requests of the 300k/month MVP budget). This is the CHEAP
index — eventId, teams, gameDate, snapshot counts. The expensive per-event
odds calls (30-60s each) are NOT made here; the index feeds the offline
PM-overlap count that decides whether they're ever worth making.

Resumable: on restart, continues from the recorded offset in the .state file.
Run detached:  nohup python3 eb_owls_history_index.py >> owls_history_index.log 2>&1 &
"""
import json, os, time, urllib.request, urllib.error
from datetime import datetime, timezone

KEYFILE = os.environ.get("OWLS_KEY_PATH", "/home/ubuntu/.eb_owls_key")
OUT = os.environ.get("OWLS_HISTORY_INDEX_PATH",
                     "/home/ubuntu/eb-odds/owls_cs2_history_index.jsonl")
STATE = OUT + ".state"
BASE = "https://api.owlsinsight.com/api/v1/history/games"
LIMIT = 100
SPORT = "cs2"


def key():
    k = open(KEYFILE, encoding="utf-8").read().strip()
    if not k:
        raise SystemExit("empty key file " + KEYFILE)
    return k


def page(off, k):
    url = f"{BASE}?sport={SPORT}&limit={LIMIT}&offset={off}"
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + k, "User-Agent": "eb-owls/1.0",
        "Accept": "application/json"})
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.load(r)
                if d.get("success") and isinstance(d.get("data"), dict):
                    return d["data"]
                return None
        except Exception as e:
            print(f"offset={off} attempt={attempt} {type(e).__name__}: {e}", flush=True)
            time.sleep(5 * attempt)
    return None


def main():
    k = key()
    off = 0
    if os.path.exists(STATE):
        off = int(open(STATE).read().strip() or 0)
        print(f"resuming at offset {off}", flush=True)
    t0 = time.monotonic()
    total = None
    while True:
        d = page(off, k)
        if d is None:
            print(f"GAVE UP at offset={off} — rerun to resume", flush=True)
            return 1
        games = d.get("games") or []
        pag = d.get("pagination") or {}
        total = pag.get("total", total)
        with open(OUT, "a", encoding="utf-8") as f:
            for g in games:
                f.write(json.dumps(g, ensure_ascii=False) + "\n")
        off += len(games)
        open(STATE, "w").write(str(off))
        print(f"{datetime.now(timezone.utc).isoformat()} offset={off}/{total} "
              f"dur={time.monotonic()-t0:.0f}s", flush=True)
        if not pag.get("hasMore") or not games:
            break
        time.sleep(1)   # gentle: ~1 req/s max
    print(f"DONE: {off} games -> {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
