"""L2 ladder capture — SANDBOX PROTOTYPE for the MB king session (hand-over).

LANE (MB_STATE §5, [build, unblocked]): "v3 rejection logging + RTDS plumbing
so the silo collects its own signal stream." This module is the LADDER half:
capture the FULL L2 book (CLOB /book) at signal time, so the silo grows its
own precise-fill dataset forward.

WHY (probe-CONFIRMED 2026-07-11, king branch): retrospective fill measurement
is CLOSED — only 164/34,507 roster tokens (0.5%) exist in orderbook_snapshots,
and shadow_fills holds just 12,713 ladders total (the PRECISE-gate coverage
ceiling). The running shadow watcher quotes TOP-OF-BOOK only (CLOB /price
best ask); it cannot answer "what would OUR SIZE have filled at" nor feed the
pre-registered sizing tiers (MB_SIZING_OPTIONS.md Option B needs post-cost
edge at size). One JSONL ladder per signal closes both gaps at $0 risk.

INTEGRATION SEAM (king session wires it; ONE call site):
    lc = LadderCapture.from_env()           # None when env-disabled
    ...after decode_fill/evaluate_gates in mirror_v3/copy_watcher.py:
    if lc: lc.submit(sig["token_id"], verdict, {"trader": sig["trader"],
                     "whale_price": sig["whale_price"],
                     "detect_lag_s": lag})
The seam is fire-and-forget BY DESIGN for the caller, but the worker awaits
its own writes — a full queue DROPS LOUDLY (counter + log line), it never
blocks detection. Records are append-only JSONL next to the shadow log; the
analyze_shadow readout can join on (token_id, ts).

SAFETY (same rails as the shadow watcher): public CLOB GETs + JSONL append
only. NO orders, NO DB writes, NO shared-module imports, NO mirror_v3
imports (prototype is silo-clean; king session relocates it under mirror_v3
and the env_guard allowlist gains the MIRROR3_LADDER_* keys).

ENV (explicit-when-enabled, v3 "unset is an error" style):
  MIRROR3_LADDER_CAPTURE    'true' to enable (absent/other = disabled)
  MIRROR3_LADDER_PATH       JSONL sink (default /opt/pa2-shared/mirror3_ladders.jsonl)
  MIRROR3_LADDER_DEPTH      levels kept per side (default 20)
  MIRROR3_LADDER_COOLDOWN_S per-token min seconds between captures (default 10)
  MIRROR3_LADDER_TIMEOUT_S  CLOB /book timeout (default 3.0)
  MIRROR3_LADDER_QUEUE      max queued captures before loud drop (default 200)
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

CLOB_BOOK_URL = "https://clob.polymarket.com/book"
_DROP_LOG_EVERY = 25  # log every Nth drop — loud, not spammy


# ── Pure, offline-testable core ──────────────────────────────────────────────
def trim_book(levels: Any, depth: int, side: str) -> list[dict]:
    """Normalize CLOB /book levels ([{price,size}] of strings) to floats,
    sort best-first (bids desc / asks asc), keep `depth`. Garbage rows are
    skipped, never fatal — a partial ladder beats no ladder."""
    out = []
    for lv in levels or []:
        try:
            p, s = float(lv["price"]), float(lv["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0.0 < p < 1.0 and s > 0:
            out.append({"price": p, "size": s})
    out.sort(key=lambda x: x["price"], reverse=(side == "bids"))
    return out[: max(1, int(depth))]


def book_record(
    token_id: str, reason: str, bids: list[dict], asks: list[dict],
    meta: Optional[dict], ts: float, capture_lag_s: float,
) -> dict:
    """One JSONL record. best/spread precomputed so readouts never re-derive
    them inconsistently; full trimmed ladders carried for VWAP walks."""
    best_bid = bids[0]["price"] if bids else None
    best_ask = asks[0]["price"] if asks else None
    return {
        "kind": "ladder",
        "ts": ts,
        "token_id": str(token_id),
        "reason": str(reason),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": (best_ask - best_bid)
        if (best_bid is not None and best_ask is not None) else None,
        "bids": bids,
        "asks": asks,
        "capture_lag_s": round(float(capture_lag_s), 3),
        "meta": meta or {},
    }


class CooldownGate:
    """Per-token min interval. Injected clock => deterministic tests."""

    def __init__(self, cooldown_s: float, clock: Callable[[], float] = time.monotonic):
        self._cd = float(cooldown_s)
        self._clock = clock
        self._last: dict[str, float] = {}

    def allow(self, token_id: str) -> bool:
        now = self._clock()
        last = self._last.get(token_id)
        if last is not None and (now - last) < self._cd:
            return False
        self._last[token_id] = now
        return True


# ── Async shell ──────────────────────────────────────────────────────────────
async def _default_fetch_book(token_id: str, timeout_s: float) -> Optional[dict]:
    """CLOB public /book. Lazy aiohttp import keeps the pure core deps-free."""
    import aiohttp

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout_s)
        ) as s:
            async with s.get(CLOB_BOOK_URL, params={"token_id": token_id}) as r:
                if r.status != 200:
                    return None
                return await r.json()
    except Exception:
        return None  # recorded as book_missing by the worker — never fatal


@dataclass
class LadderConfig:
    sink_path: str = "/opt/pa2-shared/mirror3_ladders.jsonl"
    depth: int = 20
    cooldown_s: float = 10.0
    timeout_s: float = 3.0
    queue_max: int = 200


@dataclass
class LadderStats:
    captured: int = 0
    book_missing: int = 0
    cooldown_skips: int = 0
    queue_drops: int = 0
    write_errors: int = 0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


class LadderCapture:
    """Bounded-queue capture worker. submit() never blocks the caller."""

    def __init__(
        self,
        cfg: LadderConfig,
        fetch_book: Callable[[str, float], Awaitable[Optional[dict]]] = _default_fetch_book,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        log: Callable[[str], None] = print,
    ):
        self.cfg = cfg
        self.stats = LadderStats()
        self._fetch = fetch_book
        self._clock = clock
        self._wall = wall_clock
        self._log = log
        self._gate = CooldownGate(cfg.cooldown_s, clock)
        self._q: asyncio.Queue = asyncio.Queue(maxsize=cfg.queue_max)
        self._task: Optional[asyncio.Task] = None

    @classmethod
    def from_env(cls) -> Optional["LadderCapture"]:
        """None unless MIRROR3_LADDER_CAPTURE=true — absent means OFF, and the
        caller's `if lc:` seam makes disabled cost one branch."""
        if os.environ.get("MIRROR3_LADDER_CAPTURE", "").strip().lower() != "true":
            return None
        return cls(LadderConfig(
            sink_path=os.environ.get(
                "MIRROR3_LADDER_PATH", "/opt/pa2-shared/mirror3_ladders.jsonl"),
            depth=int(os.environ.get("MIRROR3_LADDER_DEPTH", "20")),
            cooldown_s=float(os.environ.get("MIRROR3_LADDER_COOLDOWN_S", "10")),
            timeout_s=float(os.environ.get("MIRROR3_LADDER_TIMEOUT_S", "3.0")),
            queue_max=int(os.environ.get("MIRROR3_LADDER_QUEUE", "200")),
        ))

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_event_loop().create_task(self._worker())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def submit(self, token_id: str, reason: str, meta: Optional[dict] = None) -> bool:
        """Non-blocking enqueue. False = not queued (cooldown or full queue);
        full-queue drops are counted and logged LOUDLY (no silent caps)."""
        if not self._gate.allow(str(token_id)):
            self.stats.cooldown_skips += 1
            return False
        try:
            self._q.put_nowait((str(token_id), str(reason), meta, self._clock()))
            return True
        except asyncio.QueueFull:
            self.stats.queue_drops += 1
            if self.stats.queue_drops % _DROP_LOG_EVERY == 1:
                self._log(
                    f"LADDER-CAPTURE DROP #{self.stats.queue_drops}: queue full "
                    f"({self.cfg.queue_max}) — captures are being lost"
                )
            return False

    async def _worker(self) -> None:
        while True:
            token_id, reason, meta, t_submit = await self._q.get()
            try:
                await self._capture_one(token_id, reason, meta, t_submit)
            except Exception as e:  # worker must survive any single failure
                self.stats.write_errors += 1
                self._log(f"LADDER-CAPTURE ERROR {token_id}: {e!r}")
            finally:
                self._q.task_done()

    async def _capture_one(
        self, token_id: str, reason: str, meta: Optional[dict], t_submit: float
    ) -> None:
        book = await self._fetch(token_id, self.cfg.timeout_s)
        lag = self._clock() - t_submit
        if not isinstance(book, dict):
            self.stats.book_missing += 1
            rec = book_record(token_id, f"{reason}|book_missing", [], [],
                              meta, self._wall(), lag)
        else:
            rec = book_record(
                token_id, reason,
                trim_book(book.get("bids"), self.cfg.depth, "bids"),
                trim_book(book.get("asks"), self.cfg.depth, "asks"),
                meta, self._wall(), lag,
            )
            self.stats.captured += 1
        line = json.dumps(rec, separators=(",", ":"))
        await asyncio.to_thread(self._append, line)

    def _append(self, line: str) -> None:
        with open(self.cfg.sink_path, "a") as f:
            f.write(line + "\n")
