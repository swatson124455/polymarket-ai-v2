"""Unit tests for maker_onchain_recon — the on-chain ledger reconciler.

Two things carry the weight here.

1. The DIFFERENTIAL FUZZ: the reconciler replays the fills ledger with its own
   copy of the engine's net arithmetic, and a copy that drifts by one tick
   would mis-report the cost basis that feeds the event cap and the day-loss
   floor. So the replay is checked against the engine's real
   `_apply_live_trades` over randomized fills, across MULTIPLE batches with
   the state JSON round-tripped between them — the accumulation-order axis the
   engine's own one-tick bug lived on. Same standard as the engine's ship-gate.

2. The REPORTING CONTRACT: an adversarial review found six ways a real
   inventory loss exited 0 (skipped check, market missing from state, zero
   tokens compared, drift computed but unprinted, baselined shortfall,
   double-counted rotation). Each has a named regression test below. A check
   that could not run must never share an exit code with a check that passed.

No network access anywhere in this file.
"""
import importlib.util
import json
import pathlib
import random

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rec = _load("recon", "scripts/maker_onchain_recon.py")
mle = _load("mle", "scripts/maker_live_engine.py")

ADDR = "0xmaker"
WALLET = "0xBB3988D74a853ddC16f22eEC52fa53E3Cedd2247"


class FakeExec:
    def __init__(self, trades):
        self.address = ADDR
        self._trades = trades

    def fetch_my_trades(self):
        return self._trades


def _trade(tid, tok, px, sz, ts):
    return {"id": tid, "timestamp": ts,
            "maker_orders": [{"maker_address": ADDR, "asset_id": tok,
                              "side": "BUY", "price": px,
                              "matched_amount": sz}]}


