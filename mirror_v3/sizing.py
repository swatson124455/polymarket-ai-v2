"""Copy-sizing: flat base wage + conviction bonus (A+D hybrid).

OPERATOR-DECIDED 2026-07-11 (docs/MB_SIZING_OPTIONS.md decision record,
tiers set blind to data): every approved copy gets a flat 1-unit base;
a wager far above the TRADER'S OWN trailing median earns a bonus:

    r = same-tx-aggregated wager / trailing median (last 50 entry wagers)
    r < 2  -> 1.0x     2 <= r < 4 -> 1.25x     r >= 4 -> 1.5x (hard cap)

No malus below the median. Hard caps (BotBankrollManager per-bet cap,
per-event budget, per-trader daily budget) apply AFTER the multiplier —
the bonus sizes upside, the rails own the downside.

COLD START: multiplier is locked to 1.0x until the trader's median has
MIN_MEDIAN_OBS observations. Medians are seeded from the full cached
histories (hundreds-thousands of bets per rostered trader), so the lock
normally never binds in practice.

LIVE-MONEY GATE (pre-registered, not enforced here): the bonus component
may touch live sizing only after the Option-D conviction test passes
(pooled Spearman rho>0, P>=0.95, >=100 resolved fills). In the shadow it
merely ANNOTATES records so the readout can evaluate the rule.

Pure stdlib; no I/O beyond seed_medians_from_cache reading cache JSONs.
"""
from __future__ import annotations

import json
import os
from collections import deque
from statistics import median
from typing import Optional

TIER_MID_R = 2.0    # >= this many x the trader's median wager -> 1.25x
TIER_TOP_R = 4.0    # >= this -> 1.5x (hard cap)
MULT_BASE = 1.0
MULT_MID = 1.25
MULT_TOP = 1.5
WINDOW = 50         # trailing entry-wagers per trader
MIN_MEDIAN_OBS = 20  # below this, multiplier locks to 1.0x (cold start)


def conviction_multiplier(wager_usd: float, trail_median: Optional[float],
                          n_obs: int) -> tuple[float, Optional[float]]:
    """(multiplier, r). Locked to base on cold start or degenerate median."""
    if trail_median is None or trail_median <= 0 or n_obs < MIN_MEDIAN_OBS:
        return MULT_BASE, None
    r = wager_usd / trail_median
    if r >= TIER_TOP_R:
        return MULT_TOP, r
    if r >= TIER_MID_R:
        return MULT_MID, r
    return MULT_BASE, r


class TrailingMedians:
    """Per-trader trailing median of entry wagers (last WINDOW, USD)."""

    def __init__(self, window: int = WINDOW) -> None:
        self._window = window
        self._obs: dict[str, deque] = {}

    def seed(self, trader: str, wagers_usd: list[float]) -> None:
        self._obs[trader] = deque(
            [w for w in wagers_usd if w > 0][-self._window:],
            maxlen=self._window)

    def observe(self, trader: str, wager_usd: float) -> None:
        if wager_usd <= 0:
            return
        self._obs.setdefault(
            trader, deque(maxlen=self._window)).append(wager_usd)

    def stats(self, trader: str) -> tuple[Optional[float], int]:
        """(median, n_obs) BEFORE the current wager is observed."""
        d = self._obs.get(trader)
        if not d:
            return None, 0
        return float(median(d)), len(d)


def seed_medians_from_cache(cache_dir: str, roster: list[str],
                            window: int = WINDOW) -> TrailingMedians:
    """Seed from the full cached histories: per trader, the last `window`
    same-tx-aggregated BUY wagers (USD = size*price summed per tx hash).
    A missing cache file seeds nothing — that trader cold-starts (locked
    to 1.0x) instead of erroring the watcher."""
    tm = TrailingMedians(window)
    for addr in roster:
        path = os.path.join(cache_dir, f"{addr}.json")
        if not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                blob = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        trades = blob.get("trades", blob) if isinstance(blob, (dict, list)) else []
        rows = [t for t in trades
                if str(t.get("side", "")).upper() == "BUY"
                and t.get("timestamp") is not None]
        rows.sort(key=lambda t: str(t["timestamp"]))
        # same-tx aggregation; rows without a tx hash stay singletons
        wagers: list[float] = []
        by_tx: dict[str, float] = {}
        order: list[str] = []
        for i, t in enumerate(rows):
            usd = float(t.get("size", 0)) * float(t.get("price", 0))
            if usd <= 0:
                continue
            key = str(t.get("transactionHash") or f"__row{i}")
            if key not in by_tx:
                order.append(key)
            by_tx[key] = by_tx.get(key, 0.0) + usd
        wagers = [by_tx[k] for k in order]
        tm.seed(addr, wagers)
    return tm


def merge_same_tx(signals: list[dict]) -> list[dict]:
    """Merge decoded fill signals that belong to one wager: same
    (tx, trader, token_id). One whale order lands on-chain as several
    fills — the first fill alone under-reads conviction. Wager = summed
    USD; price = volume-weighted."""
    merged: dict[tuple, dict] = {}
    order: list[tuple] = []
    for s in signals:
        key = (s.get("tx", ""), s["trader"], s["token_id"])
        if key not in merged:
            merged[key] = dict(s)
            order.append(key)
            continue
        m = merged[key]
        tok_m = m["whale_size_usd"] / m["whale_price"] if m["whale_price"] else 0.0
        tok_s = s["whale_size_usd"] / s["whale_price"] if s["whale_price"] else 0.0
        m["whale_size_usd"] += s["whale_size_usd"]
        toks = tok_m + tok_s
        if toks > 0:
            m["whale_price"] = m["whale_size_usd"] / toks
    return [merged[k] for k in order]
