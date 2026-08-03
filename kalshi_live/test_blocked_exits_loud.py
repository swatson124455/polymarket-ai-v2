"""A BLOCKED EXIT IS NEVER SILENT (defect 10, diagnostic half — 2026-08-02).

Live 2026-08-02T10:32:58Z the daemon printed
    "taker-cross residual 20 ct on KXRAIN-26AUG03-BOS and the re-rest FAILED — no working exit
     until the next cycle unwind pass"
with NO reason attached, and the same rejection then repeated identically. The catch that
produced it (_rest_maker_offset's create) bound nothing — `except Exception:` with no `as e` —
so the error object was discarded into a flat global counter that cannot even name the ticker.

Two things were wrong and both are fixed here:
  1. `{e!r}` on an HTTPError renders only "HTTPError(400, 'Bad Request')". The venue's actual
     reason lives in the response BODY, which the client already attaches
     (maker_kalshi_client._request raises HTTPError(url, status, reason, headers, BytesIO(raw))
     at :159-163) and which nobody read. _err_detail extracts it.
  2. Under STOP, run_once returns before append_plan, so plan-row telemetry does not reach disk
     at all — the journal is the ONLY channel. A blocked exit therefore PRINTS.

NOT IN SCOPE, and deliberately absent: what the bot should DO after N consecutive unwind
failures (retry, cross, mark exit-only, halt). That is a money-path behaviour change and is an
operator decision; this commit only makes the failure legible.
"""
import io
import urllib.error

from test_live_hardening import MockClient, _run, q


def _http_error(status=400, body=b'{"error":{"code":"would_cross","message":"post_only order would cross"}}'):
    return urllib.error.HTTPError("https://api.example/x", status, "Bad Request", {},
                                  io.BytesIO(body))


# ---- _err_detail ---------------------------------------------------------------------------

def test_err_detail_surfaces_status_and_body():
    d = q._err_detail(_http_error())
    assert "400" in d
    assert "would_cross" in d, f"the venue's reason must survive: {d}"


def test_err_detail_falls_back_to_repr_for_non_http():
    d = q._err_detail(RuntimeError("connection reset"))
    assert "connection reset" in d


def test_err_detail_is_bounded():
    d = q._err_detail(_http_error(body=b"x" * 5000), limit=120)
    assert len(d) <= 120


def test_err_detail_survives_an_already_consumed_body():
    e = _http_error()
    e.read()                                    # something upstream drained it
    d = q._err_detail(e)
    assert "400" in d, "status must still render when the body is gone"


def test_err_detail_never_raises_on_a_hostile_exception():
    class Nasty(Exception):
        code = 400
        def read(self):
            raise RuntimeError("nope")
        @property
        def reason(self):
            raise RuntimeError("nope")
    d = q._err_detail(Nasty())
    assert isinstance(d, str) and d, "the logger must not become the failure"


# ---- the exit-rejection path ----------------------------------------------------------------

def _book(monkeypatch):
    monkeypatch.setattr(q, "public_get",
                        lambda p: {"incentive_programs": [], "next_cursor": ""}
                        if "incentive" in p else
                        {"orderbook_fp": {"yes_dollars": [["0.50", "9999"]],
                                          "no_dollars": [["0.49", "9999"]]}})


class _RejectingClient(MockClient):
    def create_quote(self, *a, **kw):
        raise _http_error()


def test_rejected_exit_rerest_names_ticker_and_reason(monkeypatch, capsys):
    # THE LIVE CASE. A rejected EXIT re-rest must say which market and why.
    _book(monkeypatch)
    c = _RejectingClient(mode="live", resting=[], positions=[])
    out_id = q._rest_maker_offset(c, "KXRAIN-26AUG03-BOS", 20.0, 0.0, "rerest")
    assert out_id is None
    out = capsys.readouterr().out
    assert "KXRAIN-26AUG03-BOS" in out, "the ticker must be named"
    assert "would_cross" in out, "the venue's reason must be named"
    assert "EXIT" in out


