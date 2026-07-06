"""v3 signal collector — the silo's OWN whale-signal stream (RTDS -> DB).

Why this exists (MB_STATE.md §5, "v3 rejection logging + RTDS plumbing"):
old MirrorBot is kept alive in paper for one reason only — it is the process
that writes `mirror_rejected_signals`, the whale-signal population the
acceptance-gate backtest consumes (bots/mirror_backtest/data_access.py). To
FULLY STOP old MB (not just pause it), the v3 silo must produce an equivalent
stream itself. This module is that producer.

What it collects — and how it differs from old MB (READ THIS before trusting
the rows):
  Old MB logs *gate-specific* rejections: a whale signal that reached gate
  site X and was rejected there (reason=mirror_whale_too_small, gate_blocked,
  no_edge, ...). Signals it COPIES are not in the table at all. That split is
  a function of the (now-dead) whale-copy strategy.

  v3 has NO strategy (the slot is empty behind the acceptance gate). It cannot
  reproduce that copied-vs-rejected split, and it should not try. Instead it
  logs the RAW watched-wallet entry-signal universe: every deduped, in-bounds,
  above-threshold YES/NO entry a watched wallet makes. Every such row is
  tagged `rejection_reason="mirror_v3_strategy_gated"` (the signal was seen but
  not acted on, because there is no strategy yet) and carries
  `metadata.source="mirror_v3"`. `mirror_rejected_signals` has no bot_name
  column, so that reason+metadata marker is the ONLY way to separate v3 rows
  from old-MB rows — keep it. The two populations are different shapes and must
  not be blended without accounting for it. This is pre-gate research data:
  treat it as UNVERIFIED until the gate's data_access validates it.

Ingress filters mirror old MB's transport/ingress layer so the stream is the
same population old MB *saw* before its strategy touched it:
  - watched-wallet fast-reject         (elite_watchlist.on_rtds_trade)
  - transactionHash / composite dedup  (elite_watchlist.on_rtds_trade)
  - price in (0.01, 0.99), size > 0    (elite_watchlist.on_trade_event)
  - side: SELL is an EXIT, skipped; Yes/Up->YES, No/Down->NO; unknown skipped

Deliberately NOT inherited: old MB's $100 whale-size floor. The acceptance gate
(bots/mirror_backtest/data_access.py) applies no size floor and is meant to pick
the threshold itself; filtering by size HERE would destroy small-trade signal the
gate could never recover once v3 is the sole writer. whale_trade_usd is still
computed and stored so the gate can bucket/threshold on it downstream. A floor is
applied only if an operator sets min_whale_usd > 0 with a data justification
(scripts/verify_signal_population.py), never copied from the dead bot.

Isolation (mirror_v3/__init__.py): this module NEVER imports bots/mirror_bot.py
or bots/elite_watchlist.py. The watched-wallet set and the restore flag are
INJECTED (a predicate + a callable), so the collector has no dependency on the
dead scoring stack and is fully unit-testable with no live socket or DB.

Fail-safe: instrumentation must never break ingest. on_rtds_trade never raises;
a DB write failure is swallowed (the underlying insert already warns). No-op if
the DB is absent.
"""

from __future__ import annotations

import time
import zlib
from collections import OrderedDict, deque
from typing import Any, Awaitable, Callable, Dict, Optional

from structlog import get_logger

from mirror_v3 import BOT_NAME

logger = get_logger()

# The signal was seen by the silo but not acted on: the strategy slot is empty
# behind the acceptance gate. Distinct from every old-MB gate reason so v3 rows
# are filterable (there is no bot_name column on the table).
V3_REJECTION_REASON = "mirror_v3_strategy_gated"
V3_REJECTION_STAGE = "pre_gate"  # schema: pre_gate | gate | post_gate
V3_SOURCE_TAG = "mirror_v3"

