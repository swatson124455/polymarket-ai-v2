"""Unit tests for scripts/chain_deep_dive.py — pure four-tier core + the
pre-registered verdict table (network paths run on the VPS; the module's
--self-test covers the same core). The reconstruction decoders are cross-
checked against the ALREADY-AUDITED semantics they must not drift from:
readjudicate._leg (V1 asset semantics) and copy_watcher.decode_fill_v2 (V2
layout). If those siblings change, these bindings break — on purpose.
Run: python3 -m pytest tests/unit/test_chain_deep_dive.py --override-ini "addopts=" """
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import chain_deep_dive as dd  # noqa: E402
import readjudicate_discrepant as rj  # noqa: E402
from mirror_v3.copy_watcher import (  # noqa: E402
    FILL_TOPIC_V2, SCALE, addr_topic, decode_fill_v2)

A = "0xabcd000000000000000000000000000000000001"


# ── Tier 1: reconstruction decoders vs the audited siblings ──────────────────
def test_v1_buy_agrees_with_audited_leg():
    """v1_reconstruct_fill's BUY price must equal readjudicate._leg's — the
    same asset semantics, one discovering direction, one matching it."""
    args = {"maker": A, "taker": "0x9", "makerAssetId": 0, "takerAssetId": 777,
            "makerAmountFilled": 60_000_000, "takerAmountFilled": 100_000_000}
    f = dd.v1_reconstruct_fill(args, A)
    leg = rj._leg(args, A, 777, True)
    assert f["side"] == "BUY" and f["token_id"] == "777"
    assert abs(f["price"] - leg[0] / leg[1]) < 1e-12


def test_v1_all_four_directions():
    def mk(maker, taker, m_id, t_id, m_amt, t_amt):
        return dd.v1_reconstruct_fill(
            {"maker": maker, "taker": taker, "makerAssetId": m_id,
             "takerAssetId": t_id, "makerAmountFilled": m_amt,
             "takerAmountFilled": t_amt}, A)
    # maker pays USDC -> BUY; maker pays token -> SELL
    assert mk(A, "0x9", 0, 777, 60_000_000, 100_000_000)["side"] == "BUY"
    assert mk(A, "0x9", 777, 0, 100_000_000, 80_000_000)["side"] == "SELL"
    # taker pays USDC -> BUY; taker pays token -> SELL
    assert mk("0x9", A, 777, 0, 100_000_000, 66_000_000)["side"] == "BUY"
    assert mk("0x9", A, 0, 777, 66_000_000, 100_000_000)["side"] == "SELL"
    # not a party -> None; token-for-token (no USDC leg) -> None
    assert mk("0x1", "0x2", 0, 777, 1, 1) is None
    assert mk(A, "0x9", 555, 777, 1, 1) is None


def _w32(n):
    return "%064x" % n


EXCH = "0x" + "5" * 40  # a full 40-hex exchange address (addr_topic round-trips)


def _v2_log(owner, token, usdc, tokens, cp="0x" + "0" * 39 + "2",
            exch=EXCH, tx="0xv2", block=9):
    return {"topics": ["0x" + FILL_TOPIC_V2.replace("0x", ""), "0x" + _w32(1),
                       addr_topic(owner), addr_topic(cp)],
            "data": "0x" + _w32(0) + _w32(token) + _w32(usdc) + _w32(tokens)
                    + _w32(0) + _w32(0) + _w32(0),
            "address": exch, "transactionHash": tx, "blockNumber": block}


def test_v2_reconstruction_matches_deployed_decoder_price():
    """v2_reconstruct_fill's (token, price, size) must match the deployed
    copy_watcher.decode_fill_v2 on the same log (direction stays pending)."""
    lg = _v2_log(A, 777, 90_000_000, 100_000_000)
    f = dd.v2_reconstruct_fill(lg, A)
    sig = decode_fill_v2(dict(lg), {A})
    assert f["side"] is None and f["era"] == "v2"
    assert f["token_id"] == sig["token_id"]
    assert abs(f["price"] - sig["whale_price"]) < 1e-12
    assert abs(f["usd"] - sig["whale_size_usd"]) < 1e-12