def test_rejected_exit_still_counts_in_silent(monkeypatch, capsys):
    # The pre-existing RF2 counter (2026-08-01) must not be traded away for the print.
    _book(monkeypatch)
    before = q._SILENT["rerest_fail_create"]
    q._rest_maker_offset(_RejectingClient(mode="live", resting=[], positions=[]),
                         "TT", 20.0, 0.0, "rerest")
    capsys.readouterr()
    assert q._SILENT["rerest_fail_create"] == before + 1


def test_the_other_three_none_paths_stay_distinguishable(monkeypatch, capsys):
    # RF2 split four indistinguishable None paths into separate counters; a create-path print
    # must not blur them. An unpriceable book takes a DIFFERENT counter and prints nothing here.
    monkeypatch.setattr(q, "public_get",
                        lambda p: {"incentive_programs": [], "next_cursor": ""}
                        if "incentive" in p else
                        {"orderbook_fp": {"yes_dollars": [["0.50", "9999"]], "no_dollars": []}})
    before_create = q._SILENT["rerest_fail_create"]
    before_unprice = q._SILENT["rerest_fail_unpriceable"]
    assert q._rest_maker_offset(MockClient(mode="live"), "TT", 20.0, 0.0, "rerest") is None
    capsys.readouterr()
    assert q._SILENT["rerest_fail_unpriceable"] == before_unprice + 1
    assert q._SILENT["rerest_fail_create"] == before_create


def test_err_detail_is_rendered_once_per_exception(monkeypatch):
    """BLIND-REVIEW FIX 2026-08-03. _err_detail drains the HTTPError's ONE-SHOT body via
    e.read(), so calling it twice on the same exception leaves the second caller with an empty
    body. run_once called it once for first_create_err and again for the loud UNWIND print, and
    creates are sorted unwind-first — so the operator-facing message, the whole point of the
    fix, was the one that came out blank. Verified: a second e.read() returns b"".
    This pins the property at the helper level: two renders of ONE exception must not differ."""
    e = _http_error()
    first = q._err_detail(e)
    second = q._err_detail(e)
    assert "would_cross" in first
    assert second != first, (
        "sanity: the body IS one-shot, so a second render differs — which is exactly why "
        "run_once must bind it once")
    assert "body=" in second and "would_cross" not in second


def test_rejected_unwind_create_prints_the_venue_reason(monkeypatch, tmp_path, capsys):
    """The UNWIND rejection print was entirely unpinned (the review showed `if False:` over it
    left the suite green). An exit we cannot place is the one failure that must never be quiet,
    and under STOP the journal is the only channel — so pin the message, not just the state."""
    _book(monkeypatch)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "TT", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    from test_live_hardening import _cfg
    _cfg(monkeypatch)
    _book(monkeypatch)
    c = _RejectingClient(mode="live", resting=[],
                         positions=[{"ticker": "TT", "position_fp": "20",
                                     "market_exposure_dollars": "10.00"}])
    _run(monkeypatch, c, str(tmp_path))
    out = capsys.readouterr().out
    assert "UNWIND create REJECTED" in out, "a blocked exit must be loud"
    assert "TT" in out
    assert "would_cross" in out, (
        "the venue's reason must survive into the operator-facing line, not be drained by an "
        "earlier render")


def test_stop_flatten_offset_rejection_carries_the_body(monkeypatch, tmp_path, capsys):
    # Under STOP the journal is the only channel, so the flatten's own rejection line must
    # carry the reason too.
    import os
    _book(monkeypatch)
    monkeypatch.setattr(q, "STOPFLAT_REPEAT_S", 0.0)
    monkeypatch.setattr(q, "STOP_ESCALATE_S", 0)
    monkeypatch.setattr(q, "STOP_TAKER_MIN_CT", 1e9)
    with open(os.path.join(str(tmp_path), "STOP"), "w") as fh:
        fh.write("halt\n")
    c = _RejectingClient(mode="live", resting=[],
                         positions=[{"ticker": "TT", "position_fp": "20",
                                     "market_exposure_dollars": "10.00"}])
    _run(monkeypatch, c, str(tmp_path))
    out = capsys.readouterr().out
    assert "offset REJECTED" in out
    assert "would_cross" in out, f"the flatten must carry the venue reason too: {out[-400:]}"
