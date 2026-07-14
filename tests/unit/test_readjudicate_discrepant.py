"""Unit tests for scripts/readjudicate_discrepant.py — pure matching core only
(network paths are exercised on the VPS; the self-test covers the same core).
Run: python3 -m pytest tests/unit/test_readjudicate_discrepant.py --override-ini "addopts=" """
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import audit_roster_chain as ac  # noqa: E402
import readjudicate_discrepant as rd  # noqa: E402

A = "0xAbCd000000000000000000000000000000000001"


def _ev(tx, m_amt, t_amt, maker=A, taker="0x9", m_id=0, t_id=777):
    return {"maker": maker, "taker": taker, "makerAssetId": m_id,
            "takerAssetId": t_id, "makerAmountFilled": m_amt,
            "takerAmountFilled": t_amt, "_tx": tx}


def test_window_blend_artifact_resolved():
    """The exact false-DISCREPANT class: two real same-token trades in one
    window. Old matcher blends to 0.70 and mismatches both API rows; the
    tx-exact matcher verifies each row against its own tx."""
    window = [_ev("0xt1", 60_000_000, 100_000_000),
              _ev("0xt2", 80_000_000, 100_000_000)]
    assert ac.match_fill(window, A, 777, "BUY", 0.60, 0.02)["status"] \
        == "price_mismatch"
    m1 = rd.match_fill_txexact(window, A, 777, "BUY", 0.60, 100.0, 0.02, 0.05)
    m2 = rd.match_fill_txexact(window, A, 777, "BUY", 0.80, 100.0, 0.02, 0.05)
    assert (m1["status"], m1["tx"]) == ("verified_txexact", "0xt1")
    assert (m2["status"], m2["tx"]) == ("verified_txexact", "0xt2")


def test_real_mismatch_survives():
    m = rd.match_fill_txexact([_ev("0xt1", 90_000_000, 100_000_000)],
                              A, 777, "BUY", 0.60, 100.0, 0.02, 0.05)
    assert m["status"] == "mismatch_txexact"
    assert abs(m["chain_price"] - 0.90) < 1e-9


def test_same_tx_split_aggregates_and_single_leg_matches():
    split = [_ev("0xt1", 30_000_000, 50_000_000),
             _ev("0xt1", 30_000_000, 50_000_000)]
    full = rd.match_fill_txexact(split, A, 777, "BUY", 0.60, 100.0, 0.02, 0.05)
    leg = rd.match_fill_txexact(split, A, 777, "BUY", 0.60, 50.0, 0.02, 0.05)
    assert full["status"] == "verified_txexact"
    assert abs(full["chain_tokens"] - 100.0) < 1e-9
    assert leg["status"] == "verified_txexact"
    assert abs(leg["chain_tokens"] - 50.0) < 1e-9


def test_no_size_match_is_not_found_never_blended():
    window = [_ev("0xt1", 60_000_000, 100_000_000),
              _ev("0xt2", 80_000_000, 100_000_000)]
    m = rd.match_fill_txexact(window, A, 777, "BUY", 0.60, 37.0, 0.02, 0.05)
    assert m["status"] == "not_found" and m["chain_price"] is None
    # zero/absent API size can never size-match
    m = rd.match_fill_txexact(window, A, 777, "BUY", 0.60, 0.0, 0.02, 0.05)
    assert m["status"] == "not_found"


def test_taker_and_direction_and_stranger_semantics():
    tk = {"maker": "0x9", "taker": A, "makerAssetId": 777, "takerAssetId": 0,
          "makerAmountFilled": 50_000_000, "takerAmountFilled": 33_000_000,
          "_tx": "0xt9"}
    assert rd.match_fill_txexact([tk], A, 777, "BUY", 0.66, 50.0, 0.02,
                                 0.05)["status"] == "verified_txexact"
    # SELL direction must not match a BUY-shaped event
    assert rd.match_fill_txexact([tk], A, 777, "SELL", 0.66, 50.0, 0.02,
                                 0.05)["status"] == "not_found"
    # stranger address / wrong token never match
    assert rd.match_fill_txexact([tk], "0xdead", 777, "BUY", 0.66, 50.0,
                                 0.02, 0.05)["status"] == "not_found"
    assert rd.match_fill_txexact([tk], A, 888, "BUY", 0.66, 50.0,
                                 0.02, 0.05)["status"] == "not_found"


