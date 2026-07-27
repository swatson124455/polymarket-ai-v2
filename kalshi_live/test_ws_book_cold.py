"""STAGE C (option B) — the cold cycle's book reads served from the WS mirror.

Run: python -m pytest test_ws_book_cold.py -q   (from the probe dir)

Every test whose name ends in *_kills_broken_variant pins a defect that would
either cost money or silently re-introduce the 12.5s of serialized REST this
change exists to remove. The mocks are hostile on purpose: a providers that
raises, a mirror that mutates under the reader, a feed that was never ACKed,
a cycle that throws. A safety test that cannot kill a broken implementation
pins nothing.

THE FOUR THINGS PINNED
  P1 FLAG-OFF / NO-PROVIDER IS A PROVABLE NO-OP — BOOK_SOURCE None means every
     book is the same REST fetch on the same path as legacy.
  P2 DECLINE ALWAYS MEANS REST — every arm of the staleness predicate falls back
     to a fresh REST book; none of them can make a book be skipped or guessed.
  P3 EQUIVALENCE — a full run_once served entirely from the mirror produces the
     IDENTICAL order plan as the same cycle served over REST, with zero REST
     book reads. This is the test that would catch a mirror wired to the wrong
     side, the wrong dialect, or the wrong ticker.
  P4 THE PROVIDER CANNOT BREAK A CYCLE — raising, or racing a live mutation,
     degrades to REST and is COUNTED (book_src_err), never swallowed silently.
"""
import os

import pytest

import kalshi_ws_feed as F
import maker_kalshi_ws_daemon as D
from test_live_hardening import q, MockClient, _run


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------
# test_live_hardening._load() re-executes maker_kalshi_quoter into a FRESH module object and
# rebinds sys.modules, so `q` is NOT the same object as the `M` the daemon imported. Daemon-level
# tests must therefore drive D.M, and quoter-level tests drive q. Keeping both explicit (rather
# than aliasing one onto the other) avoids leaking a mutation into the rest of the suite.
DM = D.M


@pytest.fixture(autouse=True)
def _clean_book_source():
    """No test may leak a provider into the next one (or into the rest of the suite)."""
    for mod in {id(q): q, id(DM): DM}.values():
        mod.BOOK_SOURCE = None
        mod._book_src.update(mirror=0, rest=0, src_err=0)
    yield
    for mod in {id(q): q, id(DM): DM}.values():
        mod.BOOK_SOURCE = None
        mod._book_src.update(mirror=0, rest=0, src_err=0)


def _ob(y, n, depth=1000):
    """orderbook_fp payload, one best level per side."""
    return {"yes_dollars": [[f"{y:.4f}", f"{depth:.2f}"]],
            "no_dollars": [[f"{n:.4f}", f"{depth:.2f}"]]}


class _FakeFeed:
    """Enough of Feed for the predicate: mirrors + confirmed_channels."""

    def __init__(self, mirrors=None, channels=("orderbook_delta",)):
        self.mirrors = mirrors or {}
        self.confirmed_channels = set(channels)


def _mirror(ticker="T1", y=0.50, n=0.48, depth=1000.0, dirty=False):
    m = F.BookMirror(ticker)
    if not dirty:
        m.apply_snapshot({"yes_dollars_fp": [[f"{y:.4f}", f"{depth:.2f}"]],
                          "no_dollars_fp": [[f"{n:.4f}", f"{depth:.2f}"]]}, seq=1)
    return m


def _daemon(feed):
    """A Daemon whose __init__ is bypassed — we only exercise mirror_book."""
    d = D.Daemon.__new__(D.Daemon)
    d.feed = feed
    return d


