#!/usr/bin/env python3
"""Self-contained tests for resolution/skill + paper execution — stdlib asserts only.

Run:  python -m esports_silo.execution.test_execution   (exit 0 = all pass)
"""
from __future__ import annotations

try:
    from . import resolution as res
    from . import paper as pp
except ImportError:
    import resolution as res  # type: ignore
    import paper as pp  # type: ignore

FAILS = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


# --- resolve: only scorable (resolved winner + p_model) rows kept, clean YES axis ----
preds = [
    {"match_id": "m1", "p_model": 0.7, "market_price": 0.6, "decision": "bet_a", "side": "team_a"},
    {"match_id": "m2", "p_model": 0.3, "market_price": 0.5, "decision": "bet_b", "side": "team_b"},
    {"match_id": "m3", "p_model": 0.5, "market_price": 0.5, "decision": "no_bet"},   # unresolved match
    {"match_id": "m4", "p_model": None, "market_price": 0.5, "decision": "no_bet"},  # no signal
]
winners = {"m1": "team_a", "m2": "team_a", "m3": None, "m4": "team_b"}
r = res.resolve(preds, winners)
check("only resolved+signal rows kept", len(r) == 2 and {x["match_id"] for x in r} == {"m1", "m2"})
check("outcome on YES axis (team_a win -> 1)", r[0]["outcome"] == 1)
check("m2 team_a won -> outcome 1", [x for x in r if x["match_id"] == "m2"][0]["outcome"] == 1)
check("unresolved match dropped", all(x["match_id"] != "m3" for x in r))
check("no-signal prediction dropped", all(x["match_id"] != "m4" for x in r))

# resolve accepts dataclass-like objects too
class P:
    def __init__(s, **k): s.__dict__.update(k)
obj = [P(match_id="mx", p_model=0.8, market_price=0.55, decision="bet_a", side="team_a")]
check("resolve reads attr objects", res.resolve(obj, {"mx": "team_b"})[0]["outcome"] == 0)

# --- skill_report: SKILL, not P&L ----------------------------------------------------
# model perfect, market at 0.5 -> model beats market on Brier
scorable = [{"p_model": 1.0, "market_price": 0.5, "outcome": 1},
            {"p_model": 0.0, "market_price": 0.5, "outcome": 0}]
rep = res.skill_report(scorable)
check("skill_report returns a report", rep is not None and rep.n == 2)
check("brier_skill > 0 (model beats market)", (rep.brier_skill or 0) > 0)
check("skill report exposes no pnl attr", not hasattr(rep, "pnl") and not hasattr(rep, "roi"))

# --- bet_hit_rate: diagnostic only ---------------------------------------------------
hr = res.bet_hit_rate(r)  # m1 bet_a & team_a won -> hit; m2 bet_b but team_a won -> miss
check("hit_rate counts bets only", hr["n_bets"] == 2)
check("hit_rate value correct (1/2)", abs(hr["hit_rate"] - 0.5) < 1e-9)
check("hit_rate labelled diagnostic-not-evidence", "NOT evidence" in hr["note"])
check("no bets -> hit_rate None", res.bet_hit_rate([])["hit_rate"] is None)

# --- paper execution -----------------------------------------------------------------
bet = {"match_id": "m1", "market_id": "0x1", "decision": "bet_a", "side": "team_a",
       "stake_usd": 25.0, "market_price": 0.6, "reason": "proven"}
o = pp.execute_paper(bet)
check("bet -> paper_filled", o.status == "paper_filled" and o.side == "team_a")
check("paper real execution is $0 (only paper/live diff)", o.real_execution_usd == 0.0)
check("team_a entry price = market price", abs(o.entry_price - 0.6) < 1e-9)
# team_b entry = 1 - price
betb = {"match_id": "m2", "decision": "bet_b", "side": "team_b", "stake_usd": 10.0,
        "market_price": 0.6}
ob = pp.execute_paper(betb)
check("team_b entry price = 1 - market price", abs(ob.entry_price - 0.4) < 1e-9)
# no_bet -> no order
no = pp.execute_paper({"decision": "no_bet", "market_price": 0.5, "reason": "halted"})
check("no_bet -> no_order", no.status == "no_order" and no.stake_usd == 0.0)
check("paper order carries no pnl field", not hasattr(o, "pnl") and not hasattr(o, "profit"))

if __name__ == "__main__":
    import sys
    print(f"\n{'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)