def _write(base, name, rows):
    with open(pathlib.Path(base) / name, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _replay(base):
    events, _, _ = rec.load_events(str(base))
    return rec.replay_events(events)


# ───────────────────────────── row classification ────────────────────────────

def test_anomaly_rows_are_never_replayed(tmp_path):
    """Anomaly rows record what the engine REFUSED to apply. Replaying them
    would invent inventory the engine never booked."""
    _write(tmp_path, "fills-20260720.jsonl", [
        {"t": 1, "no_id": "junk"},
        {"t": 1, "tid": "x", "no_timestamp": "junk"},
        {"t": 1, "tid": "x", "no_our_leg": "junk"},
        {"t": 1, "tid": "x", "unparsed": "junk"},
        {"t": 1, "tid": "x", "unmatched": "junk"},
        {"t": 1, "tid": "x", "trade_exc": "boom"}])
    gross, net, meta = _replay(tmp_path)
    assert gross == {} and net == {}
    assert meta["stats"]["skip"] == 6


def test_gross_ignores_netting_and_net_applies_it(tmp_path):
    """50 YES + 50 NO nets to zero inventory but leaves 50+50 tokens on
    chain — the whole reason two views exist."""
    _write(tmp_path, "fills-20260720.jsonl", [
        {"t": 1, "tid": "a", "mkt": "m", "leg": "yes", "px": 0.5, "sz": 50.0},
        {"t": 2, "tid": "b", "mkt": "m", "leg": "no", "px": 0.5, "sz": 50.0}])
    gross, net, _ = _replay(tmp_path)
    assert gross["m"] == {"yes": 50.0, "no": 50.0}
    assert net["m"]["y"] == 0.0 and net["m"]["n"] == 0.0
    assert net["m"]["merged"] == 50.0 and net["m"]["spent"] == 0.0


# ───────────── settlement / mode transitions (reviewer 1, F1-F3) ─────────────

def test_settlement_is_replayed_not_skipped(tmp_path):
    """F1 root fix. try_settle zeroes y/n and nets spend; replaying the
    settlements ledger reproduces that, so no 'skip settled markets' hack is
    needed — and gross still remembers the tokens, which are on chain until
    someone redeems them."""
    _write(tmp_path, "fills-20260720.jsonl", [
        {"t": 1, "tid": "a", "mkt": "m", "leg": "yes", "px": 0.4, "sz": 100.0}])
    _write(tmp_path, "settlements-20260720.jsonl", [
        {"t": 5, "mkt": "m", "payout": 100.0, "realized": 60.0}])
    gross, net, _ = _replay(tmp_path)
    assert net["m"]["y"] == 0.0 and net["m"]["spent"] == -60.0
    assert gross["m"]["yes"] == 100.0
    state = {"meta": {}, "m": {"y": 0.0, "n": 0.0, "spent": -60.0, "settled": True}}
    assert rec.check_state_vs_ledger(state, net, set()) == ([], [])


def test_late_fill_after_settlement_reconciles(tmp_path):
    """F1's concrete failure: the engine re-opens a settled market on a late
    fill (maker_live_engine.py:2127). The old settled-skip then replayed the
    whole pre-settlement history and reported permanent phantom drift."""
    _write(tmp_path, "fills-20260720.jsonl", [
        {"t": 1, "tid": "a", "mkt": "m", "leg": "yes", "px": 0.4, "sz": 100.0},
        {"t": 9, "tid": "b", "mkt": "m", "leg": "yes", "px": 0.5, "sz": 10.0}])
    _write(tmp_path, "settlements-20260720.jsonl", [
        {"t": 5, "mkt": "m", "payout": 100.0}])
    _, net, _ = _replay(tmp_path)
    assert net["m"]["y"] == 10.0
    assert net["m"]["spent"] == -55.0
    state = {"meta": {}, "m": {"y": 10.0, "n": 0.0, "spent": -55.0, "settled": False}}
    assert rec.check_state_vs_ledger(state, net, set()) == ([], [])


def test_mixed_paper_then_live_ledger_reconciles(tmp_path):
    """F2. A base dir that traded paper and then live must not report every
    paper market as drift the moment one live row appears. Paper rows are
    absolute state; live rows are deltas on top."""
    _write(tmp_path, "fills-20260719.jsonl", [
        {"t": 1, "mkt": "m", "n": 3, "y": 200.0, "nn": 0.0, "spent": 60.0}])
    _write(tmp_path, "fills-20260720.jsonl", [
        {"t": 9, "tid": "b", "mkt": "m", "leg": "yes", "px": 0.5, "sz": 10.0}])
    _, net, meta = _replay(tmp_path)
    assert net["m"]["y"] == 210.0 and net["m"]["spent"] == 65.0
    state = {"meta": {}, "m": {"y": 210.0, "n": 0.0, "spent": 65.0}}
    assert rec.check_state_vs_ledger(state, net, set()) == ([], [])
    assert "m" in meta["paper_tainted"]


def test_paper_history_does_not_exclude_a_market_from_the_chain_check(tmp_path):
    """HIGH-4. Paper fills bought nothing on chain and contribute nothing to
    `gross`, so for a paper-then-live market `gross` is already exactly the
    live legs. Excluding these made rc=0 — documented as the only go-signal —
    unreachable on the real pilot base dir, which is paper-then-live in ONE
    directory."""
    _write(tmp_path, "fills-20260719.jsonl", [
        {"t": 1, "mkt": "m", "n": 3, "y": 200.0, "nn": 0.0, "spent": 60.0}])
    _write(tmp_path, "fills-20260720.jsonl", [
        {"t": 9, "tid": "b", "mkt": "m", "leg": "yes", "px": 0.5, "sz": 10.0}])
    gross, _, _ = _replay(tmp_path)
    state = {"meta": {}, "m": {"tok_y": "111", "tok_n": "222"}}
    # chain holds ONLY the live leg: the paper 200 never existed on chain
    short, surp, skipped, compared = rec.check_chain_vs_gross(
        state, gross, {"111": 10.0, "222": 0.0}, {})
    assert short == [] and surp == [] and skipped == []
    assert compared == 2


def test_same_second_fill_and_settlement_is_ambiguous_not_drift(tmp_path):
    """Whole-second ledger timestamps cannot order a fill against a
    settlement. Reporting a guess as drift would cry wolf forever."""
    _write(tmp_path, "fills-20260720.jsonl", [
        {"t": 5, "tid": "a", "mkt": "m", "leg": "yes", "px": 0.4, "sz": 100.0}])
    _write(tmp_path, "settlements-20260720.jsonl", [
        {"t": 5, "mkt": "m", "payout": 100.0}])
    _, net, meta = _replay(tmp_path)
    assert "m" in meta["ambiguous"]
    state = {"meta": {}, "m": {"y": 100.0, "n": 0.0, "spent": -60.0}}
    drift, amb = rec.check_state_vs_ledger(state, net, meta["ambiguous"])
    assert drift == [] and len(amb) == 1


# ────────────────────────────── differential fuzz ────────────────────────────

@pytest.mark.parametrize("seed", range(60))
def test_replay_matches_engine_arithmetic_across_batches(seed, tmp_path):
    """F7-hardened: the engine is called in SEVERAL batches with its state
    JSON round-tripped between them, so it seeds `nv` from persisted state
    exactly as it does in production. Replaying from zero on both sides would
    have hidden the accumulation-order bug class this test exists for."""
    rng = random.Random(seed)
    base = str(tmp_path)
    toks = ["TY", "TN", "UY", "UN"]
    state = {"m1": {"tok_y": "TY", "tok_n": "TN"},
             "m2": {"tok_y": "UY", "tok_n": "UN"}}
    meta = {}

    trades, ts, tid = [], 1_700_000_000.0, 0
    for _ in range(rng.randint(1, 8)):            # several batches
        batch = []
        for _ in range(rng.randint(1, 6)):
            ts += rng.uniform(0.1, 5.0)
            batch.append(_trade(f"t{tid}", rng.choice(toks),
                                round(rng.randrange(1, 999) / 1000.0, 3),
                                round(rng.uniform(0.01, 500.0), 4), ts))
            tid += 1
        trades.append(batch)

    for batch in trades:
        mle._apply_live_trades(FakeExec(batch), state, [], base, meta)
        # round-trip exactly like a save/restart would
        state = json.loads(json.dumps(state))
        meta = json.loads(json.dumps(meta))

    _, net, _ = _replay(base)
    for key in ("m1", "m2"):
        st = state[key]
        exp = net.get(key, {"y": 0.0, "n": 0.0, "spent": 0.0, "merged": 0.0})
        for field, engine_field in (("y", "y"), ("n", "n"),
                                    ("spent", "spent"), ("merged", "merged")):
            assert st.get(engine_field, 0.0) == exp[field], \
                f"{key}.{field} (seed {seed})"


@pytest.mark.parametrize("seed", range(20))
def test_gross_equals_unnetted_leg_sums(seed, tmp_path):
    """Gross is the chain expectation, so it must be the raw sum of legs —
    never touched by netting or settlement."""
    rng = random.Random(seed)
    rows, exp_y, exp_n = [], 0.0, 0.0
    for i in range(rng.randint(1, 30)):
        leg = rng.choice(("yes", "no"))
        sz = round(rng.uniform(0.01, 500.0), 4)
        rows.append({"t": i + 1, "tid": f"t{i}", "mkt": "m", "leg": leg,
                     "px": round(rng.randrange(1, 999) / 1000.0, 3), "sz": sz})
        if leg == "yes":
            exp_y += sz
        else:
            exp_n += sz
    rows.append({"t": 999, "mkt": "m", "payout": 12.0})
    _write(tmp_path, "fills-20260720.jsonl", [r for r in rows if "leg" in r])
    _write(tmp_path, "settlements-20260720.jsonl", [r for r in rows if "leg" not in r])
    gross, net, _ = _replay(tmp_path)
    assert gross["m"]["yes"] == pytest.approx(exp_y)
    assert gross["m"]["no"] == pytest.approx(exp_n)
    assert net["m"]["y"] == 0.0 and net["m"]["n"] == 0.0     # settled


# ─────────────────── reporting contract regressions (reviewer 2) ─────────────

def test_ledger_without_state_is_drift_not_clean(tmp_path):
    """R2 (CRITICAL). state.json recovered from .bak can predate the ledger —
    a designed-for condition. A state-only loop reported 300 shares as CLEAN
    twice and exited 0."""
    _write(tmp_path, "fills-20260720.jsonl", [
        {"t": 1, "tid": "a", "mkt": "m9", "leg": "yes", "px": 0.4, "sz": 300.0}])
    _, net, _ = _replay(tmp_path)
    drift, _ = rec.check_state_vs_ledger({"meta": {}}, net, set())
    assert len(drift) == 1 and drift[0]["kind"] == "ledger_without_state"


def test_state_without_ledger_counts_spent_not_just_shares():
    """R11/F4. merge and settle both zero y/n and leave realized dollars in
    `spent` — the most common post-merge shape was the one shape this branch
    could not see."""
    drift, _ = rec.check_state_vs_ledger(
        {"meta": {}, "m": {"y": 0.0, "n": 0.0, "spent": 4000.0}}, {}, set())
    assert len(drift) == 1 and drift[0]["kind"] == "state_without_ledger"


def test_report_rc_is_worst_status():
    r = rec.Report()
    r.add("a", rec.PASS, "fine")
    assert r.rc() == 0
    r.add("b", rec.SKIP, "did not run")
    assert r.rc() == 4
    r.add("c", rec.DRIFT, "bad")
    assert r.rc() == 2


def test_skipped_check_never_shares_exit_code_with_passed():
    """R1 (CRITICAL). cron does not inherit an interactive shell, so an unset
    MAKER_FUNDER was the likeliest way this tool checked nothing and said so
    with rc=0."""
    r = rec.Report()
    r.add("STATE vs LEDGER", rec.PASS, "fine")
    r.add("CHAIN vs LEDGER", rec.SKIP, "no wallet")
    assert r.rc() == 4


def test_surplus_only_is_rc3_and_drift_dominates_it():
    r = rec.Report()
    r.add("CHAIN vs LEDGER", rec.PASS, "no shortfall")
    r.surplus_only = True
    assert r.rc() == 3
    r.add("STATE vs LEDGER", rec.DRIFT, "bad")
    assert r.rc() == 2


def test_rotation_duplicate_is_deduped_to_the_gz(tmp_path):
    """R6. The engine rotates write-then-delete and swallows the failure. If
    both files survive, replaying both doubles gross and reports a 100%-short
    chain on a healthy engine."""
    import gzip as _gz
    row = {"t": 1, "tid": "a", "mkt": "m", "leg": "yes", "px": 0.4, "sz": 100.0}
    _write(tmp_path, "fills-20260719.jsonl", [row])
    with _gz.open(tmp_path / "fills-20260719.jsonl.gz", "wt") as fh:
        fh.write(json.dumps(row) + "\n")
    paths, dupes = rec.ledger_paths(str(tmp_path))
    assert len(paths) == 1 and paths[0].endswith(".gz")
    assert dupes == ["fills-20260719.jsonl"]
    gross, _, _ = _replay(tmp_path)
    assert gross["m"]["yes"] == 100.0          # not 200


def test_truncated_gzip_warns_and_does_not_kill_the_run(tmp_path):
    """R12. A .gz truncated by a crashed rotation raises EOFError, which the
    OSError handler did not cover."""
    (tmp_path / "fills-20260719.jsonl.gz").write_bytes(b"\x1f\x8b\x08\x00garbage")
    _write(tmp_path, "fills-20260720.jsonl", [
        {"t": 1, "tid": "a", "mkt": "m", "leg": "yes", "px": 0.4, "sz": 5.0}])
    gross, _, _ = _replay(tmp_path)
    assert gross["m"]["yes"] == 5.0


# ──────────────────────── chain-vs-gross decision layer ──────────────────────

def _state_two_tokens():
    return {"meta": {}, "m": {"tok_y": "111", "tok_n": "222"}}


def test_shortfall_is_drift():
    gross = {"m": {"yes": 100.0, "no": 0.0}}
    short, surp, _, _ = rec.check_chain_vs_gross(
        _state_two_tokens(), gross, {"111": 60.0, "222": 0.0}, {})
    assert len(short) == 1 and short[0]["delta"] == -40.0
    assert short[0]["direction"] == "short" and surp == []


def test_baselined_shortfall_is_still_drift():
    """R5 (HIGH). `base > 0` short-circuited on sign, so a token drained to
    exactly its baseline — Maker's entire position gone — printed as a
    'surplus' of +-100.0 and exited 3 instead of 2."""
    gross = {"m": {"yes": 100.0, "no": 0.0}}
    short, surp, _, _ = rec.check_chain_vs_gross(
        _state_two_tokens(), gross, {"111": 75.0, "222": 0.0}, {"111": 75.0})
    assert len(short) == 1 and short[0]["delta"] == -100.0
    assert surp == []


def test_surplus_without_baseline_is_drift_not_ambiguity():
    """F5. On a dedicated wallet, tokens we never booked are a fault — a
    missed fill or an unbooked transfer — not shared-wallet ambiguity."""
    gross = {"m": {"yes": 100.0, "no": 0.0}}
    short, surp, _, _ = rec.check_chain_vs_gross(
        _state_two_tokens(), gross, {"111": 175.0, "222": 0.0}, {})
    assert len(short) == 1 and short[0]["direction"] == "over"
    assert surp == []


def test_surplus_with_baseline_is_ambiguous():
    gross = {"m": {"yes": 100.0, "no": 0.0}}
    short, surp, _, _ = rec.check_chain_vs_gross(
        _state_two_tokens(), gross, {"111": 200.0, "222": 0.0}, {"111": 75.0})
    assert short == [] and len(surp) == 1


def test_chain_balance_with_no_ledger_entry_is_reported():
    """The case a gross-driven loop missed entirely: tokens on chain in a
    market our ledger says we never traded."""
    short, surp, _, _ = rec.check_chain_vs_gross(
        _state_two_tokens(), {}, {"111": 42.0, "222": 0.0}, {})
    assert len(short) == 1 and short[0]["delta"] == 42.0


def test_unread_token_is_skipped_and_counted_not_silently_clean():
    """R3. Tokens absent from the chain dict were skipped silently; the run
    then printed CLEAN having compared nothing."""
    gross = {"m": {"yes": 100.0, "no": 0.0}}
    short, surp, skipped, _ = rec.check_chain_vs_gross(
        _state_two_tokens(), gross, {}, {})
    assert short == [] and surp == []
    assert len(skipped) == 2


def test_fills_without_token_mapping_are_flagged():
    """F9. _apply_live_trades can create a market entry that never went
    through the fast loop, so it has no tok_y/tok_n and is unreadable."""
    gross = {"m": {"yes": 100.0, "no": 0.0}}
    short, surp, skipped, _ = rec.check_chain_vs_gross(
        {"meta": {}, "m": {}}, gross, {"111": 0.0}, {})
    assert short == [] and len(skipped) == 1
    assert "tok_y" in skipped[0]["why"]


def test_dust_is_not_drift():
    gross = {"m": {"yes": 100.0, "no": 0.0}}
    short, surp, _, _ = rec.check_chain_vs_gross(
        _state_two_tokens(), gross, {"111": 100.005, "222": 0.0}, {})
    assert short == [] and surp == []


# ───────────────────────────────── chain layer ───────────────────────────────

class _StubChain(rec.Chain):
    def __init__(self, responder, rpcs="http://a,http://b"):
        super().__init__(rpcs)
        self.responder = responder
        self.seen = []

    def _post(self, rpc, payload):
        self.seen.append((rpc, payload))
        return self.responder(rpc, payload)


def _ok(val):
    return lambda rpc, payload: [{"jsonrpc": "2.0", "id": p["id"],
                                  "result": "0x" + format(val, "064x")}
                                 for p in payload]


def test_balances_encode_and_decode_correctly():
    captured = {}

    def responder(rpc, payload):
        captured["data"] = payload[0]["params"][0]["data"]
        return _ok(123_456_789)(rpc, payload)

    out = _StubChain(responder).balances(WALLET, ["5"])
    assert out["5"] == pytest.approx(123.456789)
    d = captured["data"]
    assert d.startswith(rec.SEL_BALANCEOF1155)
    assert d[10:74].endswith("bb3988d74a853ddc16f22eec52fa53e3cedd2247")
    assert int(d[74:], 16) == 5


def test_balances_match_by_id_not_by_order():
    """An out-of-order batch zipped positionally assigns balances to the
    wrong tokens — silent, total corruption."""
    def responder(rpc, payload):
        out = [{"jsonrpc": "2.0", "id": p["id"],
                "result": "0x" + format(int(p["params"][0]["data"][74:], 16)
                                        * 1_000_000, "064x")} for p in payload]
        out.reverse()
        return out

    assert _StubChain(responder).balances(WALLET, ["7", "9", "3"]) == \
        {"7": 7.0, "9": 9.0, "3": 3.0}


def test_rpc_error_fails_over_then_raises_never_returns_zeros():
    def responder(rpc, payload):
        return [{"jsonrpc": "2.0", "id": p["id"],
                 "error": {"code": -32000, "message": "nope"}} for p in payload]

    c = _StubChain(responder)
    with pytest.raises(RuntimeError, match="all RPCs failed"):
        c.balances(WALLET, ["1"])
    assert len(c.seen) == 2


def test_empty_0x_result_fails_over_instead_of_crashing():
    """R7. eth_call against an address with no code returns '0x' with no
    error object. Decoding that outside the failover loop killed the run on
    RPC #1 instead of trying RPC #2 — defeating the whole RPC list."""
    def responder(rpc, payload):
        if rpc == "http://a":
            return [{"jsonrpc": "2.0", "id": p["id"], "result": "0x"}
                    for p in payload]
        return _ok(3_000_000)(rpc, payload)

    c = _StubChain(responder)
    assert c.balances(WALLET, ["1"]) == {"1": 3.0}
    assert len(c.seen) == 2


def test_all_rpcs_returning_0x_raises_runtimeerror():
    def responder(rpc, payload):
        return [{"jsonrpc": "2.0", "id": p["id"], "result": "0x"} for p in payload]

    with pytest.raises(RuntimeError, match="wrong chain or wrong contract"):
        _StubChain(responder).balances(WALLET, ["1"])


def test_short_batch_response_is_a_failure_not_partial_data():
    def responder(rpc, payload):
        return [{"jsonrpc": "2.0", "id": 0, "result": "0x" + "0" * 64}]

    with pytest.raises(RuntimeError):
        _StubChain(responder).balances(WALLET, ["1", "2"])


def test_duplicate_ids_are_a_failure():
    def responder(rpc, payload):
        return [{"jsonrpc": "2.0", "id": 0, "result": "0x" + "0" * 64}
                for _ in payload]

    with pytest.raises(RuntimeError, match="missing id"):
        _StubChain(responder).balances(WALLET, ["1", "2"])


def test_second_rpc_rescues_a_failed_first():
    def responder(rpc, payload):
        if rpc == "http://a":
            raise OSError("connection reset")
        return _ok(2_000_000)(rpc, payload)

    assert _StubChain(responder).balances(WALLET, ["1"]) == {"1": 2.0}


def test_chunking_covers_every_token():
    def responder(rpc, payload):
        return [{"jsonrpc": "2.0", "id": p["id"],
                 "result": "0x" + format(int(p["params"][0]["data"][74:], 16)
                                         * 1_000_000, "064x")} for p in payload]

    c = _StubChain(responder)
    got = c.balances(WALLET, [str(i) for i in range(1, 251)])
    assert len(got) == 250 and got["250"] == 250.0
    assert len(c.seen) == 3


def test_deadline_stops_the_chain_read():
    c = _StubChain(_ok(0))
    c.deadline = 0.0
    with pytest.raises(RuntimeError, match="deadline"):
        c.balances(WALLET, ["1"])


# ─────────────────────────────── address safety ──────────────────────────────

@pytest.mark.parametrize("bad", ["", "0xabc", "abc", None,
                                 "0xZZ3988D74a853ddC16f22eEC52fa53E3Cedd2247",
                                 "0xBB3988D74a853ddC16f22eEC52fa53E3Cedd224"])
def test_invalid_addresses_are_rejected(bad):
    """R8. rjust never truncates or validates, so '0xabc' padded to a
    syntactically valid EMPTY address: every balance reads 0.0 from a
    perfectly successful call, and paper mode calls that CLEAN."""
    assert rec.valid_address(bad) is None


def test_valid_address_accepted():
    assert rec.valid_address(WALLET) == WALLET


def test_balances_rejects_a_bad_wallet_before_any_call():
    c = _StubChain(_ok(0))
    with pytest.raises(RuntimeError, match="invalid wallet"):
        c.balances("0xabc", ["1"])
    assert c.seen == []


# ───────── regressions for bugs the fix-verification round found ─────────────

def test_empty_ledger_with_inventory_in_state_is_drift_not_skip():
    """HIGH-1. `if not net` was tested BEFORE `elif drift_a`, so 900 phantom
    shares were computed as drift and then thrown away as SKIP (rc=4). A halt
    consumer keying on rc==2 never fired."""
    drift, _ = rec.check_state_vs_ledger(
        {"meta": {}, "m": {"y": 900.0, "n": 0.0, "spent": 400.0}}, {}, set())
    assert len(drift) == 1 and drift[0]["kind"] == "state_without_ledger"
    r = rec.Report()
    r.add("STATE vs LEDGER", rec.DRIFT, "drift wins over an empty ledger")
    assert r.rc() == 2


def test_corrupt_state_entry_is_loud_not_silently_skipped():
    """HIGH-2. A non-dict state entry was `continue`d with no accounting, so
    300 ledgered shares vanished from the check entirely."""
    drift, _ = rec.check_state_vs_ledger(
        {"meta": {}, "m": "CORRUPT"},
        {"m": {"y": 300.0, "n": 0.0, "spent": 120.0, "merged": 0.0}}, set())
    assert len(drift) == 1 and drift[0]["kind"] == "state_entry_not_a_dict"


def test_compared_counts_tokens_not_markets():
    """HIGH-3. `compared = len(tokens) - len(skipped)` mixed per-token with
    per-market rows and printed 'PASS: 5 token(s) match' after comparing
    zero."""
    state = {"meta": {}, "a": {}, "b": {}, "c": {"tok_y": "1", "tok_n": "2"}}
    short, surp, skipped, compared = rec.check_chain_vs_gross(
        state, {}, {"1": 0.0, "2": 0.0}, {})
    assert compared == 2 and short == [] and surp == []


def test_merged_drift_is_detected_on_live_only_markets():
    """LOW-2. `merged` was replayed and fuzz-tested but never compared, so
    engine merge drift was unchecked."""
    net = {"m": {"y": 0.0, "n": 0.0, "spent": 0.0, "merged": 50.0}}
    state = {"meta": {}, "m": {"y": 0.0, "n": 0.0, "spent": 0.0, "merged": 999.0}}
    drift, _ = rec.check_state_vs_ledger(state, net, set())
    assert len(drift) == 1 and drift[0]["delta"]["merged"] == 949.0


def test_merged_is_not_compared_on_paper_touched_markets():
    """Paper snapshots carry no `merged`, so comparing it there would be a
    guaranteed false alarm."""
    net = {"m": {"y": 0.0, "n": 0.0, "spent": 0.0, "merged": 0.0}}
    state = {"meta": {}, "m": {"y": 0.0, "n": 0.0, "spent": 0.0, "merged": 50.0}}
    drift, _ = rec.check_state_vs_ledger(state, net, set(), {"m"})
    assert drift == []


def test_settle_then_fill_same_second_is_ambiguous_without_relying_on_filenames(tmp_path):
    """LOW-1. Ambiguity detection worked only because 'fills-' sorts before
    'settlements-'. Both branches now check, so a rename or a gzip of the
    settlements ledger cannot silently kill it."""
    _write(tmp_path, "settlements-20260720.jsonl", [
        {"t": 5, "mkt": "m", "payout": 10.0}])
    _write(tmp_path, "fills-20260720.jsonl", [
        {"t": 5, "tid": "a", "mkt": "m", "leg": "yes", "px": 0.4, "sz": 100.0}])
    _, _, meta = _replay(tmp_path)
    assert "m" in meta["ambiguous"]


def test_data_api_truncation_is_reported_not_hidden(monkeypatch):
    """MEDIUM-4. Hitting the page cap returned silently, and every unread
    token then read as a chain/indexer disagreement."""
    pages = {"n": 0}

    def fake_urlopen(req, timeout=None):
        pages["n"] += 1
        rows = [{"asset": str(pages["n"] * 1000 + i), "size": 1.0}
                for i in range(rec.API_PAGE)]

        class R:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def read(self_):
                return json.dumps(rows).encode()
        return R()

    monkeypatch.setattr(rec.urllib.request, "urlopen", fake_urlopen)
    out, truncated = rec.data_api_positions(WALLET)
    assert truncated is True
    assert pages["n"] == rec.API_MAX_PAGES
    assert len(out) == rec.API_MAX_PAGES * rec.API_PAGE


def test_decimals_tripwire_scale_includes_the_baseline():
    """MEDIUM-3. With a baseline and no Maker fills, total_gross is 0, so the
    tripwire was disabled in exactly the state it was written for — a 1e12
    inflation would have surfaced as a friendly rc=3 'surplus'."""
    gross, baseline = {}, {"111": 100.0}
    scale = max(sum(g["yes"] + g["no"] for g in gross.values()),
                sum(float(v) for v in baseline.values()))
    assert scale == 100.0
    assert 100.0 * 1e12 > rec.ABSURD_MULTIPLE * scale


# ─────────────── end-to-end main() — where wiring bugs actually live ─────────

def _base_with_paper_history(tmp_path):
    """A base dir shaped like the real paper arm: paper snapshot fills, a
    settlement, and engine state carrying `merged` from a paper-era merge."""
    _write(tmp_path, "fills-20260720.jsonl", [
        {"t": 1, "mkt": "m1", "n": 2, "y": 100.0, "nn": 0.0, "spent": 40.0},
        {"t": 2, "mkt": "m2", "n": 1, "y": 0.0, "nn": 0.0, "spent": 0.0}])
    _write(tmp_path, "settlements-20260720.jsonl", [
        {"t": 8, "mkt": "m2", "payout": 0.0}])
    state = {"meta": {},
             "m1": {"y": 100.0, "n": 0.0, "spent": 40.0, "merged": 55.54,
                    "tok_y": "111", "tok_n": "222"},
             "m2": {"y": 0.0, "n": 0.0, "spent": 0.0, "merged": 103.25,
                    "settled": True, "tok_y": "333", "tok_n": "444"}}
    with open(tmp_path / "state.json", "w") as fh:
        json.dump(state, fh)
    return tmp_path


def test_main_does_not_false_alarm_on_paper_era_merged(tmp_path, capsys):
    """REGRESSION for a call-site wiring bug: `check_state_vs_ledger` grew a
    `paper_tainted` parameter with a safe default, and main() never passed it.
    Every unit test still passed while the live run reported two markets as
    DRIFT purely because paper snapshots carry no `merged`. Caught only by
    running the real thing — hence this end-to-end test."""
    base = _base_with_paper_history(tmp_path)
    rc = rec.main(["--base", str(base), "--no-chain"])
    out = capsys.readouterr().out
    assert "DRIFT" not in out, out
    assert rc == 4          # chain skipped -> cannot certify, but NOT drift


def test_main_still_reports_real_drift_on_a_paper_base(tmp_path, capsys):
    """The companion: suppressing `merged` must not suppress real inventory
    drift on the same markets."""
    base = _base_with_paper_history(tmp_path)
    state = json.load(open(base / "state.json"))
    state["m1"]["y"] = 9999.0
    json.dump(state, open(base / "state.json", "w"))
    rc = rec.main(["--base", str(base), "--no-chain"])
    assert "DRIFT" in capsys.readouterr().out
    assert rc == 2


# ── S8-F1: prune-row support (review 2026-09-01) ────────────────────────────
def _ev(t, kind, key, a=None, b=None, c=None):
    return (t, 0, 0, kind, key, a, b, c)


def test_replay_prune_absolves_settled_market():
    """fill -> settle -> prune with matching residual: the key leaves net,
    no drift is recorded, and the union check stays clean against a state
    that no longer holds the entry."""
    events = [_ev(100, "delta", "m1", "yes", 0.45, 2.0),
              _ev(200, "settle", "m1", 2.0),           # payout 2.0
              _ev(300, "prune", "m1", -1.1, "TY1", "TN1")]
    gross, net, meta = rec.replay_events(events)
    assert "m1" not in net
    assert meta["prune_drift"] == []
    assert meta["prune_tokens"] == {"TY1", "TN1"}
    drift, amb = rec.check_state_vs_ledger({"meta": {}}, net, set(), set())
    assert drift == []


def test_replay_prune_mismatch_is_drift():
    """A prune row whose archived residual disagrees with the replayed net
    is a real disagreement, same severity as any drift."""
    events = [_ev(100, "delta", "m1", "yes", 0.45, 2.0),
              _ev(200, "settle", "m1", 2.0),
              _ev(300, "prune", "m1", -9.9)]           # replay says -1.1
    _g, net, meta = rec.replay_events(events)
    assert len(meta["prune_drift"]) == 1
    assert meta["prune_drift"][0]["kind"] == "prune_mismatch"
    assert "m1" not in net                              # still dropped


def test_replay_prune_of_unknown_husk_is_clean_only_at_zero():
    """Husk prune rows (no ledger history) carry resid 0.0 => clean;
    a nonzero residual with no history is a mismatch."""
    _g, _n, meta = rec.replay_events([_ev(10, "prune", "hx", 0.0)])
    assert meta["prune_drift"] == []
    _g, _n, meta = rec.replay_events([_ev(10, "prune", "hy", -5.0)])
    assert len(meta["prune_drift"]) == 1


def test_prune_row_classifier():
    row = {"t": 1, "act": "prune", "mkt": "m", "resid_spent": -1.0}
    assert rec._is_prune_row(row)
    assert not rec._is_settle_row(row)      # no payout => never a settle
    assert not rec._is_prune_row({"t": 1, "act": "kill", "mkt": "m"})