def test_adjudicate_pre_registered_rule():
    assert rd.adjudicate({"verified_txexact": 20}, 20, 0.2) == "VINDICATED"
    assert rd.adjudicate({"verified_txexact": 19, "mismatch_txexact": 1},
                         20, 0.2) == "STILL_DISCREPANT"
    assert rd.adjudicate({"verified_txexact": 14, "not_found": 6},
                         20, 0.2) == "THIN"
    assert rd.adjudicate({"rpc_error": 15, "verified_txexact": 5},
                         20, 0.2) == "ERROR"
    assert rd.adjudicate({}, 0, 0.2) == "ERROR"


def test_discrepant_roster_extraction():
    audit = {"results": {"0xa": {"verdict": "DISCREPANT"},
                         "0xb": {"verdict": "CLEAN"},
                         "0xc": {"verdict": "THIN"}}}
    assert rd.discrepant_from_audit(audit) == ["0xa"]
    assert rd.discrepant_from_audit(audit, include_thin=True) == ["0xa", "0xc"]


def test_self_test_passes():
    assert rd._self_test() == 0


# ── dual-era V2 support (REGRESSION — V1-only matcher found 2026-07-14) ──────
# Post-migration fills live on the V2 exchanges under an unnamed event; a
# V1-only window search structurally not_found every one of them.
def test_v2_log_to_pseudo_event_decodes_buy_shaped():
    A = "0xabcd000000000000000000000000000000000001"
    tok, usdc, shares = 777, 60_000_000, 100_000_000
    data = "0x" + "".join(f"{w:064x}" for w in
                          [0, tok, usdc, shares, 0, 0, 0])
    lg = {"topics": [rd.FILL_TOPIC_V2, "0x" + "0" * 64, rd.addr_topic(A)],
          "data": data, "transactionHash": "0xv2tx1"}
    pe = rd.v2_log_to_pseudo_event(lg, A)
    assert pe is not None and pe["_v2"] is True and pe["_tx"] == "0xv2tx1"
    assert pe["makerAssetId"] == 0 and pe["takerAssetId"] == tok
    assert pe["makerAmountFilled"] == float(usdc)
    assert pe["takerAmountFilled"] == float(shares)
    # and the pseudo-event verifies the API row tx-exactly
    m = rd.match_fill_txexact([pe], A, tok, "BUY", 0.60, 100.0, 0.02, 0.05)
    assert m["status"] == "verified_txexact" and m["tx"] == "0xv2tx1"


def test_v2_log_rejections():
    A = "0xabcd000000000000000000000000000000000001"
    B = "0x9999000000000000000000000000000000000009"
    good_data = "0x" + "".join(f"{w:064x}" for w in
                               [0, 777, 60_000_000, 100_000_000, 0, 0, 0])
    base = {"topics": [rd.FILL_TOPIC_V2, "0x" + "0" * 64, rd.addr_topic(A)],
            "data": good_data, "transactionHash": "0xv2tx1"}
    assert rd.v2_log_to_pseudo_event(dict(base, topics=[
        "0x" + "ab" * 32, "0x" + "0" * 64, rd.addr_topic(A)]), A) is None
    assert rd.v2_log_to_pseudo_event(base, B) is None      # other owner
    assert rd.v2_log_to_pseudo_event(dict(base, data="0x" + "00" * 32),
                                     A) is None            # short data
    zero = "0x" + "".join(f"{w:064x}" for w in [0, 777, 0, 0, 0, 0, 0])
    assert rd.v2_log_to_pseudo_event(dict(base, data=zero), A) is None