def test_v2_taker_has_no_named_counterparty():
    """When topics[3] is the exchange (taker aggregate), counterparty is None."""
    lg = _v2_log(A, 777, 90_000_000, 100_000_000, cp=EXCH, exch=EXCH)
    f = dd.v2_reconstruct_fill(lg, A)
    assert f["was_taker"] and f["counterparty"] is None
    lg2 = _v2_log(A, 777, 90_000_000, 100_000_000,
                  cp="0x" + "0" * 39 + "7", exch="0x5")
    f2 = dd.v2_reconstruct_fill(lg2, A)
    assert not f2["was_taker"] and f2["counterparty"].endswith("7")


def test_v2_wrong_owner_rejected():
    lg = _v2_log("0x" + "0" * 39 + "e", 777, 1, 1)
    assert dd.v2_reconstruct_fill(lg, A) is None


def test_first_buys_one_per_market_earliest_block():
    fills = [
        {"token_id": "777", "side": "BUY", "price": 0.6, "block": 9, "era": "v1"},
        {"token_id": "777", "side": "BUY", "price": 0.7, "block": 5, "era": "v1"},
        {"token_id": "888", "side": "SELL", "price": 0.4, "block": 3, "era": "v1"},
        {"token_id": "999", "side": None, "price": 0.5, "block": 2, "era": "v2"}]
    fbs = dd.reconstruct_first_buys(fills, {"777": "0xC1"})
    assert len(fbs) == 1
    assert fbs[0]["market_key"] == "0xC1" and fbs[0]["block"] == 5  # earliest BUY


# ── Tier 2: reconciliation, both directions ──────────────────────────────────
def test_api_to_chain_verified_mismatch_notfound():
    chain = [{"token_id": "777", "usd": 60, "tokens": 100, "tx": "0xa",
              "price": 0.6}]
    r = dd.reconcile_api_to_chain(
        [{"tokenId": "777", "side": "BUY", "price": 0.60, "size": 100},
         {"tokenId": "777", "side": "BUY", "price": 0.90, "size": 100},
         {"tokenId": "777", "side": "BUY", "price": 0.60, "size": 7},
         {"tokenId": "not-numeric", "side": "BUY", "price": 0.6, "size": 5}],
        chain, 0.02, 0.05)
    c = r["counts"]
    assert (c["verified"], c["mismatch"], c["not_found"]) == (1, 1, 2)
    # chain_price is the CHAIN truth (0.6); the API CLAIMED 0.9 — that gap is
    # the lie. The mismatch row records the chain price, not the claim.
    assert r["mismatches"][0]["chain_price"] == 0.6
    assert r["mismatches"][0]["api_price"] == 0.90


def test_chain_to_api_hidden_activity():
    r = dd.reconcile_chain_to_api(
        [{"token_id": "777", "price": 0.6, "tokens": 100, "tx": "0xa"},
         {"token_id": "555", "price": 0.3, "tokens": 40, "tx": "0xb"}],
        [{"tokenId": "777", "price": 0.6, "size": 100}], 0.02, 0.05)
    assert r["hidden"] == 1 and r["n_chain_buys"] == 2


# ── Tier 4: forensics ────────────────────────────────────────────────────────
def test_counterparty_concentration_and_maker_taker():
    fills = [{"era": "v1", "maker": True, "counterparty": "0xcp"},
             {"era": "v1", "maker": False, "counterparty": "0xcp"},
             {"era": "v2", "was_taker": True, "counterparty": None}]
    conc = dd.counterparty_concentration(fills)
    mt = dd.maker_taker_profile(fills)
    assert conc["named_fills"] == 2 and abs(conc["top_share"] - 1.0) < 1e-12
    assert (mt["maker"], mt["taker"]) == (1, 2)


def test_true_rate_and_interp():
    assert abs(dd.true_rate_per_day(300, 3) - 100) < 1e-9
    assert abs(dd.true_rate_per_day(0, 3)) < 1e-12
    it = dd.interp_ts(50, 0, 1000, 100, 3000)
    from datetime import timezone
    assert int(it.replace(tzinfo=timezone.utc).timestamp()) == 2000


# ── Tier 3: skill re-grade on chain data (reuses walkforward.hire_ok) ─────────
def test_chain_skill_grade_labels_and_hire_bar():
    class Cfg:
        min_markets_hire = 3
        min_span_days = 0
        p_hire = 0.10
        n_boot_roster = 200
    markets = {f"0xC{i}": {"resolution": "YES", "yes_token_id": str(i),
                           "no_token_id": "n" + str(i)} for i in range(6)}
    tok2cond = {str(i): f"0xC{i}" for i in range(6)}
    fbs = [{"token_id": str(i), "side": "BUY", "price": 0.30 + 0.03 * i,
            "block": 10 + i, "era": "v1"} for i in range(6)]
    sk = dd.chain_skill_grade(fbs, markets, tok2cond, 10, 1000, 15, 5_000_000,
                              Cfg(), 7)
    assert sk["n_labeled"] == 6 and sk["n_markets"] == 6 and sk["clears_bar"]


