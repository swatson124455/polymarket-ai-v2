"""Sharp-line reference: compare a whale's entry to a no-vig sharp book price.

Strategy premise (operator, 2026-07-05): the tailable edge is KNOWLEDGE, not
latency. Sports/esports whales bet on information hours ahead and hold — a
sharp book (Pinnacle) prices the same event efficiently, so "the whale bought
YES cheaper than the no-vig sharp line implied" is a measurable, copyable edge
that survives MB's ~60s delay. (Crypto is a latency race and is expected to
FAIL the gate — the harness already models the 60s lag, so that's the kill test.)

This module is deliberately split:
  - PURE, tested now (vendor-independent): no-vig conversion, point-in-time
    line selection, and the whale-vs-sharp candidate rule for the gate.
  - VENDOR client (OddsPapi) is a thin seam that reads the API key from the
    environment (settings.ODDSPAPI_API_KEY — NEVER hardcoded) and is wired to
    the live endpoint when the paid tier lands. Its response parse targets the
    shape EB reverse-engineered (players -> entries: price + createdAt) and is
    marked UNVERIFIED against the paid tier until a real call confirms it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from bots.mirror_backtest.replay import Decision


# ── PURE core (vendor-independent, fully testable) ───────────────────────────


def no_vig_two_way(decimal_a: float, decimal_b: float) -> Optional[tuple[float, float]]:
    """Two-way no-vig implied probabilities from decimal odds.

    prob_i = (1/dec_i) / (1/dec_a + 1/dec_b). Removes the overround so the two
    sides sum to 1.0. Returns None on non-positive/degenerate odds (both must be
    > 1.0 for a real market)."""
    if decimal_a <= 1.0 or decimal_b <= 1.0:
        return None
    ia, ib = 1.0 / decimal_a, 1.0 / decimal_b
    total = ia + ib
    if total <= 0:
        return None
    return ia / total, ib / total


@dataclass
class LineEntry:
    price: float          # decimal odds for the outcome at this timestamp
    created_at: datetime  # when the book showed this price


def pick_point_in_time(entries: list[LineEntry], at_time: datetime) -> Optional[LineEntry]:
    """Latest line entry at-or-before at_time — NO look-ahead. None if the book
    had no entry by then (the event is uncovered at that moment)."""
    eligible = [e for e in entries if e.created_at <= at_time]
    if not eligible:
        return None
    return max(eligible, key=lambda e: e.created_at)


def make_sharp_edge_rule(min_edge: float = 0.03, fee: float = 0.02, size_usd: float = 100.0):
    """Candidate rule for the acceptance gate. Pure function of a signal dict.

    The signal must carry a pre-attached `sharp_prob` — the no-vig implied
    probability of the WHALE'S side, as of the whale's wager time (attached
    offline by the data layer, exactly like book_snapshot/ob_row). The rule
    tails only when the sharp line says the whale's side is worth materially
    more than they paid:  edge = sharp_prob - price - fee >= min_edge.

    Returns a Decision (tail) or None (skip). Signals without a sharp_prob are
    skipped (uncovered), never guessed."""
    def rule(signal: dict) -> Optional[Decision]:
        sp = signal.get("sharp_prob")
        price = signal.get("price")
        if sp is None or price is None:
            return None
        try:
            edge = float(sp) - float(price) - fee
        except (TypeError, ValueError):
            return None
        return Decision(size_usd=size_usd) if edge >= min_edge else None
    return rule


def whale_side_sharp_prob(
    resolution_side: str,           # the whale's side: "YES" or "NO"
    no_vig_home: float,             # no-vig prob of the market's "home"/YES outcome
    home_is_yes: bool = True,
) -> Optional[float]:
    """Map the sharp two-way no-vig prob onto the whale's YES/NO side."""
    side = str(resolution_side or "").upper()
    if side not in ("YES", "NO"):
        return None
    yes_prob = no_vig_home if home_is_yes else (1.0 - no_vig_home)
    return yes_prob if side == "YES" else (1.0 - yes_prob)


# ── VENDOR seam (OddsPapi) — env key only, wired when the paid tier lands ─────


class OddsPapiClient:
    """Thin OddsPapi wrapper. Reads the API key from the environment ONLY
    (settings.ODDSPAPI_API_KEY) — no hardcoded key, ever. Network calls are
    intentionally not implemented here until the paid tier + real response
    shape are confirmed; `parse_line_series` encodes the EB-reverse-engineered
    shape and is UNVERIFIED against the paid tier.
    """

    BASE_URL = "https://api.oddspapi.io/v4"

    def __init__(self, api_key: Optional[str] = None):
        # Deferred import so this module imports without settings present (tests).
        if api_key is None:
            try:
                from config.settings import settings
                api_key = (getattr(settings, "ODDSPAPI_API_KEY", None) or "").strip()
            except Exception:
                api_key = ""
        self._api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    @staticmethod
    def parse_line_series(outcome_payload: dict) -> list[LineEntry]:
        """Extract the intraday (price, timestamp) series for one outcome from an
        OddsPapi /historical-odds payload. Shape (EB-reverse-engineered, UNVERIFIED
        vs the paid tier): outcome -> players -> {player: [ {price, createdAt}, ...]}.
        Robust to missing/negative fields; skips anything unparseable."""
        out: list[LineEntry] = []
        players = (outcome_payload or {}).get("players", {})
        if not isinstance(players, dict):
            return out
        for _player, entries in players.items():
            if not isinstance(entries, list):
                continue
            for e in entries:
                if not isinstance(e, dict):
                    continue
                price = e.get("price")
                ts = e.get("createdAt")
                if not isinstance(price, (int, float)) or price <= 1.0 or ts is None:
                    continue
                try:
                    when = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                except (TypeError, ValueError):
                    continue
                out.append(LineEntry(price=float(price), created_at=when))
        return sorted(out, key=lambda x: x.created_at)

    async def fetch_historical_odds(self, sport: str, fixture_id: str):  # pragma: no cover
        """LIVE call — wired when the paid tier + real response shape are
        confirmed. Raises until then so nothing silently pretends to have data."""
        raise NotImplementedError(
            "OddsPapi live fetch not wired — confirm paid-tier sports coverage + "
            "response shape first, then implement using self._api_key (env)."
        )