# =============================================================================================
# P1 — flag off / no provider is a provable no-op
# =============================================================================================
def test_p1_no_provider_reads_rest_on_the_legacy_path(monkeypatch):
    seen = []

    def fake_get(path):
        seen.append(path)
        return {"orderbook_fp": _ob(0.50, 0.48)}

    monkeypatch.setattr(q, "public_get", fake_get)
    ob = q._get_book("KXT-1")
    assert seen == ["/trade-api/v2/markets/KXT-1/orderbook"]     # exact legacy path
    assert ob == _ob(0.50, 0.48)
    assert (q._book_src["mirror"], q._book_src["rest"]) == (0, 1)


def test_p1_missing_orderbook_fp_still_yields_empty_dict(monkeypatch):
    """Legacy `.get("orderbook_fp") or {}` semantics must survive the indirection."""
    monkeypatch.setattr(q, "public_get", lambda p: {})
    assert q._get_book("KXT-1") == {}


def _cold_daemon(feed=None):
    """A Daemon wired for cold_cycle only. mode='dry_run' short-circuits the ctx build
    (cold_cycle: `not STOP and self.client.mode != "dry_run"`), so no venue reads happen."""
    import threading
    d = D.Daemon.__new__(D.Daemon)
    d.feed = feed
    d.cold_lock = threading.Lock()
    d.std_lock = threading.Lock()
    d.client = MockClient(mode="dry_run")
    d.ctx = D.HotContext()
    return d


def test_p1_flag_off_never_installs_the_provider(monkeypatch):
    """WS_BOOK_COLD=0 => cold_cycle leaves BOOK_SOURCE alone. Kills 'shipped it armed'."""
    d = _cold_daemon(_FakeFeed({"T1": _mirror()}))
    seen = {}
    monkeypatch.setattr(D, "WS_BOOK_COLD", 0)
    monkeypatch.setattr(DM, "run_once", lambda: seen.setdefault("src", DM.BOOK_SOURCE))
    monkeypatch.setattr(D, "_log", lambda row: None)
    d.cold_cycle()
    assert seen["src"] is None
    assert DM.BOOK_SOURCE is None


def test_p1_flag_on_installs_the_provider_for_the_cycle(monkeypatch):
    """The positive control for the test above: with the flag ON the provider IS live
    during run_once, and gone after. Without this, flag-off passing proves nothing."""
    d = _cold_daemon(_FakeFeed({"T1": _mirror()}))
    seen = {}
    monkeypatch.setattr(D, "WS_BOOK_COLD", 1)
    monkeypatch.setattr(DM, "run_once", lambda: seen.setdefault("src", DM.BOOK_SOURCE))
    monkeypatch.setattr(D, "_log", lambda row: None)
    d.cold_cycle()
    assert seen["src"] == d.mirror_book
    assert DM.BOOK_SOURCE is None


# =============================================================================================
# P2 — every decline arm falls back to a fresh REST book
# =============================================================================================
@pytest.mark.parametrize("feed,label", [
    (None, "no feed yet (first cold cycle)"),
    (_FakeFeed({"T1": _mirror()}, channels=()), "channel never ACKed"),
    (_FakeFeed({"T1": _mirror()}, channels=("fill",)), "wrong channel ACKed"),
    (_FakeFeed({}), "ticker not watched"),
    (_FakeFeed({"T1": _mirror(dirty=True)}), "mirror dirty (gap / never seeded)"),
])
def test_p2_predicate_declines_and_cycle_falls_back_to_rest(monkeypatch, feed, label):
    d = _daemon(feed)
    assert d.mirror_book("T1") is None, f"must decline: {label}"
    monkeypatch.setattr(q, "public_get", lambda p: {"orderbook_fp": _ob(0.51, 0.47)})
    q.BOOK_SOURCE = d.mirror_book
    assert q._get_book("T1") == _ob(0.51, 0.47)               # fresh REST, not a guess
    assert (q._book_src["mirror"], q._book_src["rest"]) == (0, 1)


