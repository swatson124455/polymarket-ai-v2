#!/usr/bin/env python3
"""esports_silo — is CS2 actually on Polymarket and we're just missing the tag?

The collector found 0 CS2 matches under tags counter-strike(100602)/csgo(100635) — but CS2
matches may live under a DIFFERENT tag (a tournament tag like BLAST/IEM/ESL, or a
'counter-strike-2' slug). This settles it empirically three ways:
  1. site search (/public-search) for CS2 terms + the exact upcoming CS2 teams pinnodds quotes;
  2. re-scan /tags with a BROAD cs/tournament regex and show each candidate's open-event count;
  3. under every candidate tag, dump the head-to-head ('... vs ...') event titles + which tag
     carried them — so if CS2 matches exist, we learn the tag to add.

USAGE (on the box): python3 esports_silo/scripts/probe_polymarket_cs2.py
Read-only, keyless (Gamma is public).
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

GAMMA = "https://gamma-api.polymarket.com"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
# upcoming CS2 teams pinnodds is quoting (from the live odds pull) + majors regulars
CS2_TERMS = ["CS2", "Counter-Strike", "FaZe", "TYLOO", "9z", "PVISION", "BB Team",
             "Vitality", "NAVI", "Spirit", "MOUZ", "Astralis"]
TAG_RE = re.compile(r"cs2|cs:?go|counter-?strike|\bblast\b|\biem\b|\besl\b|\bpgl\b|major|"
                    r"dreamhack|cologne|katowice|thunderpick|esea|intel|premier", re.I)
VS_RE = re.compile(r"\bvs\b", re.I)


def get(path):
    req = urllib.request.Request(GAMMA + path, headers={"accept": "application/json", "user-agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:200]
    except Exception as e:  # noqa: BLE001
        return None, f"ERR {e}"


def jload(body):
    try:
        return json.loads(body)
    except Exception:
        return None


# ── 1) site search for CS2 terms/teams ─────────────────────────────────────
print("===== /public-search for CS2 terms + upcoming pinnodds CS2 teams =====")
for term in CS2_TERMS:
    code, body = get(f"/public-search?q={urllib.parse.quote(term)}&limit_per_type=10&events_status=active")
    if code != 200:
        print(f"  q={term!r}: HTTP {code} ({body[:80]})")
        continue
    d = jload(body) or {}
    evs = d.get("events") or (d.get("data") or {}).get("events") or []
    hits = [e for e in evs if VS_RE.search(str(e.get("title") or ""))]
    print(f"  q={term!r}: {len(evs)} events, {len(hits)} look like matches")
    for e in hits[:4]:
        tgs = ",".join(str(t.get("slug")) for t in (e.get("tags") or []))
        print(f"      • {str(e.get('title'))[:70]}   tags=[{tgs}]")

# ── 2) re-scan tags, broad cs/tournament regex ─────────────────────────────
print("\n===== /tags — broad cs/tournament scan =====")
tags = []
for page in range(45):
    code, body = get(f"/tags?limit=100&offset={page * 100}")
    arr = jload(body)
    if code != 200 or not isinstance(arr, list) or not arr:
        break
    tags.extend(arr)
    if len(arr) < 100:
        break
cands = [(t.get("id"), t.get("label"), t.get("slug")) for t in tags
         if TAG_RE.search(str(t.get("label") or "")) or TAG_RE.search(str(t.get("slug") or ""))]
print(f"candidate tags: {len(cands)}")
for tid, label, slug in cands:
    code, body = get(f"/events?tag_id={tid}&closed=false&limit=100")
    evs = jload(body) or []
    n = len(evs) if isinstance(evs, list) else 0
    matches = [e for e in (evs if isinstance(evs, list) else [])
               if VS_RE.search(str(e.get("title") or ""))]
    flag = "  <== HEAD-TO-HEAD MATCHES" if matches else ""
    print(f"  id {tid:<8} {str(label)[:26]:<26} slug={slug:<24} open_events={n} matches={len(matches)}{flag}")
    for e in matches[:5]:
        print(f"       • {str(e.get('title'))[:74]}")

print("\nPaste this whole output back — if any CS2 head-to-heads show up under a tag,")
print("that tag's id is what I add to the collector.")
