#!/usr/bin/env python3
"""Pin the CLOB /price side semantics against /book — live, any time.

WHY (2026-07-13): the shadow watcher shipped with /price's `side` read
backwards (side=BUY is the best BID, side=SELL is the best ASK — the side
names the BOOK SIDE, not your order side). Every record's bid/ask were
swapped; shadow fills quoted the bid; the spread gate could never fire.
The bug was only caught because ladder capture allowed cross-checking.
This script IS that cross-check, runnable on demand: before any watcher
deploy, after any Polymarket API change notice, or whenever the watcher's
crossed-book QUOTE SANITY ALARM fires.

METHOD: for N tokens (default: the most recent tokens in the shadow
JSONL; or --tokens), fetch /price?side=BUY, /price?side=SELL and /book.
A token AGREES when the BUY quote sits nearer the book's top bid than its
top ask AND the SELL quote sits nearer the top ask (nearest-side matching
absorbs book movement between the calls), and SELL >= BUY (uncrossed).
A token is REVERSED when both quotes sit nearer the OPPOSITE side — the
2026-07-13 failure signature.

PRE-REGISTERED VERDICT (fixed before any run):
  PASS       >= --min-tokens quotable tokens, zero REVERSED
  FAIL       any REVERSED token (semantics are wrong or changed — do not
             trust quote_book labels until resolved)
  NO-DATA    fewer quotable tokens than --min-tokens (widen --tokens)

SAFETY: read-only public GETs. No DB, no RPC, no writes.

INVOCATION (VPS or anywhere with outbound HTTPS):
    python3 scripts/verify_clob_price_sides.py \
        --shadow /opt/pa2-shared/mirror3_shadow.jsonl --n 5
    python3 scripts/verify_clob_price_sides.py --tokens <id1> <id2> ...
    python3 scripts/verify_clob_price_sides.py --self-test
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional

PRICE_URL = "https://clob.polymarket.com/price"
BOOK_URL = "https://clob.polymarket.com/book"
UA = {"User-Agent": "Mozilla/5.0 (mirror3-verify)", "Accept": "application/json"}


# ── Pure, offline-testable core ──────────────────────────────────────────────
def classify_token(price_buy: Optional[float], price_sell: Optional[float],
                   top_bid: Optional[float], top_ask: Optional[float]) -> str:
    """AGREES | REVERSED | UNQUOTABLE for one token's four observations."""
    obs = (price_buy, price_sell, top_bid, top_ask)
    if any(v is None or v <= 0 for v in obs):
        return "UNQUOTABLE"
    buy_near_bid = abs(price_buy - top_bid) <= abs(price_buy - top_ask)
    sell_near_ask = abs(price_sell - top_ask) <= abs(price_sell - top_bid)
    if buy_near_bid and sell_near_ask and price_sell >= price_buy:
        return "AGREES"
    if (not buy_near_bid) and (not sell_near_ask):
        return "REVERSED"
    return "UNQUOTABLE"  # mixed/racing book — evidence for neither verdict


def verdict(counts: dict, min_tokens: int) -> str:
    if counts.get("REVERSED", 0) > 0:
        return "FAIL"
    if counts.get("AGREES", 0) >= min_tokens:
        return "PASS"
    return "NO-DATA"


def tokens_from_shadow(path: str, n: int) -> list[str]:
    """Most recent n distinct token_ids from the shadow JSONL."""
    seen: list[str] = []
    try:
        with open(path) as f:
            lines = f.readlines()
    except OSError:
        return []
    for line in reversed(lines):
        try:
            tid = str(json.loads(line).get("token_id", ""))
        except json.JSONDecodeError:
            continue
        if tid and tid not in seen:
            seen.append(tid)
        if len(seen) >= n:
            break
    return seen


# ── Network runner ───────────────────────────────────────────────────────────
def _get(url: str, timeout_s: float = 8.0) -> Any:
    import urllib.request
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return json.loads(r.read())


