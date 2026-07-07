"""Deterministic Stage-1 composition test: score_trader -> select_and_size.

Pins the audit-corrected admission/sizing split:
  - admission = adverse-events + non-degenerate SE backstops, then BH on the
    test-half p-value (alpha spent once)
  - Jeffreys guards SIZING only (favorite-seller containment), never admission
No randomness — outcomes follow fixed patterns so the asserts are stable.
"""

from datetime import datetime, timedelta, timezone

from bots.mirror_scoring.config import ScoringConfig
from bots.mirror_scoring.estimand import Entry
from bots.mirror_scoring.q_score import score_trader, select_and_size

T0 = datetime(2026, 3, 1, tzinfo=timezone.utc)
CUTOFF = T0 + timedelta(days=100)


def build(name, n_events, win_pattern, price, start=T0, day_step=4):
    """win_pattern: list of 0/1 cycled to length n_events (deterministic).
    Events are spread day_step apart; resolution 3 days after entry, so the
    event-blocked split at CUTOFF gives a train half and a test half."""
    entries, resolved_at = [], {}
    for k in range(n_events):
        won = bool(win_pattern[k % len(win_pattern)])
        cond = f"{name}_c{k}"
        ts = start + timedelta(days=k * day_step)
        resolved_at[cond] = ts + timedelta(days=3)
        entries.append(Entry(
            trader=name, condition_id=cond, token_id="yes", side="BUY",
            price=price, timestamp=ts, size=10.0,
            resolution="YES" if won else "NO", token_won=won,
        ))
    return entries, resolved_at


def test_skilled_admitted_and_sized_favorite_contained_noise_rejected():
    cfg = ScoringConfig(MIN_EVENTS=12, MIN_ADVERSE_EVENTS=2, BH_Q=0.10)

    # Skilled: 60 events buying at 0.40, 3-of-5 wins => 60% win, edge +0.20,
    # plenty of adverse events, non-degenerate SE.
    sk_e, sk_r = build("skilled", 60, [1, 1, 1, 0, 0], 0.40)
    # Favorite-seller: 60 events at 0.95, wins 14-of-15 => ~93%, edge -0.02
    # (true tail-risk seller: positive-looking but Jeffreys-negative).
    fav_pat = [1] * 14 + [0]
    tr_e, tr_r = build("favorite", 60, fav_pat, 0.95)
    # Noise: 60 events at 0.50, 50% wins => zero edge.
    nz_e, nz_r = build("noise", 60, [1, 0], 0.50)

    resolved_at = {**sk_r, **tr_r, **nz_r}
    entries_by = {"skilled": sk_e, "favorite": tr_e, "noise": nz_e}

    scores = []
    for name, ents in entries_by.items():
        ts = score_trader(ents, cfg, CUTOFF, resolved_at)
        assert ts is not None, name
        scores.append(ts)
    scores = select_and_size(scores, entries_by, cfg)
    by = {t.trader: t for t in scores}

    # Skilled: real out-of-sample edge -> admitted AND sized.
    assert by["skilled"].admitted is True
    assert by["skilled"].kelly_weight > 0.0
    assert by["skilled"].n_adverse >= cfg.MIN_ADVERSE_EVENTS

    # Noise: no edge -> not admitted, not sized.
    assert by["noise"].admitted is False
    assert by["noise"].kelly_weight == 0.0

    # Favorite-seller: even if it sneaks into admission, Jeffreys must zero
    # its size (the money-losing failure mode is max-sizing tail risk).
    assert by["favorite"].edge_lb_jeffreys <= 0
    assert by["favorite"].kelly_weight == 0.0


def test_jeffreys_guards_sizing_not_admission():
    """A trader admitted by BH but failing Jeffreys is watch-only (size 0),
    not rejected — proves the two gates are decoupled."""
    cfg = ScoringConfig(MIN_EVENTS=12, MIN_ADVERSE_EVENTS=2, BH_Q=0.99)
    # Marginal favorite: strong enough win-rate to get a low p, but bought
    # so expensive that Jeffreys edge LB stays <= 0.
    fav_pat = [1] * 9 + [0]
    e, r = build("marginal", 40, fav_pat, 0.92)
    ts = score_trader(e, cfg, CUTOFF, r)
    scores = select_and_size([ts], {"marginal": e}, cfg)
    t = scores[0]
    if t.admitted:  # BH_Q=0.99 makes admission easy
        assert t.edge_lb_jeffreys <= 0
        assert t.kelly_weight == 0.0
        assert any("jeffreys" in reason for reason in t.watch_only_reasons)


def test_untested_placeholders_do_not_inflate_bh_denominator():
    """Regression for the 2026-07-06 admitted=0 bug: a swarm of UNTESTED
    traders (no test half -> p forced to 1.0) must NOT raise the BH bar for
    the genuinely tested ones. With the family = tested-only, one skilled
    trader is admitted regardless of how many placeholders sit alongside."""
    cfg = ScoringConfig(MIN_EVENTS=12, MIN_ADVERSE_EVENTS=2, BH_Q=0.10)

    # One genuinely skilled trader, fully spanning the cutoff (train + test).
    sk_e, sk_r = build("skilled", 60, [1, 1, 1, 0, 0], 0.40)

    # 500 "untested" traders: all their events resolve AFTER the cutoff, so
    # the train half is empty -> holdout_tested False -> p=1.0. They have
    # plenty of adverse events + non-degenerate SE (so the OLD pool filter
    # would have swept them in and 6x-inflated m).
    untested_entries, resolved_at = dict(sk_r), {}
    entries_by = {"skilled": sk_e}
    resolved_at = dict(sk_r)
    for i in range(500):
        e, r = build(f"late{i}", 30, [1, 0], 0.50,
                     start=CUTOFF + timedelta(days=5), day_step=1)
        entries_by[f"late{i}"] = e
        resolved_at.update(r)

    scores = []
    for name, ents in entries_by.items():
        ts = score_trader(ents, cfg, CUTOFF, resolved_at)
        if ts is not None:
            scores.append(ts)
    scores = select_and_size(scores, entries_by, cfg)
    by = {t.trader: t for t in scores}

    # The placeholders are present but excluded from the family...
    assert by["skilled"].holdout_tested is True
    assert all(not by[f"late{i}"].holdout_tested for i in range(500)
               if f"late{i}" in by)
    # ...so the skilled trader is still admitted despite 500 untested peers.
    assert by["skilled"].admitted is True
    assert by["skilled"].kelly_weight > 0.0