# A1 (docs/ASSUMPTION_AUDIT_2026-07-06.md): the watched-wallet filter IS the
# old bot's core hypothesis (leaderboard leaders are worth watching). Recording
# only watched wallets makes that hypothesis unfalsifiable — skilled wallets
# outside the leaderboard would be invisible forever. The control stream logs a
# deterministic 1-in-N sample of NON-watchlist trades so a comparison
# population exists. Streams are separable via metadata.stream.
STREAM_WATCHED = "raw_watched_whale"
STREAM_CONTROL = "control"

# Match old MB's ingress constants so the raw population is identical.
# The whale-size floor defaults to 0 (log everything) — see module docstring:
# the gate sets the size threshold, not the collector. The old bot's $100 floor
# is NOT the default; it can only be re-enabled explicitly with a data check.
_DEFAULT_MIN_WHALE_USD = 0.0     # 0 = no floor; gate decides (was old-bot $100)
_DEFAULT_DEDUP_MAXLEN = 50_000   # elite_watchlist._MAX_SEEN_TX
_PRICE_FLOOR = 0.01              # elite_watchlist.on_trade_event: p <= 0.01 rejected
_PRICE_CEIL = 0.99               # elite_watchlist.on_trade_event: p >= 0.99 rejected


class V3SignalCollector:
    """Consumes RTDS trade events; writes the raw watched-wallet entry stream.

    Args:
        db: object exposing async `insert_mirror_rejected_signal(...)` (the
            base_engine Database). May be None (API-only / test) -> collector
            is a no-op writer.
        is_watched: predicate(addr_lower: str) -> bool. The injected wallet
            source; the collector never builds the watchlist itself.
        is_restored: callable() -> bool. Mirrors old MB's S117 guard — no
            signal is logged until the silo's state restore has completed, so
            the stream never contains pre-restore noise.
        min_whale_usd: optional floor on size*price. Default 0 = log everything
            (the gate sets the threshold). Set > 0 only with a data justification
            (scripts/verify_signal_population.py) — never inherit the old $100.
        dedup_maxlen: bounded seen-tx set cap (default 50k, matches old MB).
        bot_name: identity tag stored in metadata (default MirrorBotV3).
        control_sample_rate: 0 = off (unwatched wallets skipped entirely);
            N > 0 = additionally log a deterministic 1-in-N sample of
            NON-watchlist trades as metadata.stream="control" (A1 — the
            comparison population the watched-wallet hypothesis needs to be
            falsifiable). Deterministic via crc32 of the dedup key, so the
            sample is stable across restarts and processes.
    """

    def __init__(
        self,
        db: Any,
        is_watched: Callable[[str], bool],
        *,
        is_restored: Callable[[], bool],
        min_whale_usd: float = _DEFAULT_MIN_WHALE_USD,
        dedup_maxlen: int = _DEFAULT_DEDUP_MAXLEN,
        bot_name: str = BOT_NAME,
        control_sample_rate: int = 0,
    ) -> None:
        self._db = db
        self._is_watched = is_watched
        self._is_restored = is_restored
        self._min_whale_usd = float(min_whale_usd)
        self._dedup_maxlen = int(dedup_maxlen)
        self._bot_name = bot_name
        self._control_sample_rate = int(control_sample_rate)
        self._seen_tx: "OrderedDict[str, None]" = OrderedDict()
        # Diagnostics — mirrors old MB's get_stats() spirit.
        self._events_seen = 0
        self._watched_matched = 0
        self._control_matched = 0
        self._deduped = 0
        self._logged = 0
        self._control_logged = 0
        self._skipped_not_whale = 0
        self._skipped_price = 0
        self._skipped_exit = 0
        self._skipped_unknown_side = 0
        self._skipped_malformed = 0
        # A4 (docs/ASSUMPTION_AUDIT_2026-07-06.md): DELTA_SECONDS=60 is
        # old-bot folklore, never measured. Feed lag = wall clock at receipt
        # minus the event's own timestamp, sampled over ALL events (platform
        # feed lag, not wallet-specific). Bounded window; percentiles surface
        # in get_stats() -> the v3 heartbeat. Caveat: includes VPS clock skew
        # (NTP-synced boxes: negligible; guarded against gross skew below).
        self._lag_samples: "deque[float]" = deque(maxlen=5000)

    # ── RTDS handler (the RTDSWebSocket handler contract) ────────────────────

    async def on_rtds_trade(self, data: Dict[str, Any]) -> None:
        """Handle one RTDS activity/trade event. Never raises.

        RTDS event shape (unwrapped payload; see rtds_websocket._dispatch):
          {asset, conditionId, outcome, price, proxyWallet, side, size,
           transactionHash, ...}
        """
        try:
            await self._collect(data)
        except Exception as e:  # defense-in-depth: ingest must never break
            logger.debug("v3_signal_collect_error", error=str(e))

    async def _collect(self, data: Dict[str, Any]) -> None:
        self._events_seen += 1

        # A4: measure the real feed lag (see __init__). Runs on every event —
        # the sample must be the platform feed, not the watched subset.
        ts_raw = data.get("timestamp")
        if ts_raw is not None:
            try:
                ts_f = float(ts_raw)
                if ts_f > 1e12:  # ms epoch
                    ts_f /= 1000.0
                lag = time.time() - ts_f
                # Sanity band: tolerate small negative skew, drop absurdities.
                if -5.0 < lag < 3600.0:
                    self._lag_samples.append(lag)
            except (TypeError, ValueError):
                pass

        # S117 analog: block until the silo's state restore has completed.
        if not self._is_restored():
            return

        addr = data.get("proxyWallet")
        if not addr:
            self._skipped_malformed += 1
            return
        addr_lower = addr.lower()

        # Dedup key (transactionHash preferred, composite fallback) — also the
        # deterministic control-sampling key, so compute it before the branch.
        dedup_key = data.get("transactionHash") or (
            f"rtds_{addr}_{data.get('asset')}_{data.get('price')}"
            f"_{data.get('size')}_{data.get('side')}"
        )

        # Watched-wallet check (elite_watchlist.on_rtds_trade fast-reject) —
        # plus the A1 control sample for unwatched wallets.
        if self._is_watched(addr_lower):
            self._watched_matched += 1
            stream = STREAM_WATCHED
        else:
            if self._control_sample_rate <= 0:
                return
            if zlib.crc32(dedup_key.encode()) % self._control_sample_rate != 0:
                return
            self._control_matched += 1
            stream = STREAM_CONTROL
        if dedup_key in self._seen_tx:
            self._deduped += 1
            return
        self._seen_tx[dedup_key] = None
        while len(self._seen_tx) > self._dedup_maxlen:
            self._seen_tx.popitem(last=False)

        market_id = data.get("conditionId")
        token_id = data.get("asset")
        if not market_id or not token_id:
            self._skipped_malformed += 1
            return

        try:
            price = float(data.get("price", 0))
            size = float(data.get("size", 0))
        except (TypeError, ValueError):
            self._skipped_malformed += 1
            return

        # Ingress bounds (elite_watchlist.on_trade_event). NOT the strategy's
        # 0.03/0.97 price-floor gate — that belongs to a strategy v3 does not have.
        if price <= _PRICE_FLOOR or price >= _PRICE_CEIL:
            self._skipped_price += 1
            return
        if size <= 0:
            self._skipped_malformed += 1
            return

        # Side resolution (elite_watchlist.on_trade_event). SELL is an exit
        # signal, not an entry candidate — the backtest's population is entries.
        raw_side = str(data.get("side", "BUY")).upper()
        outcome = str(data.get("outcome", "")).capitalize()
        if raw_side == "SELL":
            self._skipped_exit += 1
            return
        if outcome in ("Yes", "Up"):
            side = "YES"
        elif outcome in ("No", "Down"):
            side = "NO"
        else:
            # v3 has no token-side DB fallback (that lived in old MB). An
            # unresolvable side can't be a clean entry candidate — skip it.
            self._skipped_unknown_side += 1
            return

        # Whale size is RECORDED but not gated by default: the acceptance gate
        # applies no size floor and sets the threshold itself. A floor is applied
        # only if an operator sets min_whale_usd > 0 (data-justified), never
        # inherited from the dead bot's $100 (mirror_bot.py:2972).
        whale_trade_usd = size * price
        if self._min_whale_usd > 0 and whale_trade_usd < self._min_whale_usd:
            self._skipped_not_whale += 1
            return

        await self._write(
            trader_address=addr,  # FULL address — never truncated (Phase B collides on prefixes)
            market_id=str(market_id),
            token_id=str(token_id),
            side=side,
            price=price,
            whale_trade_usd=whale_trade_usd,
            outcome_raw=outcome,
            raw_side=raw_side,
            stream=stream,
        )

    async def _write(
        self,
        *,
        trader_address: str,
        market_id: str,
        token_id: str,
        side: str,
        price: float,
        whale_trade_usd: float,
        outcome_raw: str,
        raw_side: str,
        stream: str = STREAM_WATCHED,
    ) -> None:
        if self._db is None:
            return
        metadata = {
            "source": V3_SOURCE_TAG,
            "bot_name": self._bot_name,
            "stream": stream,
            "outcome": outcome_raw,
            "raw_side": raw_side,
        }
        try:
            await self._db.insert_mirror_rejected_signal(
                trader_address=trader_address,
                market_id=market_id,
                rejection_reason=V3_REJECTION_REASON,
                rejection_stage=V3_REJECTION_STAGE,
                token_id=token_id,
                side=side,
                price=price,
                whale_trade_usd=whale_trade_usd,
                metadata=metadata,
            )
            if stream == STREAM_CONTROL:
                self._control_logged += 1
            else:
                self._logged += 1
        except Exception as e:
            # insert_mirror_rejected_signal already swallows+warns internally;
            # this is belt-and-suspenders so a write can never break ingest.
            logger.warning("v3_signal_write_failed", error=str(e), market_id=market_id)

    # ── diagnostics ──────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        stats: Dict[str, Any] = {
            "events_seen": self._events_seen,
            "watched_matched": self._watched_matched,
            "control_matched": self._control_matched,
            "deduped": self._deduped,
            "logged": self._logged,
            "control_logged": self._control_logged,
            "skipped_not_whale": self._skipped_not_whale,
            "skipped_price": self._skipped_price,
            "skipped_exit": self._skipped_exit,
            "skipped_unknown_side": self._skipped_unknown_side,
            "skipped_malformed": self._skipped_malformed,
            "seen_tx_size": len(self._seen_tx),
        }
        # A4: measured feed-lag percentiles — the number that retires the
        # DELTA_SECONDS=60 assumption once enough samples accumulate.
        if self._lag_samples:
            lags = sorted(self._lag_samples)
            n = len(lags)
            stats["feed_lag_n"] = n
            stats["feed_lag_p50_s"] = round(lags[n // 2], 2)
            stats["feed_lag_p95_s"] = round(lags[min(n - 1, int(n * 0.95))], 2)
            stats["feed_lag_max_s"] = round(lags[-1], 2)
        return stats


def build_rtds_feed(
    collector: V3SignalCollector,
    *,
    ws_url: Optional[str] = None,
    ping_interval: int = 5,
    recv_timeout: int = 25,
) -> Any:
    """Wire a collector to a live RTDSWebSocket (the 'plumbing' half).

    Import is local so unit tests of the collector never pull in websockets.
    The caller owns the returned socket's connect()/disconnect() lifecycle.
    Not auto-started from run.py yet: that needs the watched-wallet source
    decided/wired (MB_STATE.md §5 next step).
    """
    from base_engine.data.rtds_websocket import RTDSWebSocket

    kwargs: Dict[str, Any] = {
        "handler": collector.on_rtds_trade,
        "ping_interval": ping_interval,
        "recv_timeout": recv_timeout,
    }
    if ws_url:
        kwargs["ws_url"] = ws_url
    return RTDSWebSocket(**kwargs)
