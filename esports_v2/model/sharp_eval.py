"""Evaluate the sharp closing line against realized results (Step 4 metrics).

Given the joined ``(closing line, who won)`` records from ``results_join``, this
computes the metrics that the FORWARD-COLLECTED data can actually support:

  - **Favorite hit-rate** — does the no-vig favourite (fair prob > 0.5) win? A
    well-calibrated sharp book should land ~= its own average favourite prob.
  - **Brier score** — mean squared error of the no-vig fair prob vs the outcome.
    The headline calibration number.
  - **Reliability table** — binned fair-prob vs realized win frequency.
  - **Closing-vs-open Brier (internal CLV)** — is the CLOSING line more accurate
    than the OPENING line we first captured? Positive movement validates that
    collecting the closing line (not just any line) is worth doing.

WHAT THIS DOES NOT MEASURE YET: the actual EB signal is ``edge = sharp_prob −
Polymarket_price − fee``. That needs the historical POLYMARKET price at bet time,
which the forward-collector does NOT capture (it collects PinnOdds only). So the
sharp-vs-market edge backtest is wired here (``edge_backtest`` consumes records
that carry ``market_price`` + ``yes_is_team_a``) but cannot be RUN until PM
prices are collected alongside the odds. This module reports that gap explicitly
rather than fabricating a market price.

De-vig method is a call parameter (``method='simple'`` default = the fleet
standard ``no_vig_two_way``; ``method='shin'`` routes through
``clv.odds_to_implied``). It is an OPEN operator decision — the caller chooses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from esports_v2.model.clv import odds_to_implied
from esports_v2.model.results_join import JoinedRecord
from esports_v2.model.sharp_reference import (
    DEFAULT_FEE,
    DEFAULT_MIN_EDGE,
    enrich_with_sharp_prob,
    no_vig_two_way,
)


def no_vig_prob_a(
    odds_a: float, odds_b: float, method: str = "simple"
) -> Optional[float]:
    """No-vig fair prob of the 'A' (home/odds_a) side. None on degenerate odds.

    method='simple' -> proportional de-overround (fleet standard, matches
    sharp_reference). method='shin' -> Shin's method via clv.odds_to_implied
    (which itself falls back to simple if the `shin` package is absent).
    """
    if method == "shin":
        if odds_a <= 1.0 or odds_b <= 1.0:
            return None
        pa, _ = odds_to_implied(odds_a, odds_b)
        return pa
    nv = no_vig_two_way(odds_a, odds_b)
    return None if nv is None else nv[0]


def wilson_ci(k: int, n: int, z: float = 1.96) -> Optional[tuple]:
    """Wilson score 95% interval for a binomial proportion, or None (n<=0).

    Chosen over the normal approximation because the sharp-line samples are
    SMALL (n=19 era) and proportions sit near 0.5-0.9 — Wilson stays inside
    [0,1] and doesn't collapse at small n. This is what makes 'hit-rate 0.42'
    readable as 'CI [0.23, 0.64] — consistent with anything' instead of a
    number that invites action (CLAUDE.md forbidden pattern 8/9).
    """
    if n <= 0:
        return None
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z / denom) * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (max(0.0, center - half), min(1.0, center + half))


# Below this many decided outcomes, every rate is labeled UNSTABLE in summaries
# (n=19 gave fav hit-rate 0.421 vs book prob 0.635 — pure sampling noise range).
MIN_STABLE_N = 50


@dataclass
class SharpLineReport:
    n: int = 0
    favorite_hit_rate: Optional[float] = None
    n_favorite_decidable: int = 0
    n_favorite_correct: Optional[int] = None
    brier_closing: Optional[float] = None
    brier_opening: Optional[float] = None
    brier_improvement: Optional[float] = None      # opening - closing (>0 = good)
    mean_favorite_prob: Optional[float] = None      # book's own avg fav confidence
    reliability: List[dict] = field(default_factory=list)  # bins
    method: str = "simple"

    def summary(self) -> str:
        lines = [
            f"Sharp-line evaluation (de-vig={self.method})",
            f"  N joined+labeled matches: {self.n}",
        ]
        if self.n == 0:
            lines.append("  (no labeled matches — see coverage/date-overlap notes)")
            return "\n".join(lines)
        fav = "n/a" if self.favorite_hit_rate is None else f"{self.favorite_hit_rate:.3f}"
        mfp = "n/a" if self.mean_favorite_prob is None else f"{self.mean_favorite_prob:.3f}"
        ci_s = ""
        if self.n_favorite_correct is not None and self.n_favorite_decidable > 0:
            ci = wilson_ci(self.n_favorite_correct, self.n_favorite_decidable)
            if ci is not None:
                ci_s = f"  95% CI [{ci[0]:.3f}, {ci[1]:.3f}]"
        unstable = (" — UNSTABLE (n<%d), do not act on" % MIN_STABLE_N
                    if self.n_favorite_decidable < MIN_STABLE_N else "")
        lines += [
            f"  Favorite hit-rate:        {fav}  (n={self.n_favorite_decidable}; "
            f"book mean fav prob {mfp}){ci_s}{unstable}",
            f"  Brier (closing line):     {self._f(self.brier_closing)}",
            f"  Brier (opening line):     {self._f(self.brier_opening)}",
            f"  Brier improvement (o-c):  {self._f(self.brier_improvement)}  "
            f"(>0 = closing line sharper)",
        ]
        if self.reliability:
            lines.append("  Reliability (fair prob bin -> realized win freq):")
            for b in self.reliability:
                lines.append(
                    f"    [{b['lo']:.1f},{b['hi']:.1f})  n={b['n']:>4}  "
                    f"pred={b['mean_pred']:.3f}  actual={b['actual']:.3f}"
                )
        return "\n".join(lines)

    @staticmethod
    def _f(v: Optional[float]) -> str:
        return "n/a" if v is None else f"{v:.4f}"


def _reliability_table(probs: List[float], outcomes: List[int], n_bins: int = 5) -> List[dict]:
    """Bin predicted probs into [0,1] and report mean-pred vs realized freq."""
    bins = []
    width = 1.0 / n_bins
    for i in range(n_bins):
        lo, hi = i * width, (i + 1) * width
        idx = [j for j, p in enumerate(probs)
               if (p >= lo and (p < hi or (i == n_bins - 1 and p <= hi)))]
        if not idx:
            continue
        mp = sum(probs[j] for j in idx) / len(idx)
        act = sum(outcomes[j] for j in idx) / len(idx)
        bins.append({"lo": lo, "hi": hi, "n": len(idx), "mean_pred": mp, "actual": act})
    return bins


def evaluate_sharp_line(
    records: List[JoinedRecord], *, method: str = "simple"
) -> SharpLineReport:
    """Compute sharp-line calibration/hit-rate metrics on joined records.

    Each record carries the closing odds (``odds_a``/``odds_b``), the opening odds
    (``open_odds_a``/``open_odds_b``), and ``home_won`` (did the odds_a side win).
    Records with degenerate odds are skipped (correct-or-absent).
    """
    close_probs: List[float] = []   # P(home) from the closing line
    open_probs: List[float] = []
    outcomes: List[int] = []        # 1 if home won else 0
    fav_correct = 0
    fav_total = 0
    fav_probs: List[float] = []

    for r in records:
        pa = no_vig_prob_a(r.odds_a, r.odds_b, method)
        if pa is None:
            continue
        oa = no_vig_prob_a(r.open_odds_a, r.open_odds_b, method)
        y = 1 if r.home_won else 0
        close_probs.append(pa)
        open_probs.append(oa if oa is not None else pa)
        outcomes.append(y)
        # Favorite decidable only when the line is not a coin flip.
        if abs(pa - 0.5) > 1e-9:
            fav_total += 1
            fav_prob = pa if pa > 0.5 else (1.0 - pa)
            fav_probs.append(fav_prob)
            home_is_fav = pa > 0.5
            if home_is_fav == bool(r.home_won):
                fav_correct += 1

    n = len(close_probs)
    if n == 0:
        return SharpLineReport(n=0, method=method)

    brier_c = sum((p - y) ** 2 for p, y in zip(close_probs, outcomes)) / n
    brier_o = sum((p - y) ** 2 for p, y in zip(open_probs, outcomes)) / n

    return SharpLineReport(
        n=n,
        favorite_hit_rate=(fav_correct / fav_total) if fav_total else None,
        n_favorite_decidable=fav_total,
        n_favorite_correct=fav_correct if fav_total else None,
        brier_closing=brier_c,
        brier_opening=brier_o,
        brier_improvement=(brier_o - brier_c),
        mean_favorite_prob=(sum(fav_probs) / len(fav_probs)) if fav_probs else None,
        reliability=_reliability_table(close_probs, outcomes),
        method=method,
    )


@dataclass
class EdgeBacktestReport:
    """Realized P&L of the sharp-vs-Polymarket edge rule (needs PM prices)."""

    n_records: int = 0
    n_with_market_price: int = 0
    n_bets: int = 0
    hit_rate: Optional[float] = None
    total_pnl: Optional[float] = None
    roi: Optional[float] = None
    note: str = ""
    n_wins: Optional[int] = None
    # Per-edge-size breakdown: does a bigger measured edge actually win/pay
    # more? Flat-ROI-vs-edge is the go/no-go signal for the edge rule itself.
    edge_buckets: List[dict] = field(default_factory=list)
    method: str = "simple"   # de-vig used for the sharp fair prob

    def summary(self) -> str:
        lines = [
            f"Sharp-vs-Polymarket edge backtest (de-vig={self.method})",
            f"  records: {self.n_records}  with_market_price: {self.n_with_market_price}",
        ]
        if self.n_with_market_price == 0:
            lines.append(f"  {self.note}")
            return "\n".join(lines)
        hit = "  hit-rate: n/a"
        if self.hit_rate is not None:
            hit = f"  hit-rate: {self.hit_rate:.3f}"
            if self.n_wins is not None and self.n_bets > 0:
                ci = wilson_ci(self.n_wins, self.n_bets)
                if ci is not None:
                    hit += f"  95% CI [{ci[0]:.3f}, {ci[1]:.3f}]"
        unstable = ("" if self.n_bets >= MIN_STABLE_N
                    else f" — UNSTABLE (n<{MIN_STABLE_N}), do not act on")
        lines += [
            f"  bets fired (should_bet): {self.n_bets}{unstable}",
            hit,
            f"  P&L: {self.total_pnl:+.4f}  ROI: {self.roi:+.2%}"
            if self.total_pnl is not None else "  P&L: n/a",
        ]
        if self.edge_buckets:
            lines.append("  By edge size (flat $1 stakes):")
            for b in self.edge_buckets:
                lines.append(
                    f"    edge [{b['lo']:.2f},{b['hi']:.2f})  n={b['n']:>4}  "
                    f"wins={b['wins']:>4}  pnl={b['pnl']:+.4f}  roi={b['roi']:+.2%}"
                )
        return "\n".join(lines)


def edge_backtest(
    records: List[dict],
    odds_lookup: Dict[str, tuple],
    *,
    fee: float = DEFAULT_FEE,
    min_edge: float = DEFAULT_MIN_EDGE,
    method: str = "simple",
) -> EdgeBacktestReport:
    """Backtest the actual EB edge rule, IF records carry Polymarket prices.

    ``records`` must carry ``match_key`` + ``market_price`` (Polymarket YES price)
    + ``yes_is_team_a`` + an outcome field ``home_won``/``yes_won``. Runs
    ``enrich_with_sharp_prob`` then scores realized P&L at flat $1 stakes on the
    ``sharp_should_bet`` side. When no record has a ``market_price``, returns a
    report that says so — the forward-collector does not yet capture PM prices.

    ``method`` (2026-07-13, operator "resolve/fix"): de-vig for the sharp fair
    prob — 'simple' (historical default, behavior unchanged) or 'shin'
    (injected via ``no_vig_prob_a``). Callers must gate shin on
    ``clv.shin_available()`` (the CLI drivers do) — inside ``odds_to_implied``
    an absent shin package silently degrades to simple.
    """
    n_with_price = sum(1 for r in records if r.get("market_price") is not None)
    if n_with_price == 0:
        return EdgeBacktestReport(
            n_records=len(records), n_with_market_price=0, method=method,
            note="No market_price on any record — the forward-collector captures "
                 "PinnOdds only. Collect the matched Polymarket price alongside the "
                 "odds to enable this backtest (see handoff §gaps).",
        )

    no_vig_a_fn = None
    if method != "simple":
        def no_vig_a_fn(oa: float, ob: float):
            return no_vig_prob_a(oa, ob, method)

    enrich_with_sharp_prob(records, odds_lookup, fee=fee, min_edge=min_edge,
                           no_vig_a_fn=no_vig_a_fn)
    bets = [r for r in records if r.get("sharp_should_bet")]
    n_bets = len(bets)
    if n_bets == 0:
        return EdgeBacktestReport(
            n_records=len(records), n_with_market_price=n_with_price, n_bets=0,
            method=method, note="No bets cleared min_edge.",
        )

    pnl = 0.0
    wins = 0
    settled = []  # (edge, won, bet_pnl) for the per-edge-size breakdown
    for r in bets:
        side = r.get("sharp_side")
        price = float(r["market_price"])
        # Resolve whether the YES outcome won from the available outcome field.
        yes_won = r.get("yes_won")
        if yes_won is None and "home_won" in r and "yes_is_team_a" in r:
            yes_won = bool(r["home_won"]) == bool(r["yes_is_team_a"])
        if yes_won is None:
            continue
        bet_yes = side == "YES"
        won = (bet_yes and yes_won) or ((not bet_yes) and not yes_won)
        entry = price if bet_yes else (1.0 - price)
        bet_pnl = (1.0 - entry) if won else (-entry)
        pnl += bet_pnl
        wins += 1 if won else 0
        edge = r.get("sharp_edge")
        if edge is not None:
            settled.append((float(edge), won, bet_pnl))

    return EdgeBacktestReport(
        n_records=len(records), n_with_market_price=n_with_price, n_bets=n_bets,
        hit_rate=(wins / n_bets) if n_bets else None,
        total_pnl=pnl,
        roi=(pnl / n_bets) if n_bets else None,
        n_wins=wins,
        edge_buckets=_edge_buckets(settled, min_edge),
        method=method,
    )


def _edge_buckets(settled: List[tuple], min_edge: float) -> List[dict]:
    """Bucket settled bets by measured edge size; empty buckets are omitted.

    Boundaries start at ``min_edge`` (nothing below it ever fires) and step
    through the ranges that matter for a threshold decision: is the rule's
    profit concentrated in big-edge bets, or flat across all of them?
    """
    bounds = sorted({round(b, 4) for b in
                     (min_edge, min_edge + 0.02, min_edge + 0.05,
                      min_edge + 0.10, 1.0) if b < 1.0}) + [1.0]
    out = []
    for lo, hi in zip(bounds, bounds[1:]):
        rows = [s for s in settled if lo <= s[0] < hi]
        if not rows:
            continue
        b_wins = sum(1 for s in rows if s[1])
        b_pnl = sum(s[2] for s in rows)
        out.append({"lo": lo, "hi": hi, "n": len(rows), "wins": b_wins,
                    "pnl": b_pnl, "roi": b_pnl / len(rows)})
    return out


# Orientation resolver seam: (condition_id, yes_token_id, team_a, team_b) -> bool
# or None. Injectable so the edge backtest is unit-testable without live CLOB.
OrientationResolver = Callable[[str, str, str, str], Optional[bool]]


def _default_orientation_resolver(
    condition_id: str, yes_token_id: str, team_a: str, team_b: str
) -> Optional[bool]:
    """Flip-proof ``yes_is_team_a`` via the authoritative live CLOB label.

    Imported lazily so this module has no hard network dependency at import time
    (and stays testable with an injected resolver). Correct-or-absent throughout.
    """
    from esports_v2.data.clob_labels import resolve_yes_is_team_a_via_clob

    return resolve_yes_is_team_a_via_clob(
        condition_id, yes_token_id, team_a, team_b
    )


def edge_backtest_from_joined(
    joined: List,  # List[results_join.JoinedRecord]
    *,
    resolve_orientation: Optional[OrientationResolver] = None,
    fee: float = DEFAULT_FEE,
    min_edge: float = DEFAULT_MIN_EDGE,
    method: str = "simple",
) -> EdgeBacktestReport:
    """Run the sharp-vs-Polymarket edge backtest directly from JoinedRecords.

    Bridges Step 2's ``JoinedRecord`` (which now carries the GAP-B PM fields) to
    ``edge_backtest``: builds the ``match_key -> (odds_a, odds_b)`` lookup and the
    per-record dicts (``match_key``/``market_price``/``yes_is_team_a``/
    ``home_won``), resolving orientation flip-proof from the captured
    ``condition_id``/``yes_token_id`` via ``resolve_orientation`` (default = live
    CLOB label). team_a passed to the resolver is the odds_a/``home`` side, so
    ``yes_is_team_a`` aligns to the same side the odds do.

    Correct-or-absent: a JoinedRecord is skipped when it has no ``market_price``,
    no ``condition_id``/``yes_token_id``, or orientation cannot be resolved to a
    bool — never a guessed side. Only records that survive reach ``edge_backtest``
    (which itself reports the no-PM-price gap when nothing has a price).
    """
    resolve = resolve_orientation or _default_orientation_resolver

    odds_lookup: Dict[str, tuple] = {}
    records: List[dict] = []
    for jr in joined:
        odds_lookup[jr.match_key] = (jr.odds_a, jr.odds_b)
        if jr.market_price is None or not jr.condition_id or not jr.yes_token_id:
            continue
        yes_is_team_a = resolve(
            jr.condition_id, jr.yes_token_id, jr.home, jr.away
        )
        if not isinstance(yes_is_team_a, bool):
            continue  # orientation unresolved -> never guess (would invert edge)
        records.append({
            "match_key": jr.match_key,
            "market_price": jr.market_price,
            "yes_is_team_a": yes_is_team_a,
            "home_won": jr.home_won,
        })

    if not records:
        return EdgeBacktestReport(
            n_records=len(joined), n_with_market_price=0, method=method,
            note="No JoinedRecord carried a PM price + resolvable orientation. "
                 "Once the forward-collector's PM capture (GAP B) overlaps the "
                 "results window, records will price out here.",
        )
    return edge_backtest(records, odds_lookup, fee=fee, min_edge=min_edge,
                         method=method)
