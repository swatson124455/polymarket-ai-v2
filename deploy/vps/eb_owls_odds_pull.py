#!/usr/bin/env python3
"""Pull per-event Owls Insight historical odds for PM-matched CS2 games (EB, 2026-07-17).

Input: /home/ubuntu/eb-odds/pm_overlap_matched.json — the 2026-07-17 overlap
list (eventId, cid, oddsSnapshots) of Owls archive games matched 1:1 to
resolved PM CS2 match-winner markets. Pulls /api/v1/history/odds?eventId=…
for each (RAW body stored; parsing offline). ~4.5k requests of the 300k/mo
MVP budget; per-call latency 30-60s is the real cost → 4 workers, expect
10-40h wall. Resumable: eventIds already in the output file are skipped.

Run detached:  nohup python3 eb_owls_odds_pull.py >> owls_odds_pull.log 2>&1 &
Kill:          pkill -f eb_owls_odds_pull.py
"""
import json, os, time, urllib.request, urllib.parse, urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

KEYFILE = os.environ.get("OWLS_KEY_PATH", "/home/ubuntu/.eb_owls_key")
MATCHED = os.environ.get("OWLS_MATCHED_PATH", "/home/ubuntu/eb-odds/pm_overlap_matched.json")
OUT = os.environ.get("OWLS_ODDS_PATH", "/home/ubuntu/eb-odds/owls_cs2_event_odds.jsonl")
WORKERS = int(os.environ.get("OWLS_PULL_WORKERS", "4"))


def key():
    k = open(KEYFILE, encoding="utf-8").read().strip()
    if not k:
        raise SystemExit("empty key file " + KEYFILE)
    return k


def fetch(event_id, k):
    url = ("https://api.owlsinsight.com/api/v1/history/odds?eventId="
           + urllib.parse.quote(event_id, safe=""))
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + k, "User-Agent": "eb-owls/1.0",
        "Accept": "application/json"})
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(30 * attempt)
                continue
            return e.code, None
        except Exception:
            time.sleep(10 * attempt)
    return None, None


def main():
    k = key()
    rows = json.load(open(MATCHED, encoding="utf-8"))
    done = set()
    if os.path.exists(OUT):
        for l in open(OUT, encoding="utf-8"):
            try:
                done.add(json.loads(l)["eventId"])
            except Exception:
                pass
    todo = [r for r in rows if r["eventId"] not in done]
    print(f"{datetime.now(timezone.utc).isoformat()} matched={len(rows)} "
          f"done={len(done)} todo={len(todo)} workers={WORKERS}", flush=True)
    t0 = time.monotonic()
    n_ok = n_fail = 0

    def one(r):
        st, body = fetch(r["eventId"], k)
        ok = st == 200 and body and body.lstrip().startswith("{")
        return r, st, body if ok else None

    with open(OUT, "a", encoding="utf-8") as f, \
            ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for i, (r, st, body) in enumerate(pool.map(one, todo), 1):
            f.write(json.dumps({"eventId": r["eventId"], "cid": r["cid"],
                                "status": st, "body": body},
                               ensure_ascii=False) + "\n")
            f.flush()
            if body is not None:
                n_ok += 1
            else:
                n_fail += 1
            if i % 25 == 0 or i == len(todo):
                el = time.monotonic() - t0
                print(f"{datetime.now(timezone.utc).isoformat()} {i}/{len(todo)} "
                      f"ok={n_ok} fail={n_fail} {el/max(i,1):.1f}s/event "
                      f"eta={(len(todo)-i)*el/max(i,1)/3600:.1f}h", flush=True)
    print(f"DONE ok={n_ok} fail={n_fail} -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
