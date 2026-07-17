#!/usr/bin/env python3
"""Owls Insight live esports odds recorder (EB, 2026-07-17).

RAW recorder: appends one JSONL line per endpoint per tick with the full
response body — parsing happens offline, so a schema surprise can never lose
data (correct-or-absent at the capture layer). 8 requests/tick hourly ≈
5.8k/month against the MVP tier's 300k budget. Unlike PinnOdds (20/day wall,
02-05Z+20-23Z gaps), this runs 24/7 — it is the only odds capture in the
20:00–23:59Z window.

Sources per tick: v1 odds (in practice 1xbet is the only esports book) and
v2 thunderpick pass-through, for cs2 / valorant / lol / dota2.

Key: /home/ubuntu/.eb_owls_key (0600, NEVER in git/chat). urllib is
HTTP/1.1-native — REQUIRED, the API hangs on HTTP/2 (probed 2026-07-17).
"""
import json, os, time, urllib.request, urllib.error
from datetime import datetime, timezone

KEYFILE = os.environ.get("OWLS_KEY_PATH", "/home/ubuntu/.eb_owls_key")
OUT = os.environ.get("OWLS_SNAPSHOT_PATH", "/home/ubuntu/eb-odds/owls_snapshots.jsonl")
BASE = "https://api.owlsinsight.com"
TITLES = ("cs2", "valorant", "lol", "dota2")
ENDPOINTS = [(f"v1_odds_{t}", f"/api/v1/{t}/odds") for t in TITLES] + \
            [(f"v2_thunderpick_{t}", f"/api/v2/thunderpick/{t}") for t in TITLES]


def key():
    k = open(KEYFILE, encoding="utf-8").read().strip()
    if not k:
        raise SystemExit("empty key file " + KEYFILE)
    return k


def fetch(path, k):
    """(status, remaining_month, body) — body None on any failure."""
    req = urllib.request.Request(
        BASE + path,
        headers={"Authorization": "Bearer " + k, "User-Agent": "eb-owls/1.0",
                 "Accept": "application/json"})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read().decode("utf-8", "replace")
                return r.status, r.headers.get("X-Ratelimit-Remaining-Month"), body
        except urllib.error.HTTPError as e:
            return e.code, None, None          # 4xx/5xx: no retry burn
        except Exception:
            if attempt == 1:
                time.sleep(3)
                continue
            return None, None, None
    return None, None, None


def main():
    cap = datetime.now(timezone.utc).isoformat()
    k = key()
    t0 = time.monotonic()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    n_ok = 0
    rem = None
    with open(OUT, "a", encoding="utf-8") as f:
        for name, path in ENDPOINTS:
            st, r, body = fetch(path, k)
            if r is not None:
                rem = r
            ok = st == 200 and body is not None and body.lstrip().startswith("{")
            if ok:
                n_ok += 1
            f.write(json.dumps({"captured_at": cap, "endpoint": name,
                                "status": st, "body": body if ok else None},
                               ensure_ascii=False) + "\n")
    total = sum(1 for _ in open(OUT, encoding="utf-8"))
    print(f"{cap} ok={n_ok}/{len(ENDPOINTS)} rem_month={rem} "
          f"total_lines={total} dur={time.monotonic()-t0:.1f}s file={OUT}")


if __name__ == "__main__":
    main()
