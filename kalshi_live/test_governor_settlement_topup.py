"""THE GOVERNOR SEES LOSSES THAT ONLY BECOME REAL AT SETTLEMENT (defect 5b, 2026-08-03).

get_realized_by_market is a /portfolio/positions read, and that endpoint drops a market the
moment it settles (measured 2026-08-03T01:35:04Z: 0 of 127 settled tickers present). So a
position held into expiry that expired worthless produced a loss NO governor feed could see.
The carry (B4a) preserves the last live value but it is FROZEN; this top-up is what moves it.

VENUE SHAPES, measured 2026-08-03T02:30:57Z over the complete history (n=127):
  * `revenue` is CENTS (matches the value/counts payout model 127/127 divided by 100)
  * `settled_time` is an ISO-8601 STRING, 127/127 — not an epoch, though min_ts takes epoch
  * min_ts IS honoured (66 rows returned against 127 total at the median settle time)
  * ⚠ yes/no_total_cost_dollars are GROSS LIFETIME costs on BOTH legs and are NOT a P&L basis
    (KXTRUMPENDORSEMENTS-26AUG01-A5: $141.15 of "cost" on 125.00 yes vs 124.89 no — almost
    entirely paired). The basis is the venue's market_exposure_dollars for the NET position,
    snapshotted while the market was alive.

OPERATOR RULING 2026-08-02: a settlement-attributed loss MAY mint a permanent ban, under the
EXISTING UTC calendar-day latch. Hence the per-day clearing pinned below.
"""
import json
import os

from test_live_hardening import MockClient, _cfg, _run, q


def _pos(t="T1", pos="0.50", realized="0.00", expo="0.25"):
    return {"ticker": t, "position_fp": pos, "market_exposure_dollars": expo,
            "realized_pnl_dollars": realized}


def _fp1(monkeypatch):
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])


def _arm(monkeypatch):
    _cfg(monkeypatch)
    _fp1(monkeypatch)
    monkeypatch.setattr(q, "MKT_DAY_LOSS_EXITONLY_USD", 3.0)
    monkeypatch.setattr(q, "MKT_OUT_LOSS_USD", 5.0)


def _state(tmp_path):
    p = os.path.join(str(tmp_path), "quoter_state.json")
    return json.load(open(p)) if os.path.exists(p) else {}


def _settle(ticker="T2", revenue_cents=0, when="2099-01-01T00:00:00.000000Z",
            yes_ct="50.00", no_ct="0.00"):
    """A venue-shaped settlement row: revenue in CENTS, settled_time an ISO string.

    The counts default to the FULL 50 ct these fixtures hold, i.e. a position that rode into
    expiry intact. |yes - no| is what actually settled, and the top-up prorates the exposure
    basis onto it (blind-review fix 2026-08-03) — so a fixture whose counts disagree with the
    held size is asserting a PARTIAL UNWIND whether it means to or not. The original default
    of 40 ct against a 50 ct position did exactly that."""
    return {"ticker": ticker, "revenue": revenue_cents, "settled_time": when,
            "market_result": "no", "yes_count_fp": yes_ct, "no_count_fp": no_ct,
            "yes_total_cost_dollars": "40.00", "no_total_cost_dollars": "0.00", "value": 0}


def _hold_then_settle(monkeypatch, tmp_path, expo="40.00", revenue_cents=0, pos="50"):
    """T2 is held with a known exposure, then departs and settles."""
    _arm(monkeypatch)
    # cycle 1: T2 alive and flat-P&L, so its exposure is snapshotted
    _run(monkeypatch, MockClient(mode="live", positions=[_pos("T1"), _pos("T2", pos=pos, expo=expo)],
                                 traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"},
                                         {"ticker": "T2", "realized_pnl_dollars": "0.00"}]),
         str(tmp_path))
    # cycle 2: T2 has settled -> gone from the positions feed, present in settlements
    return _run(monkeypatch,
                MockClient(mode="live", positions=[_pos("T1")],
                           traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"}],
                           settlements=[_settle("T2", revenue_cents=revenue_cents)]),
                str(tmp_path))


def test_exposure_is_snapshotted_while_the_market_is_alive(monkeypatch, tmp_path):
    _arm(monkeypatch)
    _run(monkeypatch, MockClient(mode="live",
                                 positions=[_pos("T1"), _pos("T2", pos="50", expo="40.00")],
                                 traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"},
                                         {"ticker": "T2", "realized_pnl_dollars": "0.00"}]),
         str(tmp_path))
    expo = _state(tmp_path).get("mkt_exposure") or {}
    assert expo.get("T2") == 40.0, f"market_exposure_dollars is the TOTAL position cost: {expo}"