def test_p2_clean_mirror_is_served_and_rest_is_not_called(monkeypatch):
    d = _daemon(_FakeFeed({"T1": _mirror(y=0.50, n=0.48, depth=1000.0)}))
    called = []
    monkeypatch.setattr(q, "public_get", lambda p: called.append(p) or {})
    q.BOOK_SOURCE = d.mirror_book
    ob = q._get_book("T1")
    assert called == []                                       # THE POINT: no network
    assert q._levels(ob["yes_dollars"])[0] == [(0.50, 1000.0)]
    assert q._levels(ob["no_dollars"])[0] == [(0.48, 1000.0)]
    assert (q._book_src["mirror"], q._book_src["rest"]) == (1, 0)


def test_p2_falsy_book_from_a_provider_is_still_an_ANSWER_kills_broken_variant(monkeypatch):
    """_get_book's contract is `None declines, anything else answers` — NOT truthiness.
    A provider handing back a bare {} (the shape run_once itself uses for "no orderbook_fp")
    must be served, not silently re-fetched over REST.

    (Found by mutation testing: the mirror provider only ever returns a two-key dict, which is
    truthy, so `if ob:` passed every other test. This pins the CONTRACT rather than today's
    single implementation of it — the next provider is what would get bitten.)"""
    called = []
    monkeypatch.setattr(q, "public_get", lambda p: called.append(p) or {"orderbook_fp": _ob(0.9, 0.05)})
    q.BOOK_SOURCE = lambda _t: {}
    assert q._get_book("T1") == {}
    assert called == [], "a falsy-but-present answer was thrown away and re-fetched"
    assert (q._book_src["mirror"], q._book_src["rest"]) == (1, 0)


def test_p2_empty_book_is_an_ANSWER_not_a_decline_kills_broken_variant(monkeypatch):
    """A seeded book that is genuinely empty must be SERVED. An implementation that
    tests truthiness (`if ob:`) instead of `is not None` would issue a REST read on
    every quiet market — silently restoring the cost this change removes."""
    m = F.BookMirror("T1")
    m.apply_snapshot({"yes_dollars_fp": [], "no_dollars_fp": []}, seq=1)
    assert not m.dirty and m.yes == {} and m.no == {}
    d = _daemon(_FakeFeed({"T1": m}))
    called = []
    monkeypatch.setattr(q, "public_get", lambda p: called.append(p) or {})
    q.BOOK_SOURCE = d.mirror_book
    ob = q._get_book("T1")
    assert called == []
    assert ob == {"yes_dollars": [], "no_dollars": []}
    # and the caller's empty-book detection still fires on this shape
    assert not (ob.get("yes_dollars") or ob.get("no_dollars"))
    assert (q._book_src["mirror"], q._book_src["rest"]) == (1, 0)


def test_p2_dirty_at_entry_is_declined_even_if_it_cleans_mid_read_kills_broken_variant():
    """The ENTRY dirty check has to exist in its own right. A mirror that is dirty when we
    start (never seeded / just gapped) holds garbage; if a snapshot re-seeds it while we are
    serializing rows, the rows we already took are still the garbage. Dropping the entry check
    and relying on the post-read recheck alone returns that garbage as a clean book — the
    post-read check cannot see it, because by then the mirror IS clean.

    (Found by mutation testing: without this test, deleting `or m.dirty` from the entry guard
    left the whole suite green.)"""
    class _CleansMidRead(F.BookMirror):
        def rows(self):
            out = super().rows()
            self.dirty = False                                # a snapshot lands mid-read
            return out

    m = _CleansMidRead("T1")
    m.yes = {0.99: 1.0}                                       # stale garbage from before the gap
    m.no = {0.99: 1.0}
    m.dirty = True                                            # never seeded / gapped
    assert _daemon(_FakeFeed({"T1": m})).mirror_book("T1") is None


