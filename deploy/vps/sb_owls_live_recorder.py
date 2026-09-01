#!/usr/bin/env python3
"""Sports-bot (SB): Owls Insight LIVE sports odds recorder (forward, 2026-07-17).

RAW recorder (EB pattern): appends one JSONL line per sport per tick with the
FULL response body — parsing happens offline, so a schema surprise can never
lose data (correct-or-absent at the capture layer).

WHY forward capture matters even though Phase-2 back-fills per-event odds:
  - The vendor `history/closing-odds` endpoint is server-side broken (524/500),
    so CLOSING lines can only be derived from our own forward snapshots.
  - The live `/api/v1/{sport}/odds` feed is **Pinnacle-sourced** (verified
    2026-07-17: data.pinnacle[…], key/title "pinnacle", h2h+spreads+totals with
    maxRiskStake limits) — the SHARP reference book. History per-event odds are
    US retail (draftkings/caesars/betmgm/…). Only the live feed captures the
    sharp line + its movement.
  - Seasonal sports the archive missed (NFL: empty until ~Sep 2026 because the
    live-archive began ~Feb 2026) can ONLY be captured going forward.

Sports: soccer, nba, nhl, nfl, tennis, mlb (all 200 on /odds, verified). cs2 is
EB's — untouched. 6 req/tick hourly ≈ 4.3k/month against the shared MVP budget
(~300k/mo; EB recorder ~6k/mo, SB history harvest is the big consumer). Runs
24/7. Quota floor aborts a tick below SB_QUOTA_FLOOR so it can never drain the
budget out from under EB.

Key: /home/ubuntu/.eb_owls_key (0600, NEVER in git/chat). urllib is
HTTP/1.1-native — REQUIRED, the API hangs on HTTP/2 (probed 2026-07-17).
Schedule hourly via cron (see deploy note in SB_OWLS_BACKDATA_STATE.md §2).
"""
import json, os, time, urllib.request, urllib.error
from datetime import datetime, timezone

KEYFILE = os.environ.get("OWLS_KEY_PATH", "/home/ubuntu/.eb_owls_key")
OUT = os.environ.get("SB_LIVE_SNAPSHOT_PATH",
                     "/home/ubuntu/sports-odds/sb_live_snapshots.jsonl")
BASE = "https://api.owlsinsight.com"
SPORTS = ("soccer", "nba", "nhl", "nfl", "tennis", "mlb")
ENDPOINTS = [(s, f"/api/v1/{s}/odds") for s in SPORTS]
QUOTA_FLOOR = int(os.environ.get("SB_QUOTA_FLOOR", "25000"))


def key():
    k = open(KEYFILE, encoding="utf-8").read().strip()
    if not k:
        raise SystemExit("empty key file " + KEYFILE)
    return k


def fetch(path, k):
    """(status, remaining_month, body) — body None on any failure."""
    req = urllib.request.Request(
        BASE + path,
        headers={"Authorization": "Bearer " + k, "User-Agent": "sb-owls/1.0",
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
            if rem is not None and int(rem) < QUOTA_FLOOR:
                print(f"{cap} QUOTA FLOOR (rem={rem} < {QUOTA_FLOOR}) — "
                      f"stopping tick early to protect EB budget", flush=True)
                break
            st, r, body = fetch(path, k)
            if r is not None:
                rem = r
            ok = st == 200 and body is not None and body.lstrip().startswith("{")
            if ok:
                n_ok += 1
            f.write(json.dumps({"captured_at": cap, "sport": name,
                                "status": st, "body": body if ok else None},
                               ensure_ascii=False) + "\n")
    total = sum(1 for _ in open(OUT, encoding="utf-8"))
    print(f"{cap} ok={n_ok}/{len(ENDPOINTS)} rem_month={rem} "
          f"total_lines={total} dur={time.monotonic()-t0:.1f}s file={OUT}", flush=True)


if __name__ == "__main__":
    main()