def test_worthless_expiry_mints_the_permanent_ban(monkeypatch, tmp_path):
    # THE MONEY CASE. A $40.00 position settles worthless (revenue 0) ->
    # a -$40.00 delta the old governor could never see. Far past the $5 rung.
    row = _hold_then_settle(monkeypatch, tmp_path, expo="40.00", revenue_cents=0)
    assert row.get("settle_topups") == 1
    assert row.get("settle_topup_usd") == -40.0
    assert _state(tmp_path).get("mkt_out") == ["T2"], \
        "a loss realized only at settlement must reach the permanent rung"


def test_winning_expiry_is_not_a_loss(monkeypatch, tmp_path):
    # A $40.00 position paying 5000c = $50.00 -> +$10.00. No ban.
    row = _hold_then_settle(monkeypatch, tmp_path, expo="40.00", revenue_cents=5000)
    assert row.get("settle_topups") == 1
    assert _state(tmp_path).get("mkt_out") == [], "a PROFITABLE settlement must not ban"
    assert (_state(tmp_path).get("mkt_settle_pnl") or {}).get("T2") == 10.0


def test_flat_before_settlement_produces_no_delta(monkeypatch, tmp_path):
    # Exited before expiry: exposure 0, revenue 0 -> delta 0. The realized loss was already
    # in the feed and must NOT be double-counted (the KXTEMPAUSH shape).
    _arm(monkeypatch)
    _run(monkeypatch, MockClient(mode="live", positions=[_pos("T1")],
                                 traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"},
                                         {"ticker": "T2", "realized_pnl_dollars": "-9.00"}]),
         str(tmp_path))
    # A flat market settles NET FLAT: |yes - no| == 0, so nothing survived to settlement.
    row = _run(monkeypatch,
               MockClient(mode="live", positions=[_pos("T1")],
                          traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"}],
                          settlements=[_settle("T2", revenue_cents=0,
                                               yes_ct="9.00", no_ct="9.00")]),
               str(tmp_path))
    assert (_state(tmp_path).get("mkt_settle_pnl") or {}).get("T2") == 0.0, \
        "a market that went flat before settling has zero exposure and zero delta"
    assert row.get("settle_topup_usd") == 0.0


def test_the_topup_is_idempotent_across_cycles(monkeypatch, tmp_path):
    # The watermark normally prevents a row being seen twice.
    _hold_then_settle(monkeypatch, tmp_path, expo="40.00", revenue_cents=0)
    first = dict(_state(tmp_path).get("mkt_settle_pnl") or {})
    for _ in range(3):
        _run(monkeypatch,
             MockClient(mode="live", positions=[_pos("T1")],
                        traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"}],
                        settlements=[_settle("T2", revenue_cents=0)]),
             str(tmp_path))
    assert (_state(tmp_path).get("mkt_settle_pnl") or {}) == first, \
        "a one-off settlement must never compound into an unbounded loss"


def test_reprocessing_a_row_is_idempotent_even_without_the_watermark(monkeypatch, tmp_path):
    """DEFENCE IN DEPTH (mutation-found 2026-08-03). The test above passes even if the delta
    ACCUMULATES, because the watermark stops the row being seen a second time — so it pins the
    cursor, not the arithmetic. Here the watermark is rewound to force genuine re-processing:
    the write must be an ASSIGN, so the value is identical no matter how often it is seen.
    Two independent guards, each pinned separately."""
    _hold_then_settle(monkeypatch, tmp_path, expo="40.00", revenue_cents=0)
    assert (_state(tmp_path).get("mkt_settle_pnl") or {}).get("T2") == -40.0
    for _ in range(3):
        st = _state(tmp_path)
        st["settle_watermark"] = 0                     # force the row to be re-read
        with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
            json.dump(st, fh)
        _run(monkeypatch,
             MockClient(mode="live", positions=[_pos("T1")],
                        traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"}],
                        settlements=[_settle("T2", revenue_cents=0)]),
             str(tmp_path))
        assert (_state(tmp_path).get("mkt_settle_pnl") or {}).get("T2") == -40.0, \
            "the delta must be ASSIGNED, never accumulated"


def test_the_carry_is_persisted_WITHOUT_the_topup(monkeypatch, tmp_path):
    # The delta is applied to the EVALUATION copy only. Folding it into the persisted carry
    # would re-add it every cycle.
    _hold_then_settle(monkeypatch, tmp_path, expo="40.00", revenue_cents=0)
    carry = _state(tmp_path).get("mkt_realized_carry") or {}
    assert carry.get("T2") == 0.0, \
        f"the carry holds the PRE-settlement realized value, not the delta: {carry}"