def probe_token(tid: str) -> tuple[Optional[float], Optional[float],
                                   Optional[float], Optional[float]]:
    def px(side: str) -> Optional[float]:
        try:
            return float(_get(f"{PRICE_URL}?token_id={tid}&side={side}")
                         .get("price", 0)) or None
        except Exception:
            return None

    pb, ps = px("BUY"), px("SELL")
    tb = ta = None
    try:
        book = _get(f"{BOOK_URL}?token_id={tid}")
        bids = [float(b["price"]) for b in book.get("bids") or []]
        asks = [float(a["price"]) for a in book.get("asks") or []]
        tb = max(bids) if bids else None
        ta = min(asks) if asks else None
    except Exception:
        pass
    return pb, ps, tb, ta


def run(tokens: list[str], min_tokens: int) -> int:
    counts: dict[str, int] = {}
    print(f"probing {len(tokens)} tokens against /price + /book "
          f"(side=BUY must read the BID, side=SELL the ASK)")
    for tid in tokens:
        pb, ps, tb, ta = probe_token(tid)
        cls = classify_token(pb, ps, tb, ta)
        counts[cls] = counts.get(cls, 0) + 1
        print(f"  {tid[:14]}…  priceBUY={pb} priceSELL={ps} "
              f"bookBid={tb} bookAsk={ta}  -> {cls}")
    v = verdict(counts, min_tokens)
    print(f"\nVERDICT: {v}  {counts}")
    print("READ: FAIL = /price side semantics are NOT (BUY=bid, SELL=ask) —"
          " quote_book labels untrustworthy, halt readout trust until fixed."
          " NO-DATA = not enough quotable tokens; add --tokens.")
    return 0 if v == "PASS" else (2 if v == "NO-DATA" else 1)


# ── Self-test (offline) ──────────────────────────────────────────────────────
def _self_test() -> int:
    print("SELF-TEST — classify/verdict core (no network)\n")
    ok = True
    t1 = classify_token(0.08, 0.09, 0.08, 0.09) == "AGREES"
    print(f"  [agrees] correct mapping classified AGREES : {t1}"); ok &= t1
    t2 = classify_token(0.09, 0.08, 0.08, 0.09) == "REVERSED"
    print(f"  [reversed] 2026-07-13 signature caught : {t2}"); ok &= t2
    t3 = classify_token(None, 0.09, 0.08, 0.09) == "UNQUOTABLE"
    t3 &= classify_token(0.5, 0.5, 0.5, 0.5) == "AGREES"  # touching book legal
    print(f"  [partial/touching] handled : {t3}"); ok &= t3
    # racing book: BUY quote nearer bid, SELL ambiguous -> not evidence
    t4 = classify_token(0.08, 0.085, 0.08, 0.10) == "UNQUOTABLE"
    print(f"  [racing] mixed evidence is UNQUOTABLE, never a verdict : {t4}")
    ok &= t4
    t5 = (verdict({"AGREES": 3}, 3) == "PASS"
          and verdict({"AGREES": 9, "REVERSED": 1}, 3) == "FAIL"
          and verdict({"AGREES": 2}, 3) == "NO-DATA"
          and verdict({}, 3) == "NO-DATA")
    print(f"  [verdict] pre-registered rule : {t5}"); ok &= t5
    print(f"\n  RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--shadow", default="/opt/pa2-shared/mirror3_shadow.jsonl")
    p.add_argument("--n", type=int, default=5)
    p.add_argument("--tokens", nargs="*", default=None)
    p.add_argument("--min-tokens", type=int, default=3)
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    if args.self_test:
        return _self_test()
    tokens = args.tokens or tokens_from_shadow(args.shadow, args.n)
    if not tokens:
        print(f"no tokens to probe (shadow={args.shadow} empty/absent and "
              f"no --tokens given)")
        return 2
    return run(tokens, args.min_tokens)


if __name__ == "__main__":
    sys.exit(main())
