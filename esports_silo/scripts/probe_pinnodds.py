#!/usr/bin/env python3
"""esports_silo — throwaway probe of the pinnodds (Pinnacle wrapper) API.

/sports 404s and their sport_id map is NOT Pinnacle-canonical (id 12 = Golf here),
so we enumerate: hit /markets?sport_id=N for N in a range, read sport_name + event
count, and auto-detect the esports sport(s). For any esports id with events we dump
one sample event so the collector can be wired to the real schema.

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

BASE = "https://pinnodds.com/kit/v1"
ESPORT_RE = re.compile(r"e-?sport|counter|cs2|cs:?go|dota|league of|valorant|rocket", re.I)
SCAN = range(1, 41)  # sport_id space to sweep


def get(path):
    req = urllib.request.Request(BASE + path, headers={
        "x-portal-apikey": KEY, "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:  # noqa: BLE001
        return None, f"ERR {e}"


print(f"scanning sport_id {SCAN.start}..{SCAN.stop - 1} on {BASE}/markets ...\n")
found = []  # (sid, name, prematch_n, live_n)
for sid in SCAN:
    row = {"sid": sid, "name": "?", "pre": 0, "live": 0, "ok": False}
    for et in ("prematch", "live"):
        code, body = get(f"/markets?sport_id={sid}&event_type={et}")
        if code == 200:
            try:
                d = json.loads(body)
                row["name"] = d.get("sport_name", "?")
                row[("pre" if et == "prematch" else "live")] = len(d.get("events") or [])
                row["ok"] = True
                row["_sample"] = (d.get("events") or [None])[0] if et == "prematch" else row.get("_sample")
            except Exception:
                pass
        time.sleep(0.15)
    if row["ok"]:
        flag = "  <== ESPORTS?" if ESPORT_RE.search(row["name"]) else ""
        print(f"  id {sid:>3}  {row['name']:<22} prematch={row['pre']:<4} live={row['live']:<4}{flag}")
        found.append(row)
    else:
        print(f"  id {sid:>3}  (no 200)")

esports = [r for r in found if ESPORT_RE.search(r["name"])]
print("\n===== esports candidates =====")
if not esports:
    print("NONE — no sport_name matched esports in the scanned range. Widen SCAN or check the key/plan.")
for r in esports:
    print(f"  id {r['sid']}  {r['name']}  prematch={r['pre']} live={r['live']}")
    samp = r.get("_sample")
    if samp:
        print("  --- sample prematch event (schema) ---")
        print(json.dumps(samp, indent=1)[:2600])
    else:
        print("  (no prematch events right now — try again during live matches, or check /markets live sample)")

print("\nPaste this whole output back — I need the id, the esports name(s), and the")
print("sample event SHAPE (team fields, market/odds fields) to wire the collector.")
