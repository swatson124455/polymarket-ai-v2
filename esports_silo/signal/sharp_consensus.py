#!/usr/bin/env python3
"""esports_silo — the signal: raw sharp-book consensus → calibrated P(team_a). **NO DE-VIG.**

This is the one thing built from the ground up (PLAN #5). The prior bot failed because its
model was a strictly worse forecaster than the CLOB; the design here is deliberately humble:
the sharp books (Pinnacle/Singbet/Thunderpick) are among the world's best forecasters, so the
signal is their *consensus*, and it must EARN the right to emit a probability by proving
calibration on outcomes before it is trusted.

THE NO-DE-VIG PROBLEM (Cmd 2) AND ITS RESOLUTION
------------------------------------------------
A raw two-way price carries vig: `1/odds_a + 1/odds_b > 1`. The usual move — normalise to make
them sum to 1 — IS overround-stripping, which is BANNED (Cmd 2). So we never do algebra to
"remove" vig. Instead:
  1. `consensus_raw_score` = the mean RAW implied `1/odds_a` across books. Vig is KEPT. This is
     a monotone-in-truth *score*, NOT a probability (it is biased high by the vig).
  2. A `PlattCalibrator` learns a monotone map `raw_score → P(team_a)` from historical
     (score, outcome) pairs. This uses OUTCOMES (data), not algebra — it is calibration, not
     de-vig — and it naturally absorbs the (roughly symmetric) vig bias.
Until that calibrator is fitted on real forward outcomes, the signal REFUSES to emit a
probability (`calibrated=False`, `p_model=None`) — an unproven map is quarantined (Cmd 4).

`edge = p_model - market_price` is computed as a DIAGNOSTIC only. It is NEVER a bet trigger:
the old rule "bet when edge is large" selected the model's largest errors. The decision rule
lives in #6 and defers to price until out-of-sample skill (via `eval/skill_metrics`) is proven.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    from ..eval.skill_metrics import raw_implied_prob  # silo-internal (1/odds, RAW)
except ImportError:  # loose-file / test fallback — same formula, kept identical
    def raw_implied_prob(decimal_odds):  # type: ignore
        try:
            o = float(decimal_odds)
        except (TypeError, ValueError):
            return None
        return 1.0 / o if o > 0 else None

# Minimum (score, outcome) pairs before a fitted calibrator is trusted to emit probabilities.
CALIB_MIN_N = int(os.getenv("CALIB_MIN_N", "50"))


# ======================================================================================
# raw consensus (NO de-vig, NO normalisation)
# ======================================================================================
@dataclass
class Consensus:
    raw_score: Optional[float]          # mean RAW 1/odds_a across books (vig kept; NOT a prob)
    n_books: int
    dispersion: Optional[float]         # stdev of per-book raw implied (book disagreement)
    per_book: Dict[str, float] = field(default_factory=dict)
    overround_mean: Optional[float] = None   # mean (1/odds_a + 1/odds_b); >1 proves vig kept


def consensus_raw_score(book_odds: List[Dict[str, Any]]) -> Consensus:
    """Mean RAW implied `1/team_a_odds` across books. Vig is KEPT (Cmd 2 — never normalised).

    `book_odds`: list of {book, team_a_odds[, team_b_odds]}. Rows without a usable team_a_odds
    are ignored. `overround_mean` (when team_b_odds present) is reported precisely to make the
    kept vig visible: it is > 1 by construction, and we do nothing to remove it.
    """
    per_book: Dict[str, float] = {}
    implied: List[float] = []
    overrounds: List[float] = []
    for r in book_odds:
        ra = raw_implied_prob(r.get("team_a_odds"))
        if ra is None:
            continue
        book = str(r.get("book") or f"book{len(per_book)}")
        per_book[book] = ra
        implied.append(ra)
        rb = raw_implied_prob(r.get("team_b_odds"))
        if rb is not None:
            overrounds.append(ra + rb)
    if not implied:
        return Consensus(raw_score=None, n_books=0, dispersion=None)
    n = len(implied)
    mean = sum(implied) / n
    disp = math.sqrt(sum((x - mean) ** 2 for x in implied) / n) if n > 1 else 0.0
    ovr = (sum(overrounds) / len(overrounds)) if overrounds else None
    return Consensus(raw_score=mean, n_books=n, dispersion=disp,
                     per_book=per_book, overround_mean=ovr)


# ======================================================================================
# Platt (logistic) calibration — learns raw_score → P(team_a) from OUTCOMES, not algebra
# ======================================================================================
def _sigmoid(z: float) -> float:
    if z >= 0:
        ez = math.exp(-min(z, 60.0))
        return 1.0 / (1.0 + ez)
    ez = math.exp(max(z, -60.0))
    return ez / (1.0 + ez)


@dataclass
class PlattCalibrator:
    """1-D logistic calibrator: P = sigmoid(w * standardized(score) + b). Monotone in score.

    Learns from (raw_score, outcome) history — this is calibration from data, NOT de-vig.
    Refuses to fit (stays `fitted=False`) on < CALIB_MIN_N pairs or a single-class sample.
    """
    w: float = 0.0
    b: float = 0.0
    _mu: float = 0.0
    _sd: float = 1.0
    fitted: bool = False
    n: int = 0

    def fit(self, scores: List[Optional[float]], outcomes: List[Any],
            iters: int = 3000, lr: float = 0.3) -> "PlattCalibrator":
        pairs = []
        for s, y in zip(scores, outcomes):
            if s is None:
                continue
            try:
                s = float(s)
                y = int(y)
            except (TypeError, ValueError):
                continue
            if y in (0, 1):
                pairs.append((s, y))
        if len(pairs) < CALIB_MIN_N or len({y for _, y in pairs}) < 2:
            self.fitted = False
            self.n = len(pairs)
            return self
        xs = [p[0] for p in pairs]
        mu = sum(xs) / len(xs)
        var = sum((x - mu) ** 2 for x in xs) / len(xs)
        sd = math.sqrt(var) or 1.0
        w = b = 0.0
        n = len(pairs)
        for _ in range(iters):
            gw = gb = 0.0
            for x, y in pairs:
                p = _sigmoid(w * ((x - mu) / sd) + b)
                gw += (p - y) * ((x - mu) / sd)
                gb += (p - y)
            w -= lr * gw / n
            b -= lr * gb / n
        self.w, self.b, self._mu, self._sd, self.n, self.fitted = w, b, mu, sd, n, True
        return self

    def predict(self, score: Optional[float]) -> Optional[float]:
        if not self.fitted or score is None:
            return None
        try:
            score = float(score)
        except (TypeError, ValueError):
            return None
        return _sigmoid(self.w * ((score - self._mu) / self._sd) + self.b)


# ======================================================================================
# the signal
# ======================================================================================
@dataclass
class SignalOutput:
    p_model: Optional[float]            # calibrated P(team_a); None until the calibrator is fitted
    raw_score: Optional[float]          # raw consensus (vig kept); a score, NOT a probability
    n_books: int
    dispersion: Optional[float]
    calibrated: bool
    market_price: Optional[float] = None
    edge: Optional[float] = None        # p_model - market_price — DIAGNOSTIC ONLY, never a trigger
    note: str = ""


class SharpSignal:
    """raw sharp consensus → (calibrated) P(team_a). Emits a probability only once proven."""

    def __init__(self, calibrator: Optional[PlattCalibrator] = None):
        self.calibrator = calibrator or PlattCalibrator()

    def fit(self, book_odds_per_match: List[List[Dict[str, Any]]],
            outcomes: List[Any]) -> "SharpSignal":
        """Fit the calibrator on historical matches: raw consensus score → outcome (team_a win)."""
        scores = [consensus_raw_score(bo).raw_score for bo in book_odds_per_match]
        self.calibrator.fit(scores, outcomes)
        return self

    def predict(self, book_odds: List[Dict[str, Any]],
                market_price: Optional[float] = None) -> SignalOutput:
        c = consensus_raw_score(book_odds)
        p = self.calibrator.predict(c.raw_score)
        calibrated = p is not None
        note = ("" if calibrated else
                "raw consensus score only — NOT a probability until the calibrator is fitted "
                "on forward outcomes (Cmd 2/4)")
        edge = (p - market_price) if (p is not None and market_price is not None) else None
        return SignalOutput(
            p_model=p, raw_score=c.raw_score, n_books=c.n_books, dispersion=c.dispersion,
            calibrated=calibrated, market_price=market_price, edge=edge, note=note)


if __name__ == "__main__":  # demo on synthetic data (no network/DB)
    import random
    rng = random.Random(1)
    hist, ys = [], []
    for _ in range(400):
        true_p = rng.random()
        # three books quote raw odds around the truth, each with a vig margin
        books = []
        for name in ("pinnacle", "singbet", "thunderpick"):
            pa = min(0.97, max(0.03, true_p + rng.uniform(-0.03, 0.03)))
            margin = rng.uniform(1.02, 1.06)
            books.append({"book": name, "team_a_odds": margin / pa,
                          "team_b_odds": margin / (1 - pa)})
        hist.append(books)
        ys.append(1 if rng.random() < true_p else 0)
    sig = SharpSignal().fit(hist, ys)
    out = sig.predict(hist[0], market_price=0.5)
    print(f"fitted={sig.calibrator.fitted} n={sig.calibrator.n}")
    print(f"raw_score={out.raw_score:.4f} (overround kept)  p_model={out.p_model:.4f}  "
          f"edge(diag)={out.edge:+.4f}")
