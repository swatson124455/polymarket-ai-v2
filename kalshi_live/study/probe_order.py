"""Measure the shaping pipeline stage by stage instead of reasoning about it.

Twice I assumed what y_cnt/n_cnt were at the OFFSET step and was wrong. This instruments
the real function by monkeypatching the helpers it calls, so every stage prints what it
actually produced under each regime.
"""
import importlib.util, sys

spec = importlib.util.spec_from_file_location("q", "maker_kalshi_quoter.py")
q = importlib.util.module_from_spec(spec)
sys.modules["q"] = q
spec.loader.exec_module(q)

YL = [["0.33", "5000"]]
NL = [["0.65", "5000"]]
M = {"target": 1000, "end": "2099-01-01T00:00:00Z", "usd_day": 5.0, "ramp_min": 180}

q.JOIN_SIZE = 100
q.MAX_MARKET_CAPITAL = 250.0
q.MIN_QUOTE_CT = 2
q.INV_TOLERANCE = 1.0


def run(label, inv, standdown, soft, hard, minq=2):
    q.STANDDOWN = standdown
    q.STANDDOWN_MIN_USD_DAY = 20.0
    q.INV_SOFT_CT = soft
    q.INV_HARD_CT = hard
    q.MIN_QUOTE_CT = minq
    seen = {}
    orig_thr = q._throttled_quote
    orig_unw = q._unwind_size

    def thr(best, cnt, over, levels, target):
        seen["before_throttle_add"] = cnt
        r = orig_thr(best, cnt, over, levels, target)
        seen["after_throttle_add"] = r[1]
        return r

    def unw(base, price, i):
        seen["offset_base_arg"] = base
        r = orig_unw(base, price, i)
        seen["offset_result"] = r
        return r

    q._throttled_quote, q._unwind_size = thr, unw
    try:
        qs = {x["side"]: x for x in q.desired_quotes(M, YL, NL, q.utcnow(), inv=inv, cost=0.33)}
    finally:
        q._throttled_quote, q._unwind_size = orig_thr, orig_unw
    y = qs.get("yes", {}).get("count", 0)
    n = qs.get("no", {}).get("count", 0)
    add, red = (y, n) if inv > 0 else (n, y)
    print(f"{label:<34} inv={inv:>5.0f} -> yes={y:<5} no={n:<5} "
          f"| ADD={add:<5} RED={red:<5} net_after_double_fill={inv + y - n:>6.0f}  {seen}")


print("JOIN_SIZE=100, MIN_QUOTE_CT=2, MAX_MARKET_CAPITAL=250\n")
run("plain, inv below SOFT", 8, False, 15.0, 60.0)
run("plain, inv above SOFT", 40, False, 15.0, 60.0)
run("standdown ON (the failing test)", 40, True, 30.0, 80.0)
run("standdown ON, low inv", 8, True, 30.0, 80.0)
run("hard stop", 90, False, 15.0, 60.0)
run("flat", 0, False, 15.0, 60.0)
run("short below SOFT", -8, False, 15.0, 60.0)
