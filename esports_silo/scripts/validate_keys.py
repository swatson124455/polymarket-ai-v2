#!/usr/bin/env python3
"""Validate the esports_silo API keys.

Stdlib-only (urllib) so it runs before any pip install. Reads keys from the
environment (load your .env first). It does NOT print key values.

Auth mechanisms are taken from the prior bot's real clients (verified):
  * OddsPapi   -> GET api.oddspapi.io/v4 ; 402 = quota exhausted, 429 = throttled
  * PandaScore -> header  Authorization: Bearer <key>
  * Riot       -> header  X-Riot-Token: <key>   (personal keys EXPIRE every 24h)

Status interpretation:
  VALID          2xx, or 402/429 (key accepted; quota/rate only)
  INVALID        401 / 403        (key rejected or expired)
  UNREACHABLE    network/proxy blocked it (NOT a statement about the key)
  MISSING        no key in env

Exit code: 0 if every required key is VALID, else 1.
"""
from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request

TIMEOUT = 15


def _mask(key: str) -> str:
    if not key:
        return "(missing)"
    return f"{key[:4]}…{key[-2:]} (len {len(key)})" if len(key) > 8 else "(short)"


def _classify(status: int) -> str:
    if status in (200, 201, 202, 204, 402, 429):
        return "VALID"
    if status in (401, 403):
        return "INVALID"
    return f"UNEXPECTED({status})"


def _probe(url: str, headers: dict) -> tuple[str, str]:
    """Return (verdict, detail). Distinguishes auth failures from network failures."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return _classify(resp.status), f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return _classify(e.code), f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001 - network/proxy/DNS all mean "can't tell"
        return "UNREACHABLE", f"{type(e).__name__}: {e}"


def check_oddspapi(key: str) -> tuple[str, str]:
    if not key:
        return "MISSING", "set ODDSPAPI_API_KEY"
    # cs2 = sport_id 17 (verified). Auth param name per OddsPapi docs — confirm if INVALID.
    url = f"https://api.oddspapi.io/v4/fixtures?sport_id=17&apiKey={key}"
    return _probe(url, {"Accept": "application/json"})


def check_pandascore(key: str) -> tuple[str, str]:
    if not key:
        return "MISSING", "set PANDASCORE_API_KEY"
    return _probe(
        "https://api.pandascore.co/videogames",
        {"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )


def check_riot(key: str) -> tuple[str, str]:
    if not key:
        return "MISSING", "set RIOT_API_KEY (note: personal keys expire every 24h)"
    return _probe(
        "https://euw1.api.riotgames.com/lol/status/v4/platform-data",
        {"X-Riot-Token": key, "Accept": "application/json"},
    )


REQUIRED = {
    "OddsPapi (sharp lines)": ("ODDSPAPI_API_KEY", check_oddspapi),
    "PandaScore (match data)": ("PANDASCORE_API_KEY", check_pandascore),
    "Riot (LoL patch/schedule)": ("RIOT_API_KEY", check_riot),
}


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    print(f"{'provider':<28} {'key':<22} {'verdict':<14} detail")
    print("-" * 84)
    all_ok = True
    for label, (env_name, check) in REQUIRED.items():
        key = os.getenv(env_name, "")
        verdict, detail = check(key)
        if verdict != "VALID":
            all_ok = False
        print(f"{label:<28} {_mask(key):<22} {verdict:<14} {detail}")

    print("-" * 84)
    if not all_ok:
        print("Some keys are not confirmed VALID.")
        print("  UNREACHABLE  -> run this from a host with network access (not a sandbox).")
        print("  INVALID      -> rotate the key; for Riot, it likely expired (24h personal keys).")
    else:
        print("All required keys VALID.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
