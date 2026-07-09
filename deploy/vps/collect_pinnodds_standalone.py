#!/usr/bin/env python3
"""Standalone PinnOdds forward-collector (stdlib only) — bootstrap for the VPS.

Canonical version is esports_v2/scripts/collect_pinnodds.py (+ pinnodds_loader.py)
in the repo; this self-contained copy exists so collection can run on the VPS
(non-git deploy dir) via cron without a deploy. Appends one timestamped snapshot
per esports match-winner line each run. Read-only vs PinnOdds; append-only to disk.

Resilience (2026-07-09 fix): PinnOdds' demo tier rate-limits, so a bare urlopen
429s and kills the run. _get now retries on 429 honoring Retry-After (and on
transient URLErrors with backoff), and there is a delay between the live/prematch
feeds. Record shape + correct-or-absent filtering are UNCHANGED.
"""
import json, os, re, time, urllib.request, urllib.error
from datetime import datetime, timezone

ENV_PATH  = os.environ.get("PINNODDS_ENV_PATH", "/opt/pa2-shared/.env")
SNAP_PATH = os.environ.get("PINNODDS_SNAPSHOT_PATH", "/home/ubuntu/eb-odds/pinnodds_snapshots.jsonl")
BASE = "https://pinnodds.com/kit/v1/markets?sport_id=11&event_type=%s"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
PROP = re.compile(r"\((kills?|games?|maps?|rounds?|towers?|handicap|total)\)\s*$", re.I)
REQ_DELAY = 3.0        # gap between the live and prematch feeds (rate-limit safety)
MAX_ATTEMPTS = 4
RETRY_AFTER_CAP = 60   # never sleep more than this on a single Retry-After

def read_key():
    with open(ENV_PATH, encoding="utf-8") as f:
        for line in f:
            if line.startswith("PINNACLE_ODDS_API_KEY=") and line.strip() != "PINNACLE_ODDS_API_KEY=":
                return line.split("=", 1)[1].strip()
    raise SystemExit("PINNACLE_ODDS_API_KEY not found in " + ENV_PATH)

def norm(s): return str(s or "").strip().lower()
def mkey(a, b, d):
    a, b = norm(a), norm(b); d = (d or "")[:10]
    if a > b: a, b = b, a
    return f"{a}||{b}||{d}"
def coerce(v):
    try: f = float(v)
    except (TypeError, ValueError): return None
    return f if f > 1.0 else None

def fetch(key, et):
    """GET one feed with retry. Returns parsed JSON, or {} after exhausting retries."""
    req = urllib.request.Request(
        BASE % et,
        headers={"x-portal-apikey": key, "User-Agent": UA, "Accept": "application/json"},
    )
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < MAX_ATTEMPTS:
                ra = e.headers.get("Retry-After") if e.headers else None
                try: wait = min(int(ra), RETRY_AFTER_CAP) if ra else 2 ** attempt
                except ValueError: wait = 2 ** attempt
                print(f"  429 on {et}, attempt {attempt}/{MAX_ATTEMPTS}, sleeping {wait}s")
                time.sleep(max(1, wait)); continue
            print(f"  HTTPError {e.code} on {et} (attempt {attempt}) — giving up this feed")
            return {}
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < MAX_ATTEMPTS:
                print(f"  transport error on {et} (attempt {attempt}): {e} — backoff")
                time.sleep(2 ** attempt); continue
            return {}
    return {}

def main():
    key = read_key()
    captured = datetime.now(timezone.utc).isoformat()
    recs = []
    for i, et in enumerate(("live", "prematch")):
        if i: time.sleep(REQ_DELAY)
        for ev in fetch(key, et).get("events", []):
            h, a = str(ev.get("home") or "").strip(), str(ev.get("away") or "").strip()
            if not h or not a or PROP.search(h) or PROP.search(a): continue
            p = ev.get("periods") or {}
            n0 = p.get("num_0") if isinstance(p, dict) else None
            ml = n0.get("money_line") if isinstance(n0, dict) else None
            if not isinstance(ml, dict): continue
            oa, ob = coerce(ml.get("home")), coerce(ml.get("away"))
            if oa is None or ob is None: continue
            recs.append({"captured_at": captured, "match_key": mkey(h, a, ev.get("starts")),
                         "home": h, "away": a, "starts": ev.get("starts"),
                         "league_name": ev.get("league_name"), "odds_a": oa, "odds_b": ob,
                         "event_type": et})
    os.makedirs(os.path.dirname(SNAP_PATH), exist_ok=True)
    with open(SNAP_PATH, "a", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    total = sum(1 for _ in open(SNAP_PATH, encoding="utf-8"))
    print(f"{captured} appended={len(recs)} total_lines={total} file={SNAP_PATH}")

if __name__ == "__main__":
    main()
