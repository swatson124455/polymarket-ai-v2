#!/usr/bin/env python3
"""esports_silo — does pinnodds expose HISTORICAL odds we could backfill the calibrator from?

The /kit/v1/markets calls are live/prematch snapshots (no depth). This probe settles whether
there is any archive/history surface on THIS plan, empirically:
  1. pull /llms-full.txt and print every endpoint + every line mentioning
     history/archive/snapshot/drops/retention/backfill;
  2. hit /api/drops (the queryable drops buffer named in the docs) for esports and show its
     shape + how far back its timestamps reach;
  3. try a few candidate history paths and report status codes (no guessing — we read what
     comes back).

USAGE (on the box — network; the silo has none):
    PINNODDS_KEY=your_key python3 esports_silo/scripts/probe_pinnodds_history.py

Read-only. Rotate the key after (shared in chat).
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

KEY = os.environ.get("PINNODDS_KEY") or (sys.argv[1] if len(sys.argv) > 1 else "")
if not KEY:
    sys.exit("usage: PINNODDS_KEY=xxxx python3 esports_silo/scripts/probe_pinnodds_history.py")

ROOT = "https://pinnodds.com"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
HIST_RE = re.compile(r"history|historical|archive|snapshot|retention|backfill|days? back|"
                     r"since|from=|/drops|time.?range|closing", re.I)
ENDPOINT_RE = re.compile(r"^\s*#{1,4}\s|GET\s+/|POST\s+/|`/(kit|api)/", re.I)


def get(url, backoff=True):
    for wait in (5, 15, 30, 0):
        req = urllib.request.Request(url, headers={
            "x-portal-apikey": KEY, "accept": "application/json,text/plain,*/*", "user-agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code == 429 and backoff and wait:
                print(f"    429 — backoff {wait}s"); time.sleep(wait); continue
            return e.code, body[:400]
        except Exception as e:  # noqa: BLE001
            return None, f"ERR {e}"
    return 429, "throttled"


# 1) reference doc — endpoints + history mentions
print("===== /llms-full.txt — endpoints & history mentions =====")
code, body = get(ROOT + "/llms-full.txt", backoff=False)
print(f"HTTP {code}, {len(body)} bytes")
if code == 200:
    lines = body.splitlines()
    print("\n-- endpoint / section headings --")
    for ln in lines:
        if ENDPOINT_RE.search(ln):
            print("  " + ln.strip()[:150])
    print("\n-- lines mentioning history/archive/drops/retention --")
    shown = 0
    for ln in lines:
        if HIST_RE.search(ln) and not ENDPOINT_RE.search(ln):
            print("  | " + ln.strip()[:160]); shown += 1
            if shown >= 50:
                print("  ... (truncated)"); break

# 2) the drops buffer — how far back does it reach?
for q in ("/api/drops?sport_id=11", "/api/drops?mode=prematch&sport_id=11", "/api/drops"):
    print(f"\n===== GET {q} =====")
    code, body = get(ROOT + q)
    print(f"HTTP {code}")
    if code == 200:
        try:
            d = json.loads(body)
            print(json.dumps(d, indent=1)[:1500])
        except Exception:
            print(body[:600])
    else:
        print(body[:200])
    time.sleep(3)

# 3) candidate history paths (report whatever status they give)
print("\n===== candidate history endpoints (status only) =====")
for path in ("/kit/v1/history?sport_id=11", "/kit/v1/historical-odds?sport_id=11",
             "/kit/v1/markets/history?sport_id=11", "/kit/v1/prematch/history?sport_id=11",
             "/api/history?sport_id=11"):
    code, _ = get(ROOT + path)
    print(f"  {code}  {path}")
    time.sleep(2)

print("\nPaste this whole output back — I need to see if ANY surface gives odds older than 'now'.")