def test_p2_dirtied_mid_read_is_declined_kills_broken_variant():
    """A gap arriving WHILE we serialize the rows must void the answer. An
    implementation that checks `dirty` only on entry would hand the cold cycle a
    book it already knows it misassembled."""
    class _DirtiesOnRead(F.BookMirror):
        def rows(self):
            out = super().rows()
            self.dirty = True                                 # gap lands mid-read
            return out

    m = _DirtiesOnRead("T1")
    m.apply_snapshot({"yes_dollars_fp": [["0.5000", "10"]],
                      "no_dollars_fp": [["0.4800", "10"]]}, seq=1)
    assert _daemon(_FakeFeed({"T1": m})).mirror_book("T1") is None


def test_p2_feed_is_dereferenced_per_call_kills_broken_variant():
    """A resubscribe swaps in a NEW Feed with fresh mirrors. An implementation that
    cached feed.mirrors at install time would keep answering from the dead feed's
    stale books after every footprint change."""
    d = _daemon(_FakeFeed({"T1": _mirror(y=0.50, n=0.48)}))
    assert d.mirror_book("T1") is not None
    d.feed = _FakeFeed({"T2": _mirror("T2")})                 # resubscribed: T1 gone
    assert d.mirror_book("T1") is None
    assert d.mirror_book("T2") is not None


# =============================================================================================
# P3 — equivalence: a full cycle off the mirror == the same cycle off REST
# =============================================================================================
def _equiv_progs():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    s, e = now - timedelta(minutes=60), now + timedelta(minutes=240)
    return [{"market_ticker": f"KXEQ-26JUL27-{i}", "incentive_type": "liquidity",
             "target_size_fp": 1, "discount_factor_bps": 5000, "period_reward": 3_000_000,
             "start_date": s.isoformat().replace("+00:00", "Z"),
             "end_date": e.isoformat().replace("+00:00", "Z")} for i in range(6)]


_EQUIV_BOOKS = {f"KXEQ-26JUL27-{i}": _ob(0.50 + 0.01 * i, 0.48 - 0.01 * i, depth=500 + 10 * i)
                for i in range(6)}


def _equiv_cfg(monkeypatch):
    monkeypatch.setattr(q, "FOOTPRINT_TOP", 6)
    monkeypatch.setattr(q, "PER_SERIES_CAP", 10)
    monkeypatch.setattr(q, "MAX_TOTAL_CAPITAL", 1e6)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 1e6)
    monkeypatch.setattr(q, "SERIES_ALLOW", [])
    monkeypatch.setattr(q, "JOIN_SIZE", 20)
    monkeypatch.setattr(q, "PIVOT_SELECT", 0)


def _equiv_public_get(hits):
    def _get(path):
        if "incentive" in path:
            return {"incentive_programs": _equiv_progs(), "next_cursor": ""}
        if "/orderbook" in path:
            t = path.split("/markets/")[1].split("/orderbook")[0]
            hits.append(t)
            return {"orderbook_fp": _EQUIV_BOOKS[t]}
        return {}
    return _get


def test_p3_mirror_served_cycle_equals_rest_served_cycle(monkeypatch, tmp_path):
    """THE test. Same programs, same books, two sources -> identical order plan,
    and the mirror run performs ZERO REST book reads."""
    _equiv_cfg(monkeypatch)
    dir_a, dir_b = tmp_path / "a", tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()

    # --- run A: REST only (legacy) ---
    hits_a = []
    monkeypatch.setattr(q, "public_get", _equiv_public_get(hits_a))
    ca = MockClient(mode="live")
    plan_a = _run(monkeypatch, ca, str(dir_a))

    # --- run B: every book from the mirror ---
    mirrors = {}
    for t, ob in _EQUIV_BOOKS.items():
        m = F.BookMirror(t)
        m.apply_snapshot({"yes_dollars_fp": ob["yes_dollars"],
                          "no_dollars_fp": ob["no_dollars"]}, seq=1)
        mirrors[t] = m
    hits_b = []
    monkeypatch.setattr(q, "public_get", _equiv_public_get(hits_b))
    q.BOOK_SOURCE = _daemon(_FakeFeed(mirrors)).mirror_book
    cb = MockClient(mode="live")
    plan_b = _run(monkeypatch, cb, str(dir_b))

    assert hits_a, "control run must actually have read books over REST"
    assert hits_b == [], f"mirror run still hit REST for books: {hits_b}"
    assert q._book_src["mirror"] == len(hits_a) and q._book_src["rest"] == 0

    # the ORDERS are what matter — identical, not merely similar
    norm = lambda c: sorted((o["ticker"], o["side"], round(float(o["price"]), 4),
                             float(o["count"])) for o in c.created)
    assert norm(cb) == norm(ca)
    assert norm(ca), "control run must actually have created orders"
    for k in ("quoted_markets", "two_sided_markets", "footprint", "gated_out",
              "creates", "cancels", "order_ops", "committed_usd"):
        assert plan_b.get(k) == plan_a.get(k), f"plan field {k} diverged"


