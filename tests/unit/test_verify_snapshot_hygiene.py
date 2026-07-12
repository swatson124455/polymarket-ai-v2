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


def test_match_conflict_detected():
    r = audit([_row(), _row(captured_at="2026-07-12T11:00:00+00:00",
                            starts="2026-07-15T14:00:00Z")])
    assert r["match_conflicts"] == 1
