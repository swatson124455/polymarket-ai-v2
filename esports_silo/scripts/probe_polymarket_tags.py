#!/usr/bin/env python3
"""esports_silo — discover Polymarket's real esports TAG IDs, then list live matches.

The blind /markets pagination was wrong (it caps at offset 2100). The correct way
(per Gamma docs) is to filter events by numeric tag_id, discovered from /tags — never
guess a slug. This probe:
  1. pulls ALL tags, prints the ones whose label/slug looks esports (id + label + slug);
  2. for each, GETs /events?tag_id=<id>&closed=false and lists event titles + their
     market questions/outcomes/prices + start times.
So we HARDCODE the exact tag IDs into the collector and prove the nightly matches exist.

USAGE (on the box): python3 esports_silo/scripts/probe_polymarket_tags.py
Read-only, keyless (Gamma is public).
"""
import json
import re
import urllib.error
import urllib.request

GAMMA = "https://gamma-api.polymarket.com"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
ESPORT_RE = re.compile(
    r"e-?sports?|cs2|cs:?go|counter-?strike|valorant|\bdota\b|league of legends|\blol\b|"
    r"\blck\b|\blpl\b|\blec\b|\blcs\b|\bvct\b|rainbow six|\br6\b|rocket league|overwatch|starcraft",
    re.I)


def get(path):
    req = urllib.request.Request(GAMMA + path, headers={"accept": "application/json", "user-agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:  # noqa: BLE001
        return None, f"ERR {e}"


# ── 1) all tags -> esports ones ────────────────────────────────────────────
print("===== scanning /tags for esports =====")
tags = []
for page in range(40):
    code, body = get(f"/tags?limit=100&offset={page * 100}")
    if code != 200:
        if page == 0:
            print(f"/tags HTTP {code}: {body[:150]}")
        break
    try:
        arr = json.loads(body)
    except Exception:
        break
    if not isinstance(arr, list) or not arr:
        break
    tags.extend(arr)
    if len(arr) < 100:
        break
print(f"total tags: {len(tags)}")

esport_tags = []
for t in tags:
    label = str(t.get("label") or t.get("name") or "")
    slug = str(t.get("slug") or "")
    if ESPORT_RE.search(label) or ESPORT_RE.search(slug):
        esport_tags.append((t.get("id"), label, slug))
print("esports-looking tags (id | label | slug):")
for tid, label, slug in esport_tags:
    print(f"  {tid}  | {label} | {slug}")
if not esport_tags:
    print("  NONE found by label — dumping first 30 tag labels so we can spot the right one:")
    for t in tags[:30]:
        print(f"    {t.get('id')} | {t.get('label')} | {t.get('slug')}")

# ── 2) live events per esports tag ─────────────────────────────────────────
for tid, label, slug in esport_tags:
    print(f"\n===== events for tag {tid} ({label}) =====")
    code, body = get(f"/events?tag_id={tid}&closed=false&limit=25&order=startDate&ascending=true")
    if code != 200:
        print(f"  HTTP {code}: {body[:150]}")
        continue
    try:
        evs = json.loads(body)
    except Exception:
        print("  non-JSON"); continue
    if not evs:
        print("  (no open events)"); continue
    for ev in evs[:25]:
        title = ev.get("title") or ev.get("slug")
        start = ev.get("startDate") or ev.get("startTime")
        mkts = ev.get("markets") or []
        print(f"  • {title}  [start {start}]  ({len(mkts)} market(s))")
        for m in mkts[:3]:
            q = m.get("question")
            outs = m.get("outcomes")
            prices = m.get("outcomePrices")
            print(f"      - {q}  outcomes={outs} prices={prices}")

print("\nPaste this whole output back — the tag IDs + event samples tell me exactly what to hardcode.")
