"""Pins for SCORE-BASED RANKING (KALSHI_SCORE_RANK) — rank on measured capture, not pool alone.

THE POINT, stated precisely because it is easy to state wrongly: the POOL MATTERS. It is a linear
multiplier in the reward (reward = share x pool) and is never dropped. It is a poor RANK KEY only
because it is one of two terms and the one that varies less — measured over 30 series / 165
book-side depth readings (venue_scan.json 2026-07-25), pool spans 6x while rival qualifying depth,
which sets `share`, spans 71,330x. The key used is therefore the PRODUCT, at full pool weight.

  T1 POOL IS NOT DISCARDED  — with equal share, the bigger pool still wins. Non-negotiable.
  T2 CAPTURE BEATS POOL     — a small-pool empty book outranks a big-pool crowded one.
  T3 COLD CACHE IS LEGACY   — no scores => byte-for-byte the pool ordering.
  T4 EXPLORATION            — unscored markets are guaranteed slots, else the venue is never swept.
  T5 DECAY                  — a stale score blends back to the pool prior; it cannot pin the bot.
  T6 SWING PENALTY          — a market whose reference price whips around is discounted.
  T7 FAILS OPEN             — corrupt/garbage cache never raises, never blocks.
  T8 SHIPS OFF              — installing it changes nothing until switched on.
"""
import json

import kalshi_market_scores as ks
from test_live_hardening import q


def _row(t, pool):
    return {"ticker": t, "usd_day": pool}


def test_pool_is_not_discarded_equal_share_bigger_pool_wins():
    """reward = share x pool. With identical measured capture-per-pool, the bigger pool MUST rank
    higher. A ranker that ignored pool would tie these — that would be wrong, not conservative."""
    m = {}
    now = 1000.0
    # same share (10%), different pools -> capture scales WITH the pool
    ks.update(m, "BIG", 0.10 * 10000, 0.50, now=now)
    ks.update(m, "SMALL", 0.10 * 1000, 0.50, now=now)
    out = ks.rank(m, [_row("SMALL", 1000), _row("BIG", 10000)], now=now, explore=0)
    assert [r["ticker"] for r in out] == ["BIG", "SMALL"]


def test_capture_beats_pool_when_the_big_pool_is_crowded():
    """The measured case: KXFUNDRAISING $10,470/day models to $5.65/day (crowded);
    KXVOGUECOVER $1,800/day models to $42.03/day (nearly empty)."""
    m = {}
    now = 1000.0
    ks.update(m, "KXFUNDRAISING-X", 5.652, 0.50, now=now)
    ks.update(m, "KXVOGUECOVER-X", 42.027, 0.50, now=now)
    out = ks.rank(m, [_row("KXFUNDRAISING-X", 10470), _row("KXVOGUECOVER-X", 1800)],
                  now=now, explore=0)
    assert [r["ticker"] for r in out][0] == "KXVOGUECOVER-X"


def test_cold_cache_is_exactly_the_legacy_pool_order():
    rows = [_row("A", 100), _row("B", 900), _row("C", 500)]
    out = ks.rank({}, rows, now=1000.0, explore=0)
    assert [r["ticker"] for r in out] == ["B", "C", "A"]


def test_exploration_guarantees_unscored_markets_get_sampled():
    """Without this the bot converges on whatever it read first and never discovers anything
    better — the read budget only covers ~200 of 2,271 active programs per cycle."""
    m = {}
    ks.update(m, "KNOWN", 999.0, 0.50, now=1000.0)      # a huge measured winner
    rows = [_row("KNOWN", 100), _row("NEW1", 1), _row("NEW2", 1)]
    out = ks.rank(m, rows, now=1000.0, explore=2)
    assert [r["ticker"] for r in out[:2]] == ["NEW1", "NEW2"], "unscored get the reserved slots"
    assert out[2]["ticker"] == "KNOWN"
    assert len(out) == 3 and len({id(r) for r in out}) == 3, "no market duplicated or dropped"


def test_a_market_judged_once_is_not_retired_forever():
    """THE RE-LOOK PIN. A market scored badly must become eligible for sampling again once its
    score is stale — books move, and a market that was crowded an hour ago may be empty now.
    Without this, `score()` reports it "scored" forever, it never wins an exploration slot, and the
    only way back is decay toward its pool prior — so a cheap market that later empties out would
    be invisible. That is a permanent loss of a real opportunity."""
    m = {}
    ks.update(m, "WASBAD", 0.01, 0.50, now=1000.0)          # looked terrible when we saw it
    ks.update(m, "GOOD", 500.0, 0.50, now=1000.0)
    rows = [_row("WASBAD", 50), _row("GOOD", 50)]

    # immediately after: WASBAD is fresh, so it is NOT re-explored (we just looked)
    fresh = ks.rank(m, rows, now=1000.0 + 60, explore=1)
    assert fresh[0]["ticker"] == "GOOD", "no point re-reading a book we just read"

    # once WASBAD is stale — and GOOD is NOT (re-observed since) — WASBAD gets a slot back.
    # Both must not be stale together or the comparison proves nothing.
    t_late = 1000.0 + ks.STALE_S + 1
    ks.update(m, "GOOD", 500.0, 0.50, now=t_late)           # GOOD seen again, so it is fresh
    assert ks.score(m, "WASBAD", 50, now=t_late)[1] == "stale"
    assert ks.score(m, "GOOD", 50, now=t_late)[1] == "scored"
    aged = ks.rank(m, rows, now=t_late, explore=1)
    assert aged[0]["ticker"] == "WASBAD", "a stale market must be re-looked, not retired"
    assert len(aged) == 2 and {r["ticker"] for r in aged} == {"WASBAD", "GOOD"}


