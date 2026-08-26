#!/usr/bin/env python3
"""FIREHOSE RECORDER - the zero-based sifter's ore feed (operator "redo
sifter with assumption all prior info is wrong", 2026-08-25).

Records EVERY venue trade (all wallets, both sides) to daily-rotated
gzip files. No filtering, no thresholds, no roster - the whole point is
that the POPULATION defines the sieves later, not inherited numbers.
The only prior knowledge reused is venue MECHANICS proven in the live
watcher: subscribe shape, app-PING keepalive, 15s silent-window reconnect.

Rows (compact ndjson): {"w","tok","s","p","z","tx","t"}
Files: <out>/firehose_YYYYMMDD.jsonl.gz (gzip members append cleanly)
Guards: retention 14 files; refuses to write if dir > 20GB or disk free
< 20GB (LOUD, then idles - never silently fills the disk).

    RTDS_WS_URL=... python3 firehose_recorder.py [--out DIR]
"""
from __future__ import annotations

import asyncio
import gzip
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone

SILENT_S = 15.0
PING_S = 5.0
DIR_CAP_GB = 20
FREE_MIN_GB = 20
RETAIN = 14


def log(msg: str) -> None:
    print(f"[firehose] {datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} {msg}",
          flush=True)


def day_path(out: str) -> str:
    return os.path.join(
        out, f"firehose_{datetime.now(timezone.utc):%Y%m%d}.jsonl.gz")


def disk_ok(out: str) -> bool:
    used = sum(os.path.getsize(os.path.join(out, f))
               for f in os.listdir(out) if f.endswith(".gz"))
    free = shutil.disk_usage(out).free
    if used > DIR_CAP_GB * 2**30 or free < FREE_MIN_GB * 2**30:
        log(f"DISK GUARD TRIPPED used={used/2**30:.1f}GB "
            f"free={free/2**30:.1f}GB - PAUSING writes")
        return False
    return True


def retention(out: str) -> None:
    fs = sorted(f for f in os.listdir(out) if f.endswith(".gz"))
    for f in fs[:-RETAIN]:
        os.unlink(os.path.join(out, f))
        log(f"retention: removed {f}")


def parse(msg) -> list[dict]:
    if not isinstance(msg, dict) or msg.get("type") != "trades":
        return []
    pl = msg.get("payload")
    items = pl if isinstance(pl, list) else [pl]
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        w, tok = it.get("proxyWallet"), it.get("asset")
        if not w or not tok:
            continue
        try:
            p, z = float(it.get("price")), float(it.get("size"))
        except (TypeError, ValueError):
            continue
        ts = it.get("timestamp") or msg.get("timestamp")
        try:
            ts = int(ts)
            ts = ts // 1000 if ts > 2e10 else ts
        except (TypeError, ValueError):
            ts = int(time.time())
        out.append({"w": str(w).lower(), "tok": str(tok),
                    "s": str(it.get("side") or "").upper(), "p": p, "z": z,
                    "tx": str(it.get("transactionHash") or "").lower(),
                    "t": ts})
    return out


async def run(url: str, out: str) -> None:
    import websockets
    os.makedirs(out, exist_ok=True)
    rows = reconns = 0
    buf: list[str] = []
    last_flush = time.time()
    writing = True
    while True:
        try:
            async with websockets.connect(url, ping_interval=20,
                                          open_timeout=15) as ws:
                await asyncio.wait_for(ws.send(json.dumps(
                    {"action": "subscribe", "subscriptions":
                        [{"topic": "activity", "type": "trades"}]})),
                    timeout=10)
                log(f"connected+subscribed (reconnects={reconns})")

                async def _ping():
                    while True:
                        await asyncio.sleep(PING_S)
                        try:
                            await asyncio.wait_for(ws.send("PING"), timeout=5)
                        except Exception:
                            return
                ping_task = asyncio.create_task(_ping())
                try:
                    while True:
                        try:
                            raw = await asyncio.wait_for(
                                ws.recv(), timeout=SILENT_S)
                        except asyncio.TimeoutError:
                            log(f"SILENT {SILENT_S:.0f}s - reconnecting "
                                f"(rows={rows})")
                            break
                        try:
                            msg = json.loads(raw)
                        except (TypeError, ValueError):
                            continue
                        for r in parse(msg):
                            buf.append(json.dumps(r))
                            rows += 1
                        now = time.time()
                        if buf and (len(buf) >= 500 or now - last_flush > 30):
                            if writing := disk_ok(out):
                                with gzip.open(day_path(out), "at",
                                               encoding="utf-8") as f:
                                    f.write("\n".join(buf) + "\n")
                            buf.clear()
                            last_flush = now
                            if int(now) % 3600 < 31:
                                retention(out)
                finally:
                    ping_task.cancel()
        except Exception as e:
            log(f"connection error: {type(e).__name__}: {e}")
        reconns += 1
        await asyncio.sleep(2)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out",
                    default="/opt/pa2-shared/mb_copyable_data/firehose")
    a = ap.parse_args()
    url = os.environ.get("RTDS_WS_URL", "").strip()
    if not url:
        sys.exit("FATAL: RTDS_WS_URL not set")
    asyncio.run(run(url, a.out))