def test_a_position_flattened_during_a_halt_never_mints_a_ban(monkeypatch, tmp_path):
    """BLIND-REVIEW FIX 2026-08-03 — the wrong-permanent-ban case.

    mkt_exposure is refreshed ONLY inside the governor block, and the STOP branch returns at
    :2998 before load_state() at :3002. So while the bot is halted, _flatten_all actively sells
    inventory (maker offsets then taker escalation) while the exposure basis stays FROZEN at
    its last pre-halt value. Settling on that stale basis omits the sale proceeds entirely and
    is always biased toward a phantom LOSS: a fully flattened ticker computes
    delta = -(full pre-halt exposure) and mints a PERMANENT ban — two files, plus every series
    sibling dragged to probe size — on a market whose true day P&L was about zero.

    The settlement row carries what actually settled. Here the position was sold to flat during
    the halt, so the settled counts net to ZERO and the delta must be 0.
    Venue corroboration: 23 of 70 rows in the frozen AUD_setts.json sample settled NET FLAT and
    still emit a settlement row with revenue 0."""
    _arm(monkeypatch)
    # alive with a $40.00 position -> exposure and count snapshotted
    _run(monkeypatch, MockClient(mode="live",
                                 positions=[_pos("T1"), _pos("T2", pos="50", expo="40.00")],
                                 traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"},
                                         {"ticker": "T2", "realized_pnl_dollars": "0.00"}]),
         str(tmp_path))
    # ...halt, flatten sells all 50 ct (no governor cycle runs), then it settles NET FLAT
    flat_settle = dict(_settle("T2", revenue_cents=0))
    flat_settle["yes_count_fp"], flat_settle["no_count_fp"] = "50.00", "50.00"
    _run(monkeypatch,
         MockClient(mode="live", positions=[_pos("T1")],
                    traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"}],
                    settlements=[flat_settle]),
         str(tmp_path))
    assert (_state(tmp_path).get("mkt_settle_pnl") or {}).get("T2") == 0.0, \
        "nothing survived to settlement, so the delta must be zero — not the stale $40"
    assert _state(tmp_path).get("mkt_out") == [], \
        "a position flattened during a halt must NEVER mint a permanent ban"


def test_partial_unwind_prorates_the_settlement_basis(monkeypatch, tmp_path):
    # Half the position was sold before expiry: only the residual can lose. $40.00 over 50 ct
    # with 25 ct settling worthless -> -$20.00, not -$40.00.
    _arm(monkeypatch)
    _run(monkeypatch, MockClient(mode="live",
                                 positions=[_pos("T1"), _pos("T2", pos="50", expo="40.00")],
                                 traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"},
                                         {"ticker": "T2", "realized_pnl_dollars": "0.00"}]),
         str(tmp_path))
    part = dict(_settle("T2", revenue_cents=0))
    part["yes_count_fp"], part["no_count_fp"] = "50.00", "25.00"    # 25 ct net settled
    _run(monkeypatch,
         MockClient(mode="live", positions=[_pos("T1")],
                    traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"}],
                    settlements=[part]),
         str(tmp_path))
    assert (_state(tmp_path).get("mkt_settle_pnl") or {}).get("T2") == -20.0, \
        "the basis must prorate to the contracts that actually settled"


def test_full_ride_into_expiry_still_bans(monkeypatch, tmp_path):
    # The operator-named case must survive the fix: nothing was sold, the whole position rode
    # into expiry and expired worthless -> the full loss, and the permanent ban.
    _arm(monkeypatch)
    _run(monkeypatch, MockClient(mode="live",
                                 positions=[_pos("T1"), _pos("T2", pos="50", expo="40.00")],
                                 traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"},
                                         {"ticker": "T2", "realized_pnl_dollars": "0.00"}]),
         str(tmp_path))
    full = dict(_settle("T2", revenue_cents=0))
    full["yes_count_fp"], full["no_count_fp"] = "50.00", "0.00"     # all 50 ct settled
    _run(monkeypatch,
         MockClient(mode="live", positions=[_pos("T1")],
                    traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"}],
                    settlements=[full]),
         str(tmp_path))
    assert (_state(tmp_path).get("mkt_settle_pnl") or {}).get("T2") == -40.0
    assert _state(tmp_path).get("mkt_out") == ["T2"], \
        "a position that genuinely rode into expiry must still reach the permanent rung"