def test_chain_skill_identical_edges_are_no_evidence():
    """A record with zero edge spread is P=0 (degenerate) — no evidence,
    never a hire. Guards against a false ADMIT on constant-edge junk."""
    class Cfg:
        min_markets_hire = 3
        min_span_days = 0
        p_hire = 0.10
        n_boot_roster = 200
    markets = {f"0xC{i}": {"resolution": "YES", "yes_token_id": str(i),
                           "no_token_id": "n" + str(i)} for i in range(6)}
    tok2cond = {str(i): f"0xC{i}" for i in range(6)}
    fbs = [{"token_id": str(i), "side": "BUY", "price": 0.4, "block": 10 + i,
            "era": "v1"} for i in range(6)]
    sk = dd.chain_skill_grade(fbs, markets, tok2cond, 10, 1000, 15, 5_000_000,
                              Cfg(), 7)
    assert sk["n_labeled"] == 6 and not sk["clears_bar"]


# ── The pre-registered admission verdict table ───────────────────────────────
class _VC:
    max_rpc_err_frac = 0.05
    min_api_check = 10
    min_api_backing = 0.80
    fabrication_frac = 0.50
    wash_share = 0.50
    hft_max_rate = 200.0
    copier_lead_s = 30


_BASE = {"rpc_err_frac": 0.0, "canary_ok": True, "n_chain_fills": 500,
         "mismatch": 0, "api_buys_checked": 40, "api_backing": 0.95,
         "skill_gradeable": True, "skill_clears": True, "skill_markets": 40,
         "skill_span": 120, "skill_p": 0.99, "skill_labeled": 40,
         "wash_flag": False, "wash_share": 0.1, "wash_named": 100,
         "rate_flag": False, "true_rate": 5.0, "copier_flag": False,
         "copier_frac": 0.0}


def _v(**over):
    return dd.deep_dive_verdict({**_BASE, **over}, _VC())[0]


def test_verdict_admit_clean():
    assert _v() == "ADMIT"


def test_verdict_incomplete_sweep_is_insufficient_not_reject():
    # A high error rate or a failed canary is an EVIDENCE GAP — never an
    # accusation, never an admission (operator rule 2026-07-14).
    assert _v(rpc_err_frac=0.5) == "INSUFFICIENT-EVIDENCE"
    assert _v(canary_ok=False) == "INSUFFICIENT-EVIDENCE"
    assert _v(n_chain_fills=0) == "INSUFFICIENT-EVIDENCE"


def test_verdict_hard_rejects_only_on_complete_sweep():
    assert _v(mismatch=2) == "REJECT"                 # chain says a lie
    assert _v(api_backing=0.3) == "REJECT"            # fabrication (>50% unbacked)
    assert _v(skill_clears=False) == "REJECT"         # fails the hire bar
    assert _v(wash_flag=True) == "REJECT"             # wash concentration
    assert _v(rate_flag=True) == "REJECT"             # un-tailable HFT rate
    assert _v(copier_flag=True) == "REJECT"           # double-lag copier


def test_verdict_thin_backing_is_insufficient_not_reject():
    # Between full backing and fabrication is an evidence gap, not a lie.
    assert _v(api_backing=0.6) == "INSUFFICIENT-EVIDENCE"


def test_verdict_ungradeable_skill_is_insufficient():
    assert _v(skill_gradeable=False, skill_labeled=2) == "INSUFFICIENT-EVIDENCE"


def test_verdict_a_lie_beats_incompleteness_ordering():
    """Completeness gate runs FIRST: an incomplete sweep is INSUFFICIENT even
    if a mismatch was seen (can't trust a partial record to convict)."""
    assert _v(rpc_err_frac=0.9, mismatch=5) == "INSUFFICIENT-EVIDENCE"


def test_roster_from_readjudicate():
    assert dd.roster_from_readjudicate(
        {"vindicated": ["0xB", "0xA"]}) == ["0xa", "0xb"]
    assert dd.roster_from_readjudicate({}) == []
