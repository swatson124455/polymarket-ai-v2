"""Tests for the snapshot hygiene auditor."""
import json

from esports_v2.scripts.verify_snapshot_hygiene import HARD, audit, render


def _row(**over):
    d = {"captured_at": "2026-07-12T10:00:00+00:00",
         "match_key": "gam esports||t1||2026-07-15",
         "home": "T1", "away": "GAM Esports",
         "starts": "2026-07-15T12:00:00Z", "league_name": "L",
         "odds_a": 1.5, "odds_b": 2.6, "event_type": "prematch",
         "condition_id": "0xc1", "yes_token_id": "t1", "yes_outcome": "T1",
         "market_price": 0.6}
    d.update(over)
    return json.dumps(d)


def test_clean_file_passes():
    r = audit([_row(), _row(captured_at="2026-07-12T11:00:00+00:00",
                          market_price=0.61)])
    assert not any(r[k] for k in HARD)
    assert r["distinct_matches"] == 1 and r["pm_priced_rows"] == 2
    assert "PASS" in render(r)


def test_hard_failures_detected():
    rows = [
        "{broken",                                        # json_bad
        _row(odds_a=0.5),                                 # bad_odds
        _row(market_price=1.7),                           # bad_price
        _row(yes_token_id=None),                          # pm_incoherent
        _row(yes_outcome="Unrelated Org"),                # orient_bad
        _row(match_key="wrong||key||2026-07-15"),         # key_mismatch
        _row(), _row(),                                   # dup_rows (identical)
        _row(condition_id="0xc1", yes_token_id="OTHER"),  # cid_conflict
    ]
    r = audit(rows)
    assert r["json_bad"] == 1 and r["bad_odds"] == 1 and r["bad_price"] == 1
    assert r["pm_incoherent"] == 1 and r["orient_bad"] >= 1
    assert r["key_mismatch"] == 1 and r["dup_rows"] >= 1
    assert r["cid_conflicts"] >= 1
    assert "FAIL" in render(r)


def test_pre_gapb_and_null_pm_are_notes_not_failures():
    old = _row()
    d = json.loads(old)
    for k in ("condition_id", "yes_token_id", "yes_outcome", "market_price"):
        d.pop(k)
    unmatched = json.loads(_row(captured_at="2026-07-12T12:00:00+00:00"))
    for k in ("condition_id", "yes_token_id", "yes_outcome", "market_price"):
        unmatched[k] = None
    r = audit([json.dumps(d), json.dumps(unmatched)])
    assert r["pre_gapb"] == 1 and r["pm_null_rows"] == 1
    assert not any(r[k] for k in HARD)


def test_start_time_drift_is_a_note_not_a_failure():
    # schedule delay: same teams, same day, shifted start (measured live
    # 2026-07-12: 235 such rows) -> NOTE, reducer handles via latest-seen starts
    r = audit([_row(), _row(captured_at="2026-07-12T11:00:00+00:00",
                            starts="2026-07-15T14:00:00Z")])
    assert r["starts_drift"] == 1 and r["match_conflicts"] == 0
    assert not any(r[k] for k in HARD)


def test_team_change_under_one_key_is_hard():
    r = audit([_row(), _row(captured_at="2026-07-12T11:00:00+00:00",
                            home="Different Org",
                            condition_id=None, yes_token_id=None,
                            yes_outcome=None, market_price=None)])
    # (key_mismatch also fires since the key no longer reproduces — both HARD)
    assert r["match_conflicts"] == 1


# ── GAP-C quote-field hygiene ───────────────────────────────────────────────


def _book_row(**over):
    d = {"captured_at": "2026-07-12T10:00:00+00:00",
         "match_key": "gam esports||t1||2026-07-15",
         "home": "T1", "away": "GAM Esports",
         "starts": "2026-07-15T12:00:00Z", "league_name": "L",
         "odds_a": 1.5, "odds_b": 2.6, "event_type": "prematch",
         "condition_id": "0xc1", "yes_token_id": "t1", "yes_outcome": "T1",
         "market_price": 0.6,
         "best_bid": 0.59, "best_ask": 0.61, "bid_size": 100, "ask_size": 40}
    d.update(over)
    return json.dumps(d)


def test_clean_book_row_passes_and_counts():
    r = audit([_book_row()])
    assert not any(r[k] for k in HARD)
    assert r["book_rows"] == 1 and r["mid_outside_touch"] == 0


def test_one_sided_book_is_legit():
    # thin side: no ask price AND no ask size -> not a pairing violation
    r = audit([_book_row(best_ask=None, ask_size=None)])
    assert not any(r[k] for k in HARD) and r["book_rows"] == 1


def test_crossed_book_is_hard():
    r = audit([_book_row(best_bid=0.62, best_ask=0.60)])
    assert r["crossed_book"] == 1 and any(r[k] for k in HARD)


def test_out_of_range_quote_is_hard():
    assert audit([_book_row(best_bid=1.4)])["bad_quote"] == 1
    assert audit([_book_row(best_ask=0.0)])["bad_quote"] == 1
    assert audit([_book_row(ask_size=0)])["bad_quote"] == 1
    assert audit([_book_row(bid_size=-5)])["bad_quote"] == 1


def test_price_size_pairing_corruption_is_hard():
    # price present but its size missing -> partial corruption
    r = audit([_book_row(bid_size=None)])
    assert r["quote_pairing"] == 1 and any(r[k] for k in HARD)
    r2 = audit([_book_row(best_ask=None)])   # size present, price missing
    assert r2["quote_pairing"] == 1


def test_mid_outside_touch_is_a_note_not_hard():
    r = audit([_book_row(market_price=0.65)])   # mid 0.65 > ask 0.61
    assert r["mid_outside_touch"] == 1
    assert not any(r[k] for k in HARD)          # skew is a NOTE, still PASS
