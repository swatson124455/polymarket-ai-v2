"""Adversarial dry_run smoke: real KalshiOrderClient (dry_run), mocked public_get.
Verifies: uses simulated_standing, own={}, never calls _flatten_all/_held_cost/
get_positions/get_balance, logs a cycle line, plan shape intact, multi-cycle
simulated_standing evolves and quiesces (no churn on unchanged book)."""
import importlib.util, os, sys, json, tempfile, io, contextlib

def _load(n):
    s = importlib.util.spec_from_file_location(n, f"{n}.py")
    m = importlib.util.module_from_spec(s); sys.modules[n] = m; s.loader.exec_module(m); return m

q = _load("maker_kalshi_quoter")
cli = _load("maker_kalshi_client")

# --- forbidden-call tripwires ---
FORBIDDEN = {"_flatten_all": 0, "_held_cost": 0, "get_positions": 0, "get_balance": 0}
_orig_flatten = q._flatten_all
_orig_held = q._held_cost
def _spy_flatten(c):
    FORBIDDEN["_flatten_all"] += 1; return _orig_flatten(c)
def _spy_held(c):
    FORBIDDEN["_held_cost"] += 1; return _orig_held(c)
q._flatten_all = _spy_flatten
q._held_cost = _spy_held

# wrap real dry_run client so get_positions/get_balance trip the wire
RealClient = cli.KalshiOrderClient
class SpyClient(RealClient):
    def get_positions(self):
        FORBIDDEN["get_positions"] += 1; return super().get_positions()
    def get_balance(self):
        FORBIDDEN["get_balance"] += 1; return super().get_balance()

os.environ.pop("KALSHI_TRADING_MODE", None)  # force dry_run

# mocked market data: 2 programs, healthy books
PROGS = {"incentive_programs": [
    {"incentive_type": "liquidity", "market_ticker": "AAA-1", "target_size_fp": 5,
     "discount_factor_bps": 1, "period_reward": 1000000, "start_date": "2026-07-01T00:00:00Z",
     "end_date": "2099-01-01T00:00:00Z"},
    {"incentive_type": "liquidity", "market_ticker": "BBB-1", "target_size_fp": 5,
     "discount_factor_bps": 1, "period_reward": 500000, "start_date": "2026-07-01T00:00:00Z",
     "end_date": "2099-01-01T00:00:00Z"},
], "next_cursor": ""}
OB = {"orderbook_fp": {"yes_dollars": [["0.50", "9999"]], "no_dollars": [["0.48", "9999"]]}}

def fake_public_get(path):
    q._reads[0] += 1
    return PROGS if "incentive" in path else OB

def run_cycle(tmpdir):
    q.DATA_DIR = tmpdir
    q.STOP_FILE = os.path.join(tmpdir, "STOP")
    q.STATE_FILE = os.path.join(tmpdir, "quoter_state.json")
    q.public_get = fake_public_get
    q.KalshiOrderClient = SpyClient
    q.FOOTPRINT_TOP = 40; q.JOIN_SIZE = 20
    q.MAX_MARKET_CAPITAL = 15.0; q.MAX_TOTAL_CAPITAL = 40.0
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = q.run_once()
    line = buf.getvalue().strip()
    rows = []
    for p in os.listdir(tmpdir):
        if p.startswith("plans-"):
            for l in open(os.path.join(tmpdir, p)):
                rows.append(json.loads(l))
    st = json.load(open(q.STATE_FILE)) if os.path.exists(q.STATE_FILE) else {}
    return rc, line, rows, st

d = tempfile.mkdtemp()
print("=== CYCLE 1 ===")
rc, line, rows, st = run_cycle(d)
plan1 = rows[-1]
print("rc:", rc)
print("stdout:", repr(line))
print("mode:", plan1.get("mode"))
print("plan keys:", sorted(plan1.keys()))
print("creates:", plan1.get("creates"), "cancels:", plan1.get("cancels"),
      "committed:", plan1.get("committed_usd"), "held:", plan1.get("held_cost_usd"))
print("sim_standing tickers:", list(st.get("simulated_standing", {}).keys()))

print("\n=== CYCLE 2 (unchanged book — should quiesce, 0 churn) ===")
rc2, line2, rows2, st2 = run_cycle(d)
plan2 = rows2[-1]
print("stdout:", repr(line2))
print("cancels:", plan2.get("cancels"), "creates:", plan2.get("creates"),
      "order_ops:", plan2.get("order_ops"))
print("sim_standing unchanged tickers:", list(st2.get("simulated_standing", {}).keys()))

print("\n=== FORBIDDEN CALL TRIPWIRES ===")
print(FORBIDDEN)

# assertions
EXPECTED_KEYS = set(json.loads(
    '{"ts":1,"mode":1,"footprint":1,"quoted_markets":1,"cancels":1,"creates":1,'
    '"order_ops":1,"write_tokens":1,"reads":1,"gated_out":1,"fetch_failed":1,'
    '"capped_markets":1,"budget_dropped_markets":1,"cancel_fail":1,"create_fail":1,'
    '"create_skipped":1,"quote_fail":1,"first_quote_err":1,"dropped_book_rows":1,'
    '"activate_markets":1,"est_capital_usd":1,"committed_usd":1,"held_cost_usd":1}').keys())

problems = []
if plan1.get("mode") != "dry_run": problems.append("mode not dry_run")
if not line: problems.append("no cycle line logged")
if EXPECTED_KEYS - set(plan1.keys()): problems.append(f"missing plan keys: {EXPECTED_KEYS - set(plan1.keys())}")
if any(v for v in FORBIDDEN.values()): problems.append(f"forbidden calls: {FORBIDDEN}")
if plan1.get("creates", 0) == 0: problems.append("cycle-1 created nothing (dry_run place path dead?)")
if plan2.get("order_ops", 99) != 0: problems.append(f"cycle-2 churned ops={plan2.get('order_ops')} (should quiesce)")
if not st.get("simulated_standing"): problems.append("simulated_standing not persisted")

print("\n=== VERDICT ===")
print("PROBLEMS:", problems if problems else "NONE — dry_run intact")