def test_never_seen_markets_are_explored_before_merely_stale_ones():
    """We know nothing about an unseen market and something about a stale one, so unseen goes
    first when slots are scarce."""
    m = {}
    ks.update(m, "STALE", 1.0, 0.50, now=1000.0)
    rows = [_row("STALE", 50), _row("NEVERSEEN", 50)]
    out = ks.rank(m, rows, now=1000.0 + ks.STALE_S + 1, explore=1)
    assert out[0]["ticker"] == "NEVERSEEN"


def test_stale_scores_decay_back_toward_the_pool_prior():
    m = {}
    ks.update(m, "OLD", 500.0, 0.50, now=1000.0)
    fresh, _ = ks.score(m, "OLD", 10.0, now=1000.0)
    aged, _ = ks.score(m, "OLD", 10.0, now=1000.0 + 10 * ks.HALF_LIFE_S)
    assert fresh > aged, "an ageing score must lose weight"
    assert abs(aged - 10.0) < 1.0, "and blend back to the pool prior, not to zero"


def test_swing_penalty_discounts_a_whipping_reference_price():
    """A market whose reference price moves between cycles fills us adversely — that is how a maker
    hands the rewards back. Two markets, identical capture, one stable and one moving 5c a cycle."""
    m = {}
    for now, ref in ((1000.0, 0.50), (1060.0, 0.50), (1120.0, 0.50)):
        ks.update(m, "STABLE", 20.0, ref, now=now)
    for now, ref in ((1000.0, 0.50), (1060.0, 0.55), (1120.0, 0.60)):
        ks.update(m, "SWINGY", 20.0, ref, now=now)
    s_stable, _ = ks.score(m, "STABLE", 100.0, now=1120.0)
    s_swingy, _ = ks.score(m, "SWINGY", 100.0, now=1120.0)
    assert s_stable > s_swingy
    assert m["SWINGY"]["ref_move"] > m["STABLE"]["ref_move"] == 0.0


