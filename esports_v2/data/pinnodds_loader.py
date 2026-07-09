"""
PinnOdds live/prematch odds loader for EsportsBot v2.

Fetches CURRENT Pinnacle match-winner odds for esports via the PinnOdds API and
returns the SAME lookup shape the sharp-line signal already consumes:
``match_key -> (odds_a, odds_b)`` (decimal odds), keyed by
``odds_loader.make_match_key`` so it is interchangeable with the OddsPapi path.

This is a FORWARD-COLLECTION source (live + prematch), which is what B13 needs —
`pinnacle_odds` is empty because no odds were ever collected. Point this at the
scan loop / a cron to accumulate sharp lines going forward.

PinnOdds API (verified 2026-07-09 against the live endpoint):
  - Base:    https://pinnodds.com/kit/v1
  - Auth:    header  x-portal-apikey: <key>   (env PINNACLE_ODDS_API_KEY)
  - Endpoint /markets?sport_id=11&event_type=prematch|live   (11 = Esports)
  - Shape:   {sport_id, sport_name, events:[ {home, away, league_name, starts,
             periods:{num_0:{money_line:{home, away, draw}}, num_1:..., ...}} ]}
  - Match winner = periods.num_0.money_line.{home, away}  (decimal; draw null for
    2-way esports). Prop events (e.g. "<team> (Kills)") carry NO num_0.money_line
    (only num_1 kill/map periods) -> they are dropped, correct-or-absent.

CORRECT-OR-ABSENT (matches sharp_reference): an event is only emitted when both
home and away are valid decimal odds (> 1.0) on num_0.money_line AND neither team
name carries a sub-market suffix. Any doubt -> skip; never a guessed line.

Usage::
    loader = PinnOddsLoader.from_env()           # reads PINNACLE_ODDS_API_KEY
    odds = loader.fetch_odds()                    # live + prematch match winners
    # odds = {"team_a||team_b||2026-07-10": (2.3, 1.632), ...}
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

# Reuse the canonical key builder so PinnOdds and OddsPapi lookups are identical.
from esports_v2.data.odds_loader import make_match_key

logger = logging.getLogger(__name__)

_BASE_URL = "https://pinnodds.com/kit/v1"
_ESPORTS_SPORT_ID = 11  # PinnOdds sport_id for Esports (verified 2026-07-09)
_REQ_DELAY = 1.5  # seconds between requests — be gentle on the demo tier

# Sub-market suffixes PinnOdds appends to team names on PROP events
# ("VfB (Kills)", "... (Games)", "... (Maps)"). Match-winner events have bare
# team names. Defence-in-depth on top of the num_0.money_line presence gate.
_PROP_SUFFIX_RX = re.compile(
    r"\((kills?|games?|maps?|rounds?|towers?|handicap|total)\)\s*$", re.I
)


class PinnOddsLoader:
    """Synchronous PinnOdds REST client for current esports match-winner odds."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("PinnOdds API key required")
        self._api_key = api_key
        self._session = requests.Session()
        # PinnOdds' WAF 403s the default python-requests User-Agent (verified live
        # 2026-07-09: curl 200, urllib/requests 403). Send a browser-ish UA + Accept.
        self._session.headers.update({
            "x-portal-apikey": api_key,
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
        })
        self._request_count = 0

    @classmethod
    def from_env(cls, var: str = "PINNACLE_ODDS_API_KEY") -> "PinnOddsLoader":
        """Build from the API key in the environment (never hardcode the key)."""
        key = os.environ.get(var, "").strip()
        if not key:
            raise ValueError(f"{var} not set in environment")
        return cls(api_key=key)

    def fetch_odds(
        self,
        event_types: Tuple[str, ...] = ("live", "prematch"),
    ) -> Dict[str, Tuple[float, float]]:
        """
        Fetch current esports match-winner odds.

        Args:
            event_types: which feeds to pull ("live", "prematch", or both).

        Returns:
            Dict mapping match_key -> (odds_a, odds_b) as DECIMAL odds.
            match_key format: "team_a_norm||team_b_norm||YYYY-MM-DD".
            Later feeds overwrite earlier ones for the same key (live > prematch
            is the intended precedence since live is passed first).
        """
        odds_lookup: Dict[str, Tuple[float, float]] = {}
        emitted = 0
        skipped = 0

        for et in event_types:
            events = self._fetch_events(et)
            for ev in events:
                parsed = self._parse_event(ev)
                if parsed is None:
                    skipped += 1
                    continue
                key, odds = parsed
                odds_lookup[key] = odds
                emitted += 1
            time.sleep(_REQ_DELAY)

        logger.info(
            f"pinnodds_odds_done emitted={emitted} skipped={skipped} "
            f"keys={len(odds_lookup)} requests={self._request_count}"
        )
        return odds_lookup

    def _fetch_events(self, event_type: str) -> List[dict]:
        """GET the esports markets feed for one event_type. [] on any failure."""
        data = self._get(
            "/markets",
            params={"sport_id": _ESPORTS_SPORT_ID, "event_type": event_type},
        )
        if not isinstance(data, dict):
            return []
        events = data.get("events")
        return events if isinstance(events, list) else []

    @staticmethod
    def _parse_event(ev: dict) -> Optional[Tuple[str, Tuple[float, float]]]:
        """
        Extract (match_key, (odds_a, odds_b)) from one PinnOdds event, or None.

        Correct-or-absent: only match-winner (num_0.money_line) with two valid
        decimal odds and bare (non-prop) team names is emitted.
        """
        if not isinstance(ev, dict):
            return None
        home = str(ev.get("home") or "").strip()
        away = str(ev.get("away") or "").strip()
        starts = str(ev.get("starts") or "")
        if not home or not away:
            return None

        # Drop prop events by name suffix ("... (Kills)"), belt-and-braces with
        # the money_line gate below.
        if _PROP_SUFFIX_RX.search(home) or _PROP_SUFFIX_RX.search(away):
            return None

        periods = ev.get("periods") or {}
        num0 = periods.get("num_0") if isinstance(periods, dict) else None
        ml = num0.get("money_line") if isinstance(num0, dict) else None
        if not isinstance(ml, dict):
            return None  # no match-winner line on this event -> skip (prop/series)

        odds_a = PinnOddsLoader._coerce_odds(ml.get("home"))
        odds_b = PinnOddsLoader._coerce_odds(ml.get("away"))
        if odds_a is None or odds_b is None:
            return None

        key = make_match_key(home, away, starts)
        return key, (odds_a, odds_b)

    @staticmethod
    def _coerce_odds(v) -> Optional[float]:
        """Decimal odds must be a number strictly greater than 1.0, else None."""
        try:
            f = float(v)
        except (ValueError, TypeError):
            return None
        return f if f > 1.0 else None

    def save_odds(self, odds: Dict[str, Tuple[float, float]], filepath: str | Path) -> None:
        """Save odds lookup to JSON for reuse (same format as OddsPapiLoader)."""
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        serializable = {k: list(v) for k, v in odds.items()}
        with open(filepath, "w") as f:
            json.dump(serializable, f, indent=2)
        logger.info(f"pinnodds_saved path={filepath} count={len(odds)}")

    @staticmethod
    def load_odds(filepath: str | Path) -> Dict[str, Tuple[float, float]]:
        """Load previously saved odds lookup."""
        filepath = Path(filepath)
        if not filepath.exists():
            return {}
        with open(filepath, "r") as f:
            data = json.load(f)
        return {k: tuple(v) for k, v in data.items()}

    def _get(self, path: str, params: Optional[Dict] = None) -> Optional[object]:
        """HTTP GET with retry. Key is on the session header, not the query."""
        for attempt in range(3):
            try:
                resp = self._session.get(
                    f"{_BASE_URL}{path}",
                    params=params or {},
                    timeout=15,
                )
                self._request_count += 1

                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "30"))
                    logger.warning(f"pinnodds_rate_limited retry_after={retry_after}")
                    time.sleep(retry_after)
                    continue
                if resp.status_code == 404:
                    return None
                if resp.status_code in (401, 403):
                    logger.error("pinnodds_auth_failed — check PINNACLE_ODDS_API_KEY")
                    return None

                resp.raise_for_status()
                return resp.json()

            except requests.RequestException as e:
                logger.warning(f"pinnodds_request_error attempt={attempt+1} error={e}")
                if attempt < 2:
                    time.sleep(2 ** (attempt + 1))

        return None
