#!/usr/bin/env python3
"""esports_silo — bet decision + Kelly sizing. **FROM-SCRATCH. PRICE-DEFERRING.**

PLAN #6. Written from scratch; `esports/kelly/esports_bankroll_manager.py` is NOT pulled — it
embeds the banned bet rule inside the sizing function (`:82-83`, `edge = fair_prob -
market_price; if edge <= 0: return 0`). Textbook Kelly is referenced below, nothing carried.

THE FIX FOR THE OLD BOT
-----------------------
The prior bet rule was "bet when `p_model − price` is large." That rule selects the model's
LARGEST ERRORS — exactly when the model is most wrong. The decision here DEFERS TO PRICE:
  * Edge alone NEVER triggers a bet.
  * A bet is allowed ONLY when the model has PROVEN out-of-sample skill (it beat the market's
    Brier on forward data — `eval/skill_metrics.SkillReport.passes_skill_gate`). That proof is
    passed in as `skill_proven`; without it the decision is always `no_bet`.
  * Sizing is fractional Kelly on the proven edge, hard-capped.
  * `SILO_ENTRY_HALT` (config default TRUE) forces `no_bet` regardless — flip only after the
    skill gates pass.

Kelly for a binary YES bought at `price` (cost `price`, pays 1 on win):
    f* = (p − price) / (1 − price)          # standard binary Kelly, ≤0 ⇒ don't bet
Fractional Kelly (`KELLY_FRACTION`, default quarter) tempers estimation error; two hard caps
(`MAX_BET_FRACTION` of bankroll, `MAX_BET_USD`) bound any single bet.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

try:
    from ..config import CONFIG
except ImportError:  # loose-file/test fallback — only .entry_halt is read here
    class _C:  # minimal stand-in
        entry_halt = os.getenv("SILO_ENTRY_HALT", "true").lower() in ("true", "1", "yes")
    CONFIG = _C()  # type: ignore

# --- sizing knobs (env-overridable; behavioural — treat as Tier-1 config) --------------
KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", "0.25"))     # quarter-Kelly by default
MAX_BET_FRACTION = float(os.getenv("MAX_BET_FRACTION", "0.05"))  # ≤5% of bankroll per bet
MAX_BET_USD = float(os.getenv("MAX_BET_USD", "300"))            # hard USD cap per bet
MIN_KELLY_F = float(os.getenv("MIN_KELLY_F", "0.005"))          # below this, edge is noise ⇒ skip


def kelly_fraction(p: Optional[float], price: Optional[float]) -> float:
    """Full-Kelly stake fraction for buying a YES token at `price`. 0.0 if no positive edge.

    f* = (p − price) / (1 − price). Clamped to [0, 1]; 0 for invalid/edgeless inputs.
    """
    try:
        p = float(p)
        price = float(price)
    except (TypeError, ValueError):
        return 0.0
    if not (0.0 < price < 1.0) or not (0.0 <= p <= 1.0):
        return 0.0
    f = (p - price) / (1.0 - price)
    return max(0.0, min(1.0, f))


@dataclass
class Decision:
    action: str                 # 'bet_a' | 'bet_b' | 'no_bet'
    stake_usd: float
    p_model: Optional[float]
    market_price: Optional[float]
    side: Optional[str]         # 'team_a' | 'team_b' | None
    kelly_f: float              # full-Kelly fraction on the chosen side (0 if no bet)
    reason: str


def _size(full_kelly_f: float, bankroll: float) -> float:
    """Fractional Kelly → USD, applying both hard caps. Rounded to cents."""
    frac = full_kelly_f * KELLY_FRACTION
    frac = min(frac, MAX_BET_FRACTION)
    usd = min(frac * max(0.0, bankroll), MAX_BET_USD)
    return round(max(0.0, usd), 2)


def decide(p_model: Optional[float], market_price: Optional[float], bankroll: float,
           *, skill_proven: bool, calibrated: bool = True) -> Decision:
    """Price-deferring bet decision.

    `p_model`      calibrated P(team_a) from the signal (None ⇒ no probability yet).
    `market_price` Polymarket team_a=YES price.
    `skill_proven` did the model beat the market OOS (skill_metrics gate)? Gate for ANY bet.
    `calibrated`   is p_model a real (fitted) probability? (SignalOutput.calibrated)
    """
    no = lambda reason: Decision("no_bet", 0.0, p_model, market_price, None, 0.0, reason)

    if CONFIG.entry_halt:
        return no("SILO_ENTRY_HALT is set — entries halted until skill gates pass")
    if not calibrated or p_model is None:
        return no("signal not calibrated — no probability to act on (Cmd 4)")
    if market_price is None or not (0.0 < float(market_price) < 1.0):
        return no("no usable market price")
    if not skill_proven:
        # THE FIX: never bet on an unproven model, no matter how large the edge.
        return no("model skill not proven out-of-sample — deferring to price")

    price = float(market_price)
    f_a = kelly_fraction(p_model, price)              # buy team_a=YES at `price`
    f_b = kelly_fraction(1.0 - p_model, 1.0 - price)  # buy team_b=YES at `1-price`

    if max(f_a, f_b) < MIN_KELLY_F:
        return no(f"edge below MIN_KELLY_F={MIN_KELLY_F} — not worth the risk")
    if f_a >= f_b:
        return Decision("bet_a", _size(f_a, bankroll), p_model, market_price, "team_a", f_a,
                        f"proven skill + positive Kelly on team_a (f*={f_a:.4f})")
    return Decision("bet_b", _size(f_b, bankroll), p_model, market_price, "team_b", f_b,
                    f"proven skill + positive Kelly on team_b (f*={f_b:.4f})")


if __name__ == "__main__":  # demo (no network/DB)
    print("entry_halt =", CONFIG.entry_halt)
    for kw in (dict(skill_proven=False), dict(skill_proven=True)):
        d = decide(0.70, 0.55, 1000.0, calibrated=True, **kw)
        print(f"  skill_proven={kw['skill_proven']}: {d.action} ${d.stake_usd} — {d.reason}")
