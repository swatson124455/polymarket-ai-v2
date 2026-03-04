"""
Twitter/X API v2 Filtered Stream Monitor — sports injury pipeline.

Connects to the X API v2 filtered stream endpoint using httpx.AsyncClient.stream()
(NOT aiohttp — httpx is used project-wide).

Monitors beat reporters configured in sports/data/beat_reporters.json.
Puts raw tweet dicts onto an asyncio.Queue using put_nowait() so slow
processing never blocks tweet reception.

Reconnect strategy: 30×2^n capped at 300 s (same pattern as whale_tracker.py).

Requires: TWITTER_BEARER_TOKEN env var in settings
Gated by: SPORTS_TWITTER_STREAM_ENABLED=true (default false)
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Optional, Set
from structlog import get_logger

logger = get_logger()

_STREAM_URL = "https://api.twitter.com/2/tweets/filtered-stream"
_RULES_URL  = "https://api.twitter.com/2/tweets/filtered-stream/rules"
_BASE_BACKOFF = 30   # seconds
_MAX_BACKOFF  = 300  # seconds


class TwitterInjuryMonitor:
    """
    X API v2 filtered stream monitor for sports beat reporters.

    Usage::
        monitor = TwitterInjuryMonitor(output_queue)
        asyncio.create_task(monitor.run_forever())

    Output queue items (dict)::
        {
          "source":    "twitter",
          "source_id": "<tweet_id>",
          "author_id": "<twitter_user_id>",
          "text":      "<tweet text>",
          "url":       "https://twitter.com/i/web/status/<id>",
        }
    """

    def __init__(
        self,
        output_queue: asyncio.Queue,
        bearer_token: Optional[str] = None,
        reporter_file: Optional[str] = None,
    ) -> None:
        self._queue = output_queue
        self._bearer_token = bearer_token
        self._reporter_file = reporter_file
        self._running = False
        self._consecutive_failures = 0
        self._reporter_ids: Set[str] = set()

    # ─── Public API ───────────────────────────────────────────────────────────

    async def run_forever(self) -> None:
        """Entry point: stream indefinitely, reconnecting on errors."""
        from config.settings import settings

        if not getattr(settings, "SPORTS_TWITTER_STREAM_ENABLED", False):
            logger.info(
                "TwitterInjuryMonitor: disabled",
                reason="SPORTS_TWITTER_STREAM_ENABLED=false",
            )
            return

        bearer = self._bearer_token or getattr(settings, "TWITTER_BEARER_TOKEN", None)
        if not bearer:
            logger.warning(
                "TwitterInjuryMonitor: no bearer token — monitor inactive",
                hint="Set TWITTER_BEARER_TOKEN in .env",
            )
            return

        reporter_file = self._reporter_file or getattr(
            settings, "SPORTS_TWITTER_BEAT_REPORTER_FILE",
            "sports/data/beat_reporters.json",
        )
        await self._load_reporter_ids(reporter_file)

        self._running = True
        logger.info("TwitterInjuryMonitor: starting stream loop", reporter_count=len(self._reporter_ids))

        while self._running:
            try:
                await self._stream(bearer)
                self._consecutive_failures = 0
            except asyncio.CancelledError:
                logger.info("TwitterInjuryMonitor: cancelled")
                break
            except Exception as exc:
                self._consecutive_failures += 1
                backoff = min(
                    _BASE_BACKOFF * (2 ** (self._consecutive_failures - 1)),
                    _MAX_BACKOFF,
                )
                logger.warning(
                    "TwitterInjuryMonitor: stream error — will reconnect",
                    error=str(exc),
                    consecutive_failures=self._consecutive_failures,
                    backoff_s=backoff,
                )
                await asyncio.sleep(backoff)

    def stop(self) -> None:
        """Signal the monitor to stop after the current reconnect cycle."""
        self._running = False

    async def setup_rules(self, bearer_token: Optional[str] = None) -> None:
        """
        Install X API v2 stream rules for all loaded reporter IDs.

        Batches IDs into groups of 25 (X API per-rule limit).
        Call this once to configure rules before starting the stream.
        """
        import httpx

        from config.settings import settings
        bearer = bearer_token or self._bearer_token or getattr(settings, "TWITTER_BEARER_TOKEN", None)
        if not bearer:
            logger.warning("TwitterInjuryMonitor.setup_rules: no bearer token")
            return

        if not self._reporter_ids:
            reporter_file = getattr(
                settings, "SPORTS_TWITTER_BEAT_REPORTER_FILE",
                "sports/data/beat_reporters.json",
            )
            await self._load_reporter_ids(reporter_file)

        headers = {
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Delete all existing rules
            resp = await client.get(_RULES_URL, headers=headers)
            if resp.status_code == 200:
                existing = resp.json().get("data", [])
                if existing:
                    ids_to_delete = [r["id"] for r in existing]
                    await client.post(
                        _RULES_URL, headers=headers,
                        json={"delete": {"ids": ids_to_delete}},
                    )
                    logger.info(
                        "TwitterInjuryMonitor.setup_rules: deleted existing rules",
                        count=len(ids_to_delete),
                    )

            # Add new rules — 25 IDs per rule to stay within X limits
            ids = list(self._reporter_ids)
            rules = []
            for i in range(0, len(ids), 25):
                batch = ids[i : i + 25]
                from_clause = " OR ".join(f"from:{uid}" for uid in batch)
                rules.append({
                    "value": f"({from_clause}) lang:en",
                    "tag": f"reporters_batch_{i // 25}",
                })

            if rules:
                resp = await client.post(
                    _RULES_URL, headers=headers, json={"add": rules}
                )
                logger.info(
                    "TwitterInjuryMonitor.setup_rules: rules installed",
                    rule_count=len(rules),
                    status=resp.status_code,
                )

    # ─── Internals ────────────────────────────────────────────────────────────

    async def _load_reporter_ids(self, reporter_file: str) -> None:
        """Load beat reporter Twitter IDs from the JSON seed file.

        I32: Validates both path candidates; raises FileNotFoundError if neither exists.
        """
        path1 = Path(reporter_file)
        path2 = Path(__file__).parents[2] / reporter_file
        path = path1 if path1.exists() else (path2 if path2.exists() else None)
        if path is None:
            raise FileNotFoundError(
                f"TwitterInjuryMonitor: reporter file not found at '{path1}' or '{path2}'. "
                "Set SPORTS_TWITTER_BEAT_REPORTER_FILE to a valid path."
            )
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            for _sport, reporters in data.items():
                for reporter in reporters:
                    uid = str(reporter.get("id", "")).strip()
                    if uid:
                        self._reporter_ids.add(uid)
            logger.info(
                "TwitterInjuryMonitor: loaded reporter IDs",
                count=len(self._reporter_ids),
                file=str(path),
            )
            # I31: Warn at 80% queue capacity if queue has a maxsize
            _qmax = getattr(self._queue, "maxsize", 0)
            if _qmax and self._queue.qsize() >= int(_qmax * 0.80):
                logger.warning(
                    "TwitterInjuryMonitor: output queue at ≥80% capacity",
                    qsize=self._queue.qsize(), maxsize=_qmax,
                )
        except Exception as exc:
            logger.warning(
                "TwitterInjuryMonitor: could not load reporter file",
                error=str(exc),
                file=str(path),
            )

    async def _stream(self, bearer_token: str) -> None:
        """
        Open the filtered stream and yield tweets into the output queue.

        Raises on connection errors / non-200 status so the outer loop
        can apply backoff and reconnect.
        """
        import httpx

        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "User-Agent": "polymarket-sports-monitor/1.0",
        }
        params = {
            "tweet.fields": "created_at,author_id,text,lang",
            "expansions": "author_id",
            "user.fields": "username,name",
        }

        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "GET", _STREAM_URL, headers=headers, params=params
            ) as response:
                if response.status_code == 429:
                    # I56: X API rate-limit — backoff 30s before raising so reconnect loop waits
                    logger.warning(
                        "TwitterInjuryMonitor: 429 rate-limited by X API — backing off 30s"
                    )
                    await asyncio.sleep(30)
                    raise RuntimeError("Twitter stream 429 rate-limited")
                if response.status_code != 200:
                    body = await response.aread()
                    raise RuntimeError(
                        f"Twitter stream returned {response.status_code}: {body[:200]}"
                    )

                logger.info("TwitterInjuryMonitor: stream connected")
                self._consecutive_failures = 0

                async for raw_line in response.aiter_lines():
                    if not self._running:
                        return

                    line = raw_line.strip()
                    if not line:
                        continue  # heartbeat keep-alive

                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    tweet = payload.get("data", {})
                    text      = tweet.get("text", "").strip()
                    author_id = tweet.get("author_id", "")
                    tweet_id  = tweet.get("id", "")

                    if not text:
                        continue

                    item = {
                        "source":    "twitter",
                        "source_id": tweet_id,
                        "author_id": author_id,
                        "text":      text,
                        "url":       f"https://twitter.com/i/web/status/{tweet_id}",
                    }
                    try:
                        self._queue.put_nowait(item)
                        # I31: Warn at 80% queue capacity
                        _qmax = getattr(self._queue, "maxsize", 0)
                        if _qmax and self._queue.qsize() >= int(_qmax * 0.80):
                            logger.warning(
                                "TwitterInjuryMonitor: output queue at ≥80% capacity",
                                qsize=self._queue.qsize(), maxsize=_qmax,
                            )
                    except asyncio.QueueFull:
                        logger.warning(
                            "TwitterInjuryMonitor: queue full — dropping tweet",
                            tweet_id=tweet_id,
                        )
