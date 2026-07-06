#!/usr/bin/env python3
"""esports_silo — ground-truth probe: does Polymarket actually list esports markets?

The snapshot collector reported 0 esports across all games. That could mean (a) the
collector only scanned one un-paginated page of ~100 markets, (b) its content classifier
needs a game word that PM titles omit, or (c) Polymarket genuinely has ~no esports match
markets right now. This probe settles it: paginate ALL active markets, bucket them by a
strict classifier (what the collector catches) AND a broad esports net, dump samples +
the tag/category fields, so we can fix the collector's query/classifier — or conclude the
pairing target isn't there.

USAGE (on the box — network; the silo has none):
    python3 esports_silo/scripts/probe_polymarket_esports.py
Read-only, keyless (Gamma is public).
"""
import json
import re
import sys
import urllib.error
import urllib.request

GAMMA = "https://gamma-api.polymarket.com"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
PAGE = 100
MAX_PAGES = 60  # up to 6000 active markets

# strict = the collector's current net (game words only)
STRICT = {
    "cs2": ("cs2", "counter-strike", "counter strike", "csgo", "cs:go", "cs go"),
    "lol": ("league of legends", "lck", "lpl", "lec", "lcs", "worlds", "msi"),
    "dota2": ("dota", "the international"),
    "valorant": ("valorant", "vct", "champions tour"),
}
# broad = strict + event/league/venue names that flag esports even without a game word
BROAD = re.compile(
    r"\b(e-?sports?|cs2|cs:?go|counter-?strike|valorant|vct|dota|the international|"
    r"league of legends|lol|lck|lpl|lec|lcs|worlds|msi|iem|esl|pgl|blast|dreamhack|"
    r"katowice|cologne|major|faze|navi|vitality|g2 esports|team liquid|fnatic|astralis|"
    r"cloud9|nrg|sentinels|paper rex|t1|gen\.?g|blast premier|riyadh masters)\b", re.I)


def get(url):
    req = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:  # noqa: BLE001
        return None, f"ERR {e}"


def strict_game(text):
    t = f" {text.lower()} "
    for g, kws in STRICT.items():
        for kw in kws:
            if kw in t:
                return g
    return None


total = 0
strict_hits = {}
broad_rows = []
tag_keys_seen = set()
first_keys = None

for page in range(MAX_PAGES):
    off = page * PAGE
    code, body = get(f"{GAMMA}/markets?closed=false&limit={PAGE}&offset={off}")
    if code != 200:
        print(f"page {page} (offset {off}) -> HTTP {code}: {body[:120]}")
        break
    try:
        arr = json.loads(body)
    except Exception:
        print(f"page {page}: non-JSON"); break
    if not isinstance(arr, list) or not arr:
        break
    for m in arr:
        total += 1
        if first_keys is None:
            first_keys = sorted(m.keys())
        for k in ("tags", "category", "categories", "series", "events"):
            if m.get(k):
                tag_keys_seen.add(k)
        q = str(m.get("question") or m.get("title") or "")
        desc = str(m.get("description") or "")[:200]
        blob = q + " " + desc
        g = strict_game(blob)
        if g:
            strict_hits[g] = strict_hits.get(g, 0) + 1
        if BROAD.search(blob):
            broad_rows.append((q[:90], m.get("slug"), m.get("category")))
    if len(arr) < PAGE:
        break

print(f"\nscanned {total} active markets across {page + 1} page(s)")
print("market object keys:", first_keys)
print("tag-ish fields present on some markets:", sorted(tag_keys_seen) or "NONE")

print(f"\nSTRICT classifier (collector's current net) esports hits: {strict_hits or 'NONE'}")

print(f"\nBROAD esports net matched {len(broad_rows)} market(s):")
for q, slug, cat in broad_rows[:40]:
    print(f"  [{cat}] {q}   ({slug})")
if not broad_rows:
    print("  NONE — Polymarket appears to list ~no esports match markets right now.")
print("\nPaste this whole output back.")