def test_settled_position_we_never_saw_held_is_skipped(monkeypatch, tmp_path):
    # net > 0 but no count basis: no way to prorate, so do not ban on it.
    _arm(monkeypatch)
    _run(monkeypatch, MockClient(mode="live", positions=[_pos("T1")],
                                 traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"},
                                         {"ticker": "T2", "realized_pnl_dollars": "-1.00"}]),
         str(tmp_path))
    row = dict(_settle("T2", revenue_cents=0))
    row["yes_count_fp"], row["no_count_fp"] = "50.00", "0.00"
    _run(monkeypatch,
         MockClient(mode="live", positions=[_pos("T1")],
                    traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"}],
                    settlements=[row]),
         str(tmp_path))
    st = _state(tmp_path)
    assert "T2" not in (st.get("mkt_settle_pnl") or {})
    assert "T2" not in (st.get("mkt_out") or [])


def test_unknown_exposure_never_fabricates_a_ban(monkeypatch, tmp_path):
    # A settlement for a ticker we never snapshotted: skip, count, do not ban on a basis we
    # do not have.
    _arm(monkeypatch)
    _run(monkeypatch, MockClient(mode="live", positions=[_pos("T1")],
                                 traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"}]),
         str(tmp_path))
    _run(monkeypatch,
         MockClient(mode="live", positions=[_pos("T1")],
                    traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"}],
                    settlements=[_settle("NEVER-SEEN", revenue_cents=0)]),
         str(tmp_path))
    st = _state(tmp_path)
    assert "NEVER-SEEN" not in (st.get("mkt_out") or [])
    assert "NEVER-SEEN" not in (st.get("mkt_settle_pnl") or {})


def test_one_unknown_ticker_does_not_poison_the_whole_batch(monkeypatch, tmp_path):
    """Mutation-found 2026-08-03: the previous pin passes even if the unknown-exposure guard
    is removed, because the missing basis then raises and the OUTER except swallows it — the
    ban is avoided by accident. The real requirement is that an unknown ticker is SKIPPED so
    the rest of the batch still processes. Here a known settlement rides alongside an unknown
    one and must still be applied."""
    _arm(monkeypatch)
    _run(monkeypatch, MockClient(mode="live",
                                 positions=[_pos("T1"), _pos("T2", pos="50", expo="40.00")],
                                 traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"},
                                         {"ticker": "T2", "realized_pnl_dollars": "0.00"}]),
         str(tmp_path))
    row = _run(monkeypatch,
               MockClient(mode="live", positions=[_pos("T1")],
                          traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"}],
                          settlements=[_settle("NEVER-SEEN", revenue_cents=0),
                                       _settle("T2", revenue_cents=0)]),
               str(tmp_path))
    assert row.get("settle_feed_fail") is None, \
        "an unknown ticker must be skipped, not raise into the feed handler"
    assert (_state(tmp_path).get("mkt_settle_pnl") or {}).get("T2") == -40.0, \
        "the known settlement in the same batch must still be applied"
    assert "NEVER-SEEN" not in (_state(tmp_path).get("mkt_settle_pnl") or {})


def test_settlements_outage_does_not_halt_and_does_not_advance_the_watermark(monkeypatch, tmp_path):
    # Operator decision 2026-08-02: this feed is ADDITIVE protection and must not join
    # gov_fail_streak. Self-healing: the watermark stays put so the next good read catches up.
    _arm(monkeypatch)
    _run(monkeypatch, MockClient(mode="live", positions=[_pos("T1")],
                                 traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"}]),
         str(tmp_path))
    wm_before = _state(tmp_path).get("settle_watermark")
    row = _run(monkeypatch,
               MockClient(mode="live", positions=[_pos("T1")],
                          traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"}],
                          get_settlements_raises=True),
               str(tmp_path))
    assert row.get("settle_feed_fail") == 1
    assert row.get("governor_fail_reduce_only") is None, \
        "a settlements outage must not drive the book reduce-only"
    assert _state(tmp_path).get("settle_watermark") == wm_before


def test_settlement_deltas_clear_at_the_day_roll(monkeypatch, tmp_path):
    # The ladder latches on the UTC calendar day, so yesterday's settlement must not count
    # toward today's rungs. The WATERMARK is a feed cursor and deliberately does NOT rewind.
    _hold_then_settle(monkeypatch, tmp_path, expo="40.00", revenue_cents=0)
    st = _state(tmp_path)
    assert (st.get("mkt_settle_pnl") or {}).get("T2") == -40.0
    wm = st.get("settle_watermark")
    st["mkt_realized_day"] = "2020-01-01"
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
        json.dump(st, fh)
    _run(monkeypatch, MockClient(mode="live", positions=[_pos("T1")],
                                 traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"}]),
         str(tmp_path))
    st2 = _state(tmp_path)
    assert (st2.get("mkt_settle_pnl") or {}) == {}, "settlement deltas are per-day"
    assert st2.get("settle_watermark") == wm, "the feed cursor must NOT rewind"
