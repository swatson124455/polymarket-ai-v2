"""W12 — price-shape weight in the prospective-capture estimator (ships OFF).

Pins: flag default 0 and byte-identical output when off; ON weights a mid-price book by
~1.0 and a deep-extreme book by 4·p·(1−p) (the KXEURUSDAW signature W10 measured: extreme
presence forecast dollars, credited $0.00); exponent knob scales the discount.
"""
import maker_kalshi_quoter as q

# a simple both-sides-qualifying book: one bid at reference with plenty of size
YL = [(0.50, 500.0)]
NL = [(0.50, 500.0)]
M = {"usd_day": 100.0, "df": 0.9}

EXT_YL = [(0.97, 500.0)]
EXT_NL = [(0.03, 500.0)]


def test_flag_ships_off():
    assert q.W12_PRICE_SHAPE == 0


def test_off_is_noop(monkeypatch):
    base = q._prospective_capture(M, YL, NL, 0.50, 0.50, 10.0)
    monkeypatch.setattr(q, "W12_PRICE_SHAPE", 0)
    assert q._prospective_capture(M, YL, NL, 0.50, 0.50, 10.0) == base


def test_on_mid_price_unchanged_extreme_discounted(monkeypatch):
    off_mid = q._prospective_capture(M, YL, NL, 0.50, 0.50, 10.0)
    off_ext = q._prospective_capture(M, EXT_YL, EXT_NL, 0.97, 0.03, 10.0)
    monkeypatch.setattr(q, "W12_PRICE_SHAPE", 1)
    on_mid = q._prospective_capture(M, YL, NL, 0.50, 0.50, 10.0)
    on_ext = q._prospective_capture(M, EXT_YL, EXT_NL, 0.97, 0.03, 10.0)
    assert abs(on_mid - off_mid) < 1e-9                    # w(0.5) = 1.0 exactly
    assert on_ext < off_ext * 0.15                         # w(0.97) = 0.1164
    assert on_ext > 0.0


def test_mirror_books_weighted_equally(monkeypatch):
    """Review fix: the shape keys on the reflection-invariant MID, so a book and its
    mirror get the same weight (best_y-only weighted them 8.6x apart)."""
    monkeypatch.setattr(q, "W12_PRICE_SHAPE", 1)
    a = q._prospective_capture(M, [(0.90, 500.0)], [(0.05, 500.0)], 0.90, 0.05, 10.0)
    b = q._prospective_capture(M, [(0.05, 500.0)], [(0.90, 500.0)], 0.05, 0.90, 10.0)
    assert abs(a - b) < 1e-12


def test_exponent_knob_deepens_the_discount(monkeypatch):
    monkeypatch.setattr(q, "W12_PRICE_SHAPE", 1)
    e1 = q._prospective_capture(M, EXT_YL, EXT_NL, 0.97, 0.03, 10.0)
    monkeypatch.setattr(q, "W12_SHAPE_EXP", 2.0)
    e2 = q._prospective_capture(M, EXT_YL, EXT_NL, 0.97, 0.03, 10.0)
    assert e2 < e1