# =============================================================================================
# P4 — a broken provider degrades to REST and is COUNTED
# =============================================================================================
def test_p4_raising_provider_falls_back_and_is_counted_kills_broken_variant(monkeypatch):
    """A provider bug must cost a REST read, not a live trading cycle. An
    implementation that let the exception escape would take down the whole
    footprint loop through its `except Exception -> fetch_failed` arm and
    silently retain every standing order."""
    def _boom(_t):
        raise ValueError("mirror path is broken")

    monkeypatch.setattr(q, "public_get", lambda p: {"orderbook_fp": _ob(0.50, 0.48)})
    q.BOOK_SOURCE = _boom
    assert q._get_book("T1") == _ob(0.50, 0.48)
    assert q._book_src == {"mirror": 0, "rest": 1, "src_err": 1}


def test_p4_concurrent_mutation_never_raises_kills_broken_variant():
    """run_once reads the mirror from a WORKER thread while the event loop applies
    deltas, so a dict resize mid-sort raises RuntimeError. It must degrade to REST,
    never propagate."""
    class _AlwaysRacing(F.BookMirror):
        def rows(self):
            raise RuntimeError("dictionary changed size during iteration")

    m = _AlwaysRacing("T1")
    m.apply_snapshot({"yes_dollars_fp": [["0.5000", "10"]],
                      "no_dollars_fp": [["0.4800", "10"]]}, seq=1)
    assert _daemon(_FakeFeed({"T1": m})).mirror_book("T1") is None


def test_p4_transient_race_retries_then_succeeds():
    """One racing read must not permanently demote a healthy ticker to REST."""
    class _RacesOnce(F.BookMirror):
        n = 0

        def rows(self):
            _RacesOnce.n += 1
            if _RacesOnce.n == 1:
                raise RuntimeError("dictionary changed size during iteration")
            return super().rows()

    m = _RacesOnce("T1")
    m.apply_snapshot({"yes_dollars_fp": [["0.5000", "10"]],
                      "no_dollars_fp": [["0.4800", "10"]]}, seq=1)
    out = _daemon(_FakeFeed({"T1": m})).mirror_book("T1")
    assert out is not None and out["yes_dollars"] == [["0.5000", "10.00"]]


def test_p4_provider_is_uninstalled_even_when_the_cycle_raises(monkeypatch):
    """A throwing run_once must not leave the mirror installed as a global for a
    later run in this process."""
    d = _cold_daemon(_FakeFeed({"T1": _mirror()}))
    seen = {}

    def _boom():
        seen["src"] = DM.BOOK_SOURCE
        raise RuntimeError("cycle exploded")

    monkeypatch.setattr(D, "WS_BOOK_COLD", 1)
    monkeypatch.setattr(DM, "run_once", _boom)
    monkeypatch.setattr(D, "_log", lambda row: None)
    d.cold_cycle()
    assert seen["src"] is not None, "provider must have been installed for the cycle"
    assert DM.BOOK_SOURCE is None, "provider leaked past a raising cycle"