def test_cache_fails_open_on_garbage(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{not json")
    assert ks.load(str(p)) == {}
    p.write_text(json.dumps({"schema": 999, "markets": {"X": {"capture": 5}}}))
    assert ks.load(str(p)) == {}, "wrong schema must fail open, not be trusted"
    assert ks.load(str(tmp_path / "nope.json")) == {}


def test_roundtrip_save_load(tmp_path):
    p = str(tmp_path / "s.json")
    m = {}
    ks.update(m, "T", 12.5, 0.42, now=1000.0)
    ks.save(p, m)
    got = ks.load(p)
    assert got["T"]["capture"] == 12.5 and got["T"]["ref"] == 0.42


def test_ships_off_and_defaults_are_sane():
    assert q.SCORE_RANK == 0, "installing this must change nothing until switched on"
    assert q.SCORE_EXPLORE > 0, "zero exploration would freeze the bot on its first sample"


def test_flag_off_emits_no_score_keys(monkeypatch, tmp_path):
    from test_live_hardening import MockClient, _run, _cfg
    _cfg(monkeypatch, join=100, mktcap=250, totcap=100000)
    monkeypatch.setattr(q, "SCORE_RANK", 0)
    monkeypatch.setattr(q, "select_footprint", lambda p, n: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00+00:00"}])
    row = _run(monkeypatch, MockClient(mode="live"), str(tmp_path))
    assert "scored_markets" not in row


# --------------------------------------------------------------------------------------------
# D1 — staleness sets the LABEL, not the VALUE (2026-08-04)
# --------------------------------------------------------------------------------------------
def test_d1_stale_keeps_its_measurement_instead_of_collapsing_to_the_pool_prior():
    """A market measured as hopelessly crowded must NOT be rehabilitated by mere aging.

    Before D1, `stale` returned pool * unknown_bonus — byte-identical to NEVER-MEASURED. With
    STALE_S=1800 against HALF_LIFE_S=3600 the decay at the cliff is still 0.707, so the value
    jumped DISCONTINUOUSLY upward at 1800s: a crowded market (capture 1) on a fat pool (1000)
    leapt from ~= its measurement to the full pool prior.
    """
    m = {}
    ks.update(m, "CROWDED", 1.0, 0.50, now=0.0)          # measured: nearly worthless to us
    just_fresh, k1 = ks.score(m, "CROWDED", 1000.0, now=ks.STALE_S - 1)
    just_stale, k2 = ks.score(m, "CROWDED", 1000.0, now=ks.STALE_S + 1)
    assert (k1, k2) == ("scored", "stale"), "the LABEL must still flip at the cliff"
    assert just_stale < 1000.0, "a measured-bad market must not score the full pool prior"
    assert abs(just_stale - just_fresh) < 1.0, "and the value must be CONTINUOUS across it"

    never = ks.score({}, "CROWDED", 1000.0, now=ks.STALE_S + 1)
    assert never[1] == "unknown" and never[0] == 1000.0
    assert just_stale < never[0], "stale evidence must rank BELOW a pure unknown here"


def test_d1_swing_penalty_survives_going_stale():
    """The swing discount is the adverse-fill signal. Before D1 the stale branch skipped
    `penalty` entirely, so 30 minutes erased what a violently swinging market had earned."""
    calm, swingy = {}, {}
    ks.update(calm, "CALM", 40.0, 0.50, now=0.0)
    ks.update(swingy, "SWINGY", 40.0, 0.50, now=0.0)
    ks.update(swingy, "SWINGY", 40.0, 0.90, now=1.0)     # big reference move -> ref_move > 0
    assert (swingy["SWINGY"].get("ref_move") or 0.0) > 0.0

    t = ks.STALE_S + 10
    calm_s, kc = ks.score(calm, "CALM", 100.0, now=t, swing_penalty=1.0)
    swing_s, kw = ks.score(swingy, "SWINGY", 100.0, now=t, swing_penalty=1.0)
    assert kc == kw == "stale"
    # ⚠ A BARE `swing_s < calm_s` IS SELF-FULFILLING and was: adversarial review mutated the
    # penalty out of the stale branch entirely and this pin still PASSED, because the fixture's
    # second update bumps SWINGY's ts by 1s and with capture(40) < pool(100) the fresher row
    # blends fractionally lower on decay alone — margin 0.008. The penalty is worth ~53 here, so
    # demand a margin decay cannot manufacture, and assert the mechanism directly.
    assert swing_s < calm_s * 0.5, "the swing discount must DOMINATE, not win by a decay sliver"
    # measured 2026-08-04 at this age: swingy/calm 0.3484, off/swingy 2.8701. Bounds set BELOW
    # the measured values so they cannot pass by a hair, and not tuned to them either.
    off, _ = ks.score(swingy, "SWINGY", 100.0, now=t, swing_penalty=0.0)
    assert off > swing_s * 2.0, "turning swing_penalty off must materially raise the same row"
    # AND at a FRESH age the penalty must bite hardest — this is what stops the 2026-08-04 fix
    # (relaxing the penalty toward 1.0 as the measurement decays) from being over-applied into
    # "the swing discount barely does anything". Measured ratio there: 0.0873.
    fresh_c, _ = ks.score(calm, "CALM", 100.0, now=60, swing_penalty=1.0)
    fresh_s, _ = ks.score(swingy, "SWINGY", 100.0, now=60, swing_penalty=1.0)
    assert fresh_s < fresh_c * 0.2, "a fresh swingy measurement must be heavily discounted"


def test_d1_does_not_change_which_rows_the_explore_quota_resamples():
    """The fix moves the VALUE only. rank() keys exploration on the LABEL, so sweep behaviour
    must be byte-identical — a stale row is still eligible for a sampling slot."""
    # ⚠ THIS PIN WAS VACUOUS: explore=2 with exactly 2 candidates returns everything whatever
    # the scores are — adversarial review made score() return a constant 0.0 and it still passed.
    # Use a SCARCE quota over MANY same-ts stale rows, which is the real shape (run_once stamps a
    # whole cycle with one `now`), so the pick is decided by the tie-break and not by the value.
    m = {}
    for t in ("AAA", "BBB", "CCC"):
        ks.update(m, t, 40.0, 0.50, now=0.0)
    m["BBB"]["capture"] = 999.0        # make the VALUE order differ from the ticker order
    rows = [{"ticker": t, "usd_day": 100.0} for t in ("AAA", "BBB", "CCC")]
    out = ks.rank(m, rows, now=ks.STALE_S + 10, explore=1)
    picked = [r["ticker"] for r in out if r.get("explore")]
    assert picked == ["AAA"], f"scarce explore slot must go by ts+ticker, not score; got {picked}"

    m["AAA"]["capture"] = 999.0        # flip which row scores best; the pick must NOT move
    out2 = ks.rank(m, rows, now=ks.STALE_S + 10, explore=1)
    assert [r["ticker"] for r in out2 if r.get("explore")] == ["AAA"],         "the explore pick must be independent of the score — that is what 'byte-unchanged' means"
