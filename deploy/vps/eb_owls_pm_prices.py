#!/usr/bin/env python3
"""Pull CLOB hourly price history for the PM-matched CS2 markets (EB, 2026-07-17).

Input: /home/ubuntu/eb-odds/owls_pm_meta.json (gamma meta per cid: outcomes,
clobTokenIds, outcomePrices label, gameStartTime). For each cid pulls
/prices-history?market=<yes_token>&interval=max&fidelity=60 (VERIFIED serving
resolved markets, §0-S7) and stores the RAW history. Free CLOB API, ~4.2k
calls, gentle 0.3s spacing. Resumable: cids already in the output are skipped.

Run detached:  nohup python3 eb_owls_pm_prices.py >> owls_pm_prices.log 2>&1 &
Kill:          pkill -f eb_owls_pm_prices.py
"""
import json, os, time, urllib.request, urllib.parse
from datetime import datetime, timezone

META = os.environ.get("OWLS_PM_META_PATH", "/home/ubuntu/eb-odds/owls_pm_meta.json")
OUT = os.environ.get("OWLS_PM_PRICES_PATH", "/home/ubuntu/eb-odds/owls_pm_prices.jsonl")
CLOB = "https://clob.polymarket.com/prices-history"


def fetch(token):
    url = CLOB + "?" + urllib.parse.urlencode(
        {"market": token, "interval": "max", "fidelity": 60})
    req = urllib.request.Request(url, headers={"User-Agent": "eb-pm-prices/1.0"})
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.load(r)
                h = d.get("history")
                return h if isinstance(h, list) else None
        except Exception:
            time.sleep(5 * attempt)
    return None


def main():
    meta = json.load(open(META, encoding="utf-8"))
    done = set()
    if os.path.exists(OUT):
        for l in open(OUT, encoding="utf-8"):
            try:
                done.add(json.loads(l)["cid"])
            except Exception:
                pass
    todo = [(cid, m) for cid, m in meta.items()
            if cid not in done and len(m.get("clobTokenIds") or []) == 2]
    print(f"{datetime.now(timezone.utc).isoformat()} meta={len(meta)} "
          f"done={len(done)} todo={len(todo)}", flush=True)
    t0 = time.monotonic()
    n_ok = n_empty = 0
    with open(OUT, "a", encoding="utf-8") as f:
        for i, (cid, m) in enumerate(todo, 1):
            tok = str(m["clobTokenIds"][0]).strip()
            h = fetch(tok)
            if h:
                n_ok += 1
            else:
                n_empty += 1
            f.write(json.dumps({"cid": cid, "yes_token": tok,
                                "points": len(h) if h else 0,
                                "history": h}, ensure_ascii=False) + "\n")
            f.flush()
            if i % 100 == 0 or i == len(todo):
                el = time.monotonic() - t0
                print(f"{datetime.now(timezone.utc).isoformat()} {i}/{len(todo)} "
                      f"ok={n_ok} empty={n_empty} {el/i:.2f}s/mkt "
                      f"eta={(len(todo)-i)*el/i/60:.0f}min", flush=True)
            time.sleep(0.3)
    print(f"DONE ok={n_ok} empty={n_empty} -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
