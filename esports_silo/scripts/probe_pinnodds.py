#!/usr/bin/env python3
"""esports_silo — throwaway probe of the pinnodds (Pinnacle wrapper) API.

Two facts learned the hard way:
  * /sports and /leagues 404; /markets?sport_id=N&event_type=prematch|live works.
  * ids 1..10 are conventional sports (Soccer..Boxing, "Other"=10); esports is a
    HIGHER id, but the API 429s after ~20 requests, so a blind 1..40 sweep never
    reaches it. The default Python-urllib UA is also blocked (use a browser UA).

Strategy: (1) pull the vendor reference (/llms-full.txt etc.) in ONE request — it
carries the authoritative sport_id map + response schema; (2) a GENTLE, back-off
probe of a few candidate esports ids (prematch only) so we don't trip the 429.

USAGE (on the box — it has network; the silo does not):
    PINNODDS_KEY=your_key python3 esports_silo/scripts/probe_pinnodds.py

Read-only. The key is only sent to pinnodds; rotate it after (shared in chat).
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
    sys.exit("usage: PINNODDS_KEY=xxxx python3 esports_silo/scripts/probe_pinnodds.py")

ROOT = "https://pinnodds.com"
BASE = ROOT + "/kit/v1"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
ESPORT_RE = re.compile(r"e-?sport|counter|cs2|cs:?go|dota|league of legends|valorant|rocket league|starcraft", re.I)
# esports is >10 and 12=Golf (seen earlier); probe a small candidate set slowly.
CANDIDATES = [11, 13, 14, 15, 16, 17, 18, 19, 20, 22, 25]


def get(url, backoff=True):
    """GET with browser UA. On 429, back off (5s,15s,30s) up to 3 tries."""
    for attempt, wait in enumerate((5, 15, 30, 0)):
        req = urllib.request.Request(url, headers={
            "x-portal-apikey": KEY, "accept": "application/json,text/plain,*/*", "user-agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code == 429 and backoff and wait:
                print(f"    429 — backing off {wait}s ...")
                time.sleep(wait)
                continue
            return e.code, body[:400]
        except Exception as e:  # noqa: BLE001
            return None, f"ERR {e}"
    return 429, "still throttled"


# ── Part 1 — vendor reference (one shot): sport_id map + schema ─────────────
print("===== reference docs (authoritative sport_id map + schema) =====")
for path in ("/llms-full.txt", "/kit/v1/llms-full.txt", "/kit/llms-full.txt",
             "/openapi.json", "/kit/v1/openapi.json"):
    code, body = get(ROOT + path, backoff=False)
    print(f"\n--- GET {ROOT + path} -> HTTP {code} ({len(body)} bytes) ---")
    if code != 200:
        continue
    # surface every line that names a sport id or an esport, then a head sample
    hits = [ln for ln in body.splitlines()
            if ESPORT_RE.search(ln) or re.search(r"sport[_ ]?id|sport_name|\bmarkets?\b", ln, re.I)]
    if hits:
        print("  lines mentioning sports/esports/markets:")
        for ln in hits[:60]:
            print("   |", ln.strip()[:160])
    print("  head:")
    print(body[:2500])
    break  # first doc that returns 200 is enough

# ── Part 2 — gentle esports market probe (prematch only, spaced) ───────────
print("\n===== gentle esports probe (prematch, spaced to dodge 429) =====")
found_esports = []
for sid in CANDIDATES:
    code, body = get(f"{BASE}/markets?sport_id={sid}&event_type=prematch")
    name, n = "?", 0
    samp = None
    if code == 200:
        try:
            d = json.loads(body)
            name = d.get("sport_name", "?")
            evs = d.get("events") or []
            n = len(evs)
            samp = evs[0] if evs else None
        except Exception:
            pass
    flag = "  <== ESPORTS" if ESPORT_RE.search(name) else ""
    print(f"  id {sid:>3}  HTTP {code}  {name:<22} prematch={n}{flag}")
    if ESPORT_RE.search(name):
        found_esports.append((sid, name, n, samp))
        if samp:
            print("    --- sample prematch event (schema) ---")
            print("    " + json.dumps(samp, indent=1)[:2400].replace("\n", "\n    "))
    time.sleep(4)  # stay under the rate limit

print("\n===== summary =====")
if found_esports:
    for sid, name, n, _ in found_esports:
        print(f"  esports id {sid} = {name} (prematch events now: {n})")
else:
    print("  no esports sport_name in the candidate set — paste the docs section above;")
    print("  the sport_id map there tells us the exact id, and we probe just that one.")
print("\nPaste this whole output back.")
