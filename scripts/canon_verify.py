#!/usr/bin/env python3
"""CANON VERIFIER - daily random BLIND verification of the lane's recorded
data against on-chain / venue ground truth. Operator-ordered 2026-08-25
("verify at random and blindly. this is top priority").

Three independent checks, each on a DATE-SEEDED random sample (the seed is
the UTC date, fixed before any value is seen - samples cannot be
cherry-picked, and a rerun on the same day reproduces the same sample):

  [1] RECORD CHECK  - shadow-sink records with a tx are re-derived from the
      Polygon tx receipt: does the chain agree the recorded trader BOUGHT the
      recorded token at the recorded price? (OrderFilled decode + the
      receipt-validated transfer-log side rule - the same kit that exposed
      the bidsim self-fill artifact on 2026-08-24.)
  [2] LABEL CHECK   - resolved labels in the supplemented cache are
      re-fetched from CLOB /markets/{condition_id} and the winner re-derived
      from token prices (the production-proven resolution source).
  [3] FEE CHECK     - fee_map entries are re-fetched from CLOB
      taker_base_fee and compared.

ANY mismatch prints a loud "[canon] ALARM" line. A source that cannot be
sampled prints UNAVAILABLE - never silence (empty-set false-pass landmine).
Read-only; every network call carries a timeout.

    PYTHONPATH=/opt/mirror3 python3 canon_verify.py [--k-records 8]
        [--k-labels 8] [--k-fees 6] [--seed YYYYMMDD]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.request
from datetime import datetime, timezone

RPC = os.environ.get("MIRROR3_RPC_URL", "https://polygon.gateway.tenderly.co")
CLOB = "https://clob.polymarket.com"
H = {"Content-Type": "application/json", "User-Agent": "curl/8"}
SINK = "/opt/pa2-shared/mirror3_shadow.jsonl"
GAMMA = "/opt/pa2-shared/mb_copyable_data/copyable_cache/gamma_resolutions.json"
FEEMAP = "/opt/pa2-shared/mb_copyable_data/copyable_cache/fee_map.json"
PRICE_TOL = 5e-4  # chain price = usdc/tokens to ~4dp vs recorded


def _rpc(method: str, params: list):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode()
    req = urllib.request.Request(RPC, data=body, headers=H)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["result"]


def _get(url: str):
    req = urllib.request.Request(url, headers=H)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def check_records(rng: random.Random, k: int, out: list) -> tuple[int, int]:
    from mirror_v3.copy_watcher import (FILL_TOPIC_V2, _hex, _topic_addr,
                                        _words, side_from_receipt_logs)
    rows = []
    with open(SINK, errors="replace") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if r.get("tx") and r.get("trader") and r.get("token_id") \
                    and isinstance(r.get("whale_price"), (int, float)):
                rows.append(r)
    if not rows:
        out.append("[canon]   records: UNAVAILABLE - no tx-bearing rows")
        return 0, 0
    sample = rng.sample(rows, min(k, len(rows)))
    okc = 0
    for r in sample:
        tx, trader, tok = r["tx"], str(r["trader"]).lower(), str(r["token_id"])
        try:
            rcpt = _rpc("eth_getTransactionReceipt", [tx])
        except Exception as e:
            out.append(f"[canon]   record {tx[:14]}.. RPC_ERROR {e!r}")
            continue
        if not rcpt:
            out.append(f"[canon] ALARM record {tx[:14]}.. NOT ON CHAIN")
            continue
        logs = [dict(lg) for lg in (rcpt.get("logs") or [])]
        prices = []
        for lg in logs:
            tp = lg.get("topics") or []
            if len(tp) >= 3 and _hex(tp[0]).lower() == FILL_TOPIC_V2 \
                    and _topic_addr(tp[2]) == trader:
                w = _words(lg.get("data", "0x"))
                if len(w) >= 4 and str(w[1]) == tok and w[3] > 0:
                    prices.append(w[2] / w[3])
        side = side_from_receipt_logs(logs, trader, tok)
        rec_p = float(r["whale_price"])
        p_ok = bool(prices) and (
            (min(prices) - PRICE_TOL <= rec_p <= max(prices) + PRICE_TOL)
            or abs(sum(prices) / len(prices) - rec_p) <= PRICE_TOL)
        s_ok = (side == "BUY")
        if p_ok and s_ok:
            okc += 1
        else:
            out.append(f"[canon] ALARM record {tx[:14]}.. price_ok={p_ok} "
                       f"(rec={rec_p:.4f} chain={[round(p,4) for p in prices[:4]]}) "
                       f"side={side}")
        time.sleep(0.2)
    return okc, len(sample)


def check_labels(rng: random.Random, k: int, out: list) -> tuple[int, int]:
    try:
        g = json.load(open(GAMMA))
    except (OSError, ValueError) as e:
        out.append(f"[canon]   labels: UNAVAILABLE - {e!r}")
        return 0, 0
    resolved = [(cid, m) for cid, m in g.items()
                if isinstance(m, dict) and m.get("resolution") in ("YES", "NO")
                and m.get("yes_token_id") and m.get("no_token_id")]
    if not resolved:
        out.append("[canon]   labels: UNAVAILABLE - no resolved entries")
        return 0, 0
    sample = rng.sample(resolved, min(k, len(resolved)))
    okc = 0
    for cid, m in sample:
        try:
            j = _get(f"{CLOB}/markets/{cid}")
        except Exception as e:
            out.append(f"[canon]   label {cid[:14]}.. FETCH_ERROR {e!r}")
            continue
        toks = j.get("tokens") or []
        winner = None
        for t in toks:
            try:
                if float(t.get("price") or 0) >= 0.99:
                    winner = str(t.get("token_id"))
            except (TypeError, ValueError):
                pass
        if winner is None:
            out.append(f"[canon]   label {cid[:14]}.. venue prices not "
                       f"settled - SKIP (not a mismatch)")
            continue
        expect = str(m["yes_token_id"]) if m["resolution"] == "YES" \
            else str(m["no_token_id"])
        if winner == expect:
            okc += 1
        else:
            out.append(f"[canon] ALARM label {cid[:14]}.. cache says "
                       f"{m['resolution']} but venue winner is a different "
                       f"token")
        time.sleep(0.2)
    return okc, len(sample)


def check_fees(rng: random.Random, k: int, out: list) -> tuple[int, int]:
    try:
        fm = json.load(open(FEEMAP))          # flat {token_id: taker_bps}
        g = json.load(open(GAMMA))
    except (OSError, ValueError) as e:
        out.append(f"[canon]   fees: UNAVAILABLE - {e!r}")
        return 0, 0
    cid_of: dict = {}
    for cid, m in g.items():
        if isinstance(m, dict):
            for kk in ("yes_token_id", "no_token_id"):
                if m.get(kk):
                    cid_of[str(m[kk])] = cid
    pairs = [(t, b, cid_of[t]) for t, b in fm.items() if t in cid_of]
    if not pairs:
        out.append("[canon]   fees: UNAVAILABLE - no fee_map token maps to a "
                   "condition_id via the label cache")
        return 0, 0
    sample = rng.sample(pairs, min(k, len(pairs)))
    okc = 0
    for tok, b, cid in sample:
        try:
            j = _get(f"{CLOB}/markets/{cid}")
        except Exception as e:
            out.append(f"[canon]   fee {str(tok)[:12]}.. FETCH_ERROR {e!r}")
            continue
        v = j.get("taker_base_fee")
        if v == b:
            okc += 1
        else:
            out.append(f"[canon] ALARM fee {str(tok)[:12]}.. map={b} "
                       f"venue={v}")
        time.sleep(0.2)
    return okc, len(sample)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k-records", type=int, default=8)
    ap.add_argument("--k-labels", type=int, default=8)
    ap.add_argument("--k-fees", type=int, default=6)
    ap.add_argument("--seed", type=int, default=None,
                    help="override the date seed (audit reruns)")
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    seed = a.seed if a.seed is not None else int(now.strftime("%Y%m%d"))
    rng = random.Random(seed)
    out: list[str] = []
    r_ok, r_n = check_records(rng, a.k_records, out)
    l_ok, l_n = check_labels(rng, a.k_labels, out)
    f_ok, f_n = check_fees(rng, a.k_fees, out)
    alarms = sum(1 for ln in out if "ALARM" in ln)
    print(f"[canon] {now:%Y-%m-%dT%H:%MZ} seed={seed} | records {r_ok}/{r_n} "
          f"| labels {l_ok}/{l_n} | fees {f_ok}/{f_n} | ALARMS={alarms}"
          + ("  <== INVESTIGATE" if alarms else ""))
    for ln in out:
        print(ln)
    if r_n == 0 and l_n == 0 and f_n == 0:
        print("[canon] ALARM: ALL THREE SOURCES UNSAMPLEABLE - the verifier "
              "is blind, not the data clean")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
