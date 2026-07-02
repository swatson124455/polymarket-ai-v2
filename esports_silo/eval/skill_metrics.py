#!/usr/bin/env python3
"""esports_silo — skill-evaluation harness. **P&L-FREE. DE-VIG-FREE.**

Judges a forecaster on FORECASTING SKILL vs the outcome and vs the market — never on
realized dollars (Cmd 1) and never after stripping vig (Cmd 2). Every input is used RAW.

FROM-SCRATCH per D1 — the prior `esports_v2/backtest/metrics.py` is NOT pulled: it embeds
`compute_pnl` (`:160`, banned Cmd 1), a `passes_gate` coupled to CLV (`:42`, Cmd 2), and a
Shin-devigged `pinnacle_prob`/CLV (`:11,143`, Cmd 2). The scoring formulas here (Brier,
log-loss, ECE, Pearson, AUC) are textbook and *referenced*, not carried. There is deliberately
no P&L, yield, ROI, stake, or CLV function anywhere in this module.

The unit of judgement is: does the model forecast the OUTCOME better than the market price?
`brier_skill = market_brier - model_brier` (positive ⇒ model beats the market). The old bot
lost because its model was a strictly worse forecaster than the CLOB; this harness is how we
detect that BEFORE trusting a signal.

Prediction record (a plain dict); only `p_model` + `outcome` are required:
  p_model       float  P(team_a wins), calibrated, in [0,1]        (REQUIRED)
  outcome       int    1 if team_a won, 0 if team_b won (resolved) (REQUIRED)
  market_price  float  Polymarket team_a=YES price at decision     (optional; skill-vs-market)
  team_a_odds   float  RAW closing decimal odds, team_a            (optional; closing-line)
  team_b_odds   float  RAW closing decimal odds, team_b            (optional)
  game          str    for per-game breakdown                      (optional)

Closing-line note (Cmd 2): the implied close is `1/team_a_odds` — RAW, vig NOT removed and
NOT normalised against `team_b_odds` (that normalisation IS overround-stripping, which is
banned). It is a biased-but-consistent reference, so a *correlation* against it is meaningful
while an absolute de-vigged probability is forbidden.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# --- skill-gate thresholds (env-overridable; P&L-free by construction) ----------------
# Minimum resolved sample before a skill verdict is trusted (forward data is small at first).
SKILL_MIN_N = int(os.getenv("SKILL_MIN_N", "200"))
# Max Expected Calibration Error the model may carry and still pass.
SKILL_MAX_ECE = float(os.getenv("SKILL_MAX_ECE", "0.10"))
# The model must beat the market's Brier by at least this margin (skill, not noise).
SKILL_MIN_BRIER_EDGE = float(os.getenv("SKILL_MIN_BRIER_EDGE", "0.0"))

_EPS = 1e-15


# ======================================================================================
# low-level scorers — pure functions over (probability, binary outcome)
# ======================================================================================
def _valid(preds: List[dict], key: str = "p_model") -> List[dict]:
    """Records with a usable probability in [0,1] AND a resolved 0/1 outcome."""
    out = []
    for p in preds:
        v = p.get(key)
        y = p.get("outcome")
        if v is None or y is None:
            continue
        try:
            v = float(v)
            y = int(y)
        except (TypeError, ValueError):
            continue
        if 0.0 <= v <= 1.0 and y in (0, 1):
            out.append(p)
    return out


def brier(preds: List[dict], key: str = "p_model") -> Optional[float]:
    """Mean Brier score = mean((p - y)^2). Lower is better. None if no usable rows."""
    rows = _valid(preds, key)
    if not rows:
        return None
    return sum((float(p[key]) - int(p["outcome"])) ** 2 for p in rows) / len(rows)


def log_loss(preds: List[dict], key: str = "p_model") -> Optional[float]:
    """Mean binary log loss (clamped). Lower is better. None if no usable rows."""
    rows = _valid(preds, key)
    if not rows:
        return None
    total = 0.0
    for p in rows:
        q = min(1 - _EPS, max(_EPS, float(p[key])))
        y = int(p["outcome"])
        total += -(y * math.log(q) + (1 - y) * math.log(1 - q))
    return total / len(rows)


def accuracy(preds: List[dict], key: str = "p_model") -> Optional[float]:
    """Fraction where (p>0.5) matches the outcome. Ties (p==0.5) count as wrong."""
    rows = _valid(preds, key)
    if not rows:
        return None
    return sum(1 for p in rows if (float(p[key]) > 0.5) == (int(p["outcome"]) == 1)) / len(rows)


def ece(preds: List[dict], key: str = "p_model", n_bins: int = 10
        ) -> Tuple[Optional[float], List[Dict[str, Any]]]:
    """Expected Calibration Error via equal-width histogram binning, plus reliability bins.

    ECE = sum over bins of (bin_n / N) * |mean_pred - empirical_rate|.
    """
    rows = _valid(preds, key)
    if not rows:
        return None, []
    n_bins = max(1, n_bins)
    buckets: List[List[dict]] = [[] for _ in range(n_bins)]
    for p in rows:
        v = float(p[key])
        idx = min(n_bins - 1, int(v * n_bins))  # v==1.0 lands in the last bin
        buckets[idx].append(p)
    total = len(rows)
    e = 0.0
    bins: List[Dict[str, Any]] = []
    for i, b in enumerate(buckets):
        if not b:
            bins.append({"lo": i / n_bins, "hi": (i + 1) / n_bins, "n": 0,
                         "mean_pred": None, "empirical": None})
            continue
        mean_pred = sum(float(p[key]) for p in b) / len(b)
        empirical = sum(int(p["outcome"]) for p in b) / len(b)
        e += (len(b) / total) * abs(mean_pred - empirical)
        bins.append({"lo": i / n_bins, "hi": (i + 1) / n_bins, "n": len(b),
                     "mean_pred": mean_pred, "empirical": empirical})
    return e, bins


def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    """Pearson correlation. None if <2 points or a series has zero variance."""
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def discrimination(preds: List[dict], key: str = "p_model") -> Optional[float]:
    """Point-biserial correlation of the probability with the 0/1 outcome (higher=better)."""
    rows = _valid(preds, key)
    if len(rows) < 2:
        return None
    return _pearson([float(p[key]) for p in rows], [float(int(p["outcome"])) for p in rows])


def auc(preds: List[dict], key: str = "p_model") -> Optional[float]:
    """ROC AUC via the Mann–Whitney rank statistic (tie-corrected). 0.5 = no discrimination."""
    rows = _valid(preds, key)
    pos = [float(p[key]) for p in rows if int(p["outcome"]) == 1]
    neg = [float(p[key]) for p in rows if int(p["outcome"]) == 0]
    if not pos or not neg:
        return None
    # average rank of positives among all scores (ties share the average rank)
    scored = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg], key=lambda t: t[0])
    ranks = [0.0] * len(scored)
    i = 0
    while i < len(scored):
        j = i
        while j < len(scored) and scored[j][0] == scored[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0  # ranks are 1-based; average over the tie block
        for k in range(i, j):
            ranks[k] = avg_rank
        i = j
    sum_pos_ranks = sum(r for r, (_, lbl) in zip(ranks, scored) if lbl == 1)
    n_pos, n_neg = len(pos), len(neg)
    return (sum_pos_ranks - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


# ======================================================================================
# closing-line agreement — RAW implied prob (Cmd 2: no de-vig, no normalisation)
# ======================================================================================
def raw_implied_prob(decimal_odds: Optional[float]) -> Optional[float]:
    """1 / decimal_odds — the RAW implied probability (vig NOT removed). None if invalid."""
    try:
        o = float(decimal_odds)
    except (TypeError, ValueError):
        return None
    return 1.0 / o if o > 0 else None


def closing_line_agreement(preds: List[dict], key: str = "p_model"
                           ) -> Dict[str, Optional[float]]:
    """How well `key` tracks the RAW sharp closing line (correlation + mean signed delta).

    Uses `1/team_a_odds` RAW (Cmd 2). Only records carrying a positive `team_a_odds` and a
    valid probability are used; `n` reports how many.
    """
    xs: List[float] = []
    ys: List[float] = []
    for p in _valid(preds, key):
        pc = raw_implied_prob(p.get("team_a_odds"))
        if pc is None:
            continue
        xs.append(float(p[key]))
        ys.append(pc)
    if not xs:
        return {"n": 0, "correlation": None, "mean_delta": None}
    mean_delta = sum(a - b for a, b in zip(xs, ys)) / len(xs)
    return {"n": len(xs), "correlation": _pearson(xs, ys), "mean_delta": mean_delta}


# ======================================================================================
# the report
# ======================================================================================
@dataclass
class SkillReport:
    n: int = 0
    model_brier: Optional[float] = None
    model_log_loss: Optional[float] = None
    model_accuracy: Optional[float] = None
    model_ece: Optional[float] = None
    model_discrimination: Optional[float] = None
    model_auc: Optional[float] = None
    # vs-market (only when market_price present)
    n_with_market: int = 0
    market_brier: Optional[float] = None
    market_log_loss: Optional[float] = None
    brier_skill: Optional[float] = None      # market_brier - model_brier  (>0 ⇒ model better)
    log_loss_skill: Optional[float] = None
    # closing-line agreement (RAW; diagnostic only)
    closing_line: Dict[str, Optional[float]] = field(default_factory=dict)
    reliability_bins: List[Dict[str, Any]] = field(default_factory=list)
    per_game: Dict[str, Dict[str, Optional[float]]] = field(default_factory=dict)

    def passes_skill_gate(self) -> Tuple[bool, List[str]]:
        """PASS iff the model is a genuinely better forecaster than the market.

        P&L-FREE by construction. Fails (with reasons) when the sample is too small, the
        model does not beat the market's Brier by the required margin, or it is miscalibrated.
        """
        reasons: List[str] = []
        if self.n_with_market < SKILL_MIN_N:
            reasons.append(f"n_with_market={self.n_with_market} < SKILL_MIN_N={SKILL_MIN_N}")
        if self.brier_skill is None:
            reasons.append("no market comparison available (need market_price + outcomes)")
        elif self.brier_skill < SKILL_MIN_BRIER_EDGE:
            reasons.append(
                f"brier_skill={self.brier_skill:+.4f} < required edge {SKILL_MIN_BRIER_EDGE:+.4f} "
                f"(model does not beat the market)")
        if self.model_ece is None:
            reasons.append("no ECE (no resolved predictions)")
        elif self.model_ece > SKILL_MAX_ECE:
            reasons.append(f"ECE={self.model_ece:.4f} > SKILL_MAX_ECE={SKILL_MAX_ECE:.4f}")
        return (not reasons), reasons

    def summary(self) -> str:
        def f(x, p="{:.4f}"):
            return "n/a" if x is None else p.format(x)
        lines = [
            f"skill report — n={self.n} (n_with_market={self.n_with_market})",
            f"  model:  Brier {f(self.model_brier)}  logloss {f(self.model_log_loss)}  "
            f"acc {f(self.model_accuracy)}  ECE {f(self.model_ece)}  "
            f"corr {f(self.model_discrimination)}  AUC {f(self.model_auc)}",
            f"  market: Brier {f(self.market_brier)}  logloss {f(self.market_log_loss)}",
            f"  skill:  ΔBrier {f(self.brier_skill, '{:+.4f}')} (>0 ⇒ model beats market)  "
            f"Δlogloss {f(self.log_loss_skill, '{:+.4f}')}",
            f"  closing-line (RAW): n={self.closing_line.get('n')} "
            f"corr {f(self.closing_line.get('correlation'))} "
            f"Δ {f(self.closing_line.get('mean_delta'), '{:+.4f}')}",
        ]
        ok, why = self.passes_skill_gate()
        lines.append(f"  skill gate: {'PASS' if ok else 'FAIL — ' + '; '.join(why)}")
        return "\n".join(lines)


def evaluate(preds: List[dict]) -> SkillReport:
    """Compute the full P&L-free skill report over a list of prediction records."""
    rows = _valid(preds, "p_model")
    r = SkillReport(n=len(rows))
    if not rows:
        return r
    r.model_brier = brier(rows)
    r.model_log_loss = log_loss(rows)
    r.model_accuracy = accuracy(rows)
    r.model_ece, r.reliability_bins = ece(rows)
    r.model_discrimination = discrimination(rows)
    r.model_auc = auc(rows)
    r.closing_line = closing_line_agreement(rows)

    # vs-market: only the subset carrying a usable market_price.
    mkt = _valid([p for p in rows if p.get("market_price") is not None], "market_price")
    r.n_with_market = len(mkt)
    if mkt:
        r.market_brier = brier(mkt, "market_price")
        r.market_log_loss = log_loss(mkt, "market_price")
        mb, mm = brier(mkt, "p_model"), brier(mkt, "market_price")
        if mb is not None and mm is not None:
            r.brier_skill = mm - mb
        lb, lm = log_loss(mkt, "p_model"), log_loss(mkt, "market_price")
        if lb is not None and lm is not None:
            r.log_loss_skill = lm - lb

    # per-game breakdown (Brier + skill where market present).
    games: Dict[str, List[dict]] = {}
    for p in rows:
        games.setdefault(str(p.get("game") or "unknown"), []).append(p)
    for g, gp in games.items():
        gm = [p for p in gp if p.get("market_price") is not None]
        mb = brier(gm, "p_model") if gm else None
        mk = brier(gm, "market_price") if gm else None
        r.per_game[g] = {
            "n": len(gp),
            "model_brier": brier(gp),
            "market_brier": mk,
            "brier_skill": (mk - mb) if (mb is not None and mk is not None) else None,
        }
    return r


if __name__ == "__main__":  # tiny demo on synthetic data (no network, no DB)
    import random  # local: demo only, never in the library path

    rng = random.Random(0)
    demo = []
    for _ in range(500):
        true_p = rng.random()
        y = 1 if rng.random() < true_p else 0
        demo.append({
            "p_model": min(1, max(0, true_p + rng.uniform(-0.08, 0.08))),
            "market_price": min(1, max(0, true_p + rng.uniform(-0.04, 0.04))),
            "team_a_odds": 1.0 / max(0.02, min(0.98, true_p)),
            "outcome": y,
            "game": rng.choice(["cs2", "lol", "dota2"]),
        })
    print(evaluate(demo).summary())
