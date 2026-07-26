#!/usr/bin/env python3
"""WEBSOCKET PUBLIC-ACCESS PROBE -- NEW FILE, READ-ONLY, NO KEYS SENT, NEVER TRADES.

Sources CONTRADICT each other on whether Kalshi's WS market-data channels need auth:
  * docs.kalshi.com/asyncapi.yaml  -> `trade` and `ticker`: "Authentication: Not required"
  * third-party guides            -> "All WebSocket connections require authentication"
Settle it empirically with an UNAUTHENTICATED connect. No API key is loaded or sent.
If this connects and streams, a real-time signed trade tape is available with zero
credentials and zero live-system contact.

Run:  python kalshi_ws_public_probe.py [seconds]
"""
import asyncio
import json
import sys
import time

import websockets

WS = "wss://api.elections.kalshi.com/trade-api/ws/v2"


async def run(seconds):
    print(f"connecting UNAUTHENTICATED to {WS} ...")
    try:
        async with websockets.connect(WS, open_timeout=15, ping_interval=None) as ws:
            print("  CONNECTED (no auth headers sent)")
            for cid, chan in ((1, "trade"), (2, "ticker")):
                await ws.send(json.dumps({"id": cid, "cmd": "subscribe",
                                          "params": {"channels": [chan]}}))
                print(f"  -> subscribe {chan}")
            t0 = time.time()
            seen = {}
            while time.time() - t0 < seconds:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                except asyncio.TimeoutError:
                    print("  (10s idle)")
                    continue
                m = json.loads(raw)
                typ = m.get("type")
                seen[typ] = seen.get(typ, 0) + 1
                if seen[typ] <= 2:
                    print(f"  [{typ}] {json.dumps(m)[:320]}")
            print(f"\n  message-type counts over {seconds}s: {seen}")
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run(int(sys.argv[1]) if len(sys.argv) > 1 else 30)))
