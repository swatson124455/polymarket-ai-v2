"""Pins for audit batch 2 + the operator-named halt confirmation (2026-07-29).

  C1 HALT NEEDS A SUSTAINED BREACH — one blip cycle cannot write STOP; N consecutive can;
     recovery resets the streak.
  C2 SAFETY KNOBS LIVE-APPLY FROM THE ENV FILE — an operator edit takes effect next cycle,
     is printed, and malformed values / unset KALSHI_ENV_FILE change nothing.
  C3 CORRUPT STATE IS LOUD AND PRESERVED — absent file stays a silent cold start; an
     unreadable file is moved aside, counted, and warned about.
  C4 KILL SCRIPT PAGINATES — cursor-follow until exhausted; a cursored page-1 response
     cannot produce a plausible "FLAT" zero.
"""
import datetime as dt
import json
import os

from test_live_hardening import q, MockClient, _run, _cfg


def _fp1(monkeypatch):
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])


# ---- C1 ----

def _drawdown_setup(monkeypatch, tmp_path, peak):
    _cfg(monkeypatch)
    _fp1(monkeypatch)
    monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 40.0)
    monkeypatch.setattr(q, "DAILY_DOWN_HALT_USD", 999.0)
    day = q.utcnow().strftime("%Y%m%d")
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
        json.dump({"equity_day": day, "equity_day_start": peak, "equity_day_peak": peak,
                   "equity_day_down": 0.0, "equity_prev": peak, "equity_prev_cost": peak,
                   "down_basis": "cost", "equity_basis": "mark"}, fh)


class _BalClient(MockClient):
    def __init__(self, bal, **kw):
        super().__init__(**kw)
        self._bal = bal
    def get_balance(self):
        return {"balance_dollars": f"{self._bal:.4f}"}


def test_halt_requires_sustained_breach(monkeypatch, tmp_path):
    monkeypatch.setattr(q, "HALT_CONFIRM_N", 3)
    _drawdown_setup(monkeypatch, tmp_path, peak=300.0)
    stop = os.path.join(str(tmp_path), "STOP")
    # breach cycle 1 and 2: dd = $50 > $40 but streak < 3 -> NO stop
    for i in (1, 2):
        row = _run(monkeypatch, _BalClient(250.0, mode="live"), str(tmp_path))
        assert not os.path.exists(stop), f"breach cycle {i} must not halt yet"
        assert row.get("halt_breach_streak") == i
    # recovery cycle: back inside the arm -> streak resets
    _run(monkeypatch, _BalClient(295.0, mode="live"), str(tmp_path))
    assert not os.path.exists(stop)
    st = json.load(open(os.path.join(str(tmp_path), "quoter_state.json")))
    assert st.get("halt_breach_streak") == 0, "recovery must reset the streak"
    # three consecutive breaches -> STOP written on the third
    for i in (1, 2, 3):
        _run(monkeypatch, _BalClient(250.0, mode="live"), str(tmp_path))
    assert os.path.exists(stop), "a sustained breach must still halt"


def test_halt_confirm_default_and_legacy_mode(monkeypatch, tmp_path):
    assert q.HALT_CONFIRM_N == 3
    monkeypatch.setattr(q, "HALT_CONFIRM_N", 1)          # legacy: fire on first breach
    _drawdown_setup(monkeypatch, tmp_path, peak=300.0)
    _run(monkeypatch, _BalClient(250.0, mode="live"), str(tmp_path))
    assert os.path.exists(os.path.join(str(tmp_path), "STOP"))


# ---- C2 ----

def test_safety_knobs_live_apply_from_env_file(monkeypatch, tmp_path):
    envf = os.path.join(str(tmp_path), "live.env")
    with open(envf, "w") as fh:
        fh.write("# comment\nKALSHI_DAILY_LOSS_HALT_USD=55\nKALSHI_TAKER_FLATTEN=0\n"
                 "KALSHI_UNRELATED_KNOB=999\nKALSHI_HELD_MAX_USD=garbage\n")
    monkeypatch.setenv("KALSHI_ENV_FILE", envf)
    monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 40.0)
    monkeypatch.setattr(q, "TAKER_FLATTEN", 1)
    monkeypatch.setattr(q, "HELD_MAX_USD", 100.0)
    monkeypatch.setattr(q, "FOOTPRINT_TOP", q.FOOTPRINT_TOP)   # unrelated stays put
    q._refresh_safety_knobs()
    assert q.DAILY_LOSS_HALT_USD == 55.0, "operator's file edit must apply next cycle"
    assert q.TAKER_FLATTEN == 0
    assert q.HELD_MAX_USD == 100.0, "malformed value must keep the running one"


def test_knob_refresh_is_noop_without_env_file(monkeypatch):
    monkeypatch.delenv("KALSHI_ENV_FILE", raising=False)
    monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 40.0)
    q._refresh_safety_knobs()
    assert q.DAILY_LOSS_HALT_USD == 40.0
    monkeypatch.setenv("KALSHI_ENV_FILE", "/nonexistent/x.env")
    base = q._SILENT["knob_refresh_fail"]
    q._refresh_safety_knobs()                            # must not raise
    assert q._SILENT["knob_refresh_fail"] == base + 1


# ---- C3 ----

def test_corrupt_state_is_loud_and_preserved(monkeypatch, tmp_path):
    sf = os.path.join(str(tmp_path), "quoter_state.json")
    monkeypatch.setattr(q, "STATE_FILE", sf)
    # absent -> silent cold start
    base = q._SILENT["state_corrupt"]
    assert q.load_state() == {}
    assert q._SILENT["state_corrupt"] == base
    # present-but-unreadable -> counted, moved aside, returns {}
    with open(sf, "w") as fh:
        fh.write("{not json")
    assert q.load_state() == {}
    assert q._SILENT["state_corrupt"] == base + 1
    assert not os.path.exists(sf), "corrupt file must be moved aside, not left to re-fail"
    assert [f for f in os.listdir(str(tmp_path)) if f.startswith("quoter_state.json.corrupt-")]


# ---- C4 ----

def test_kill_script_paginates(monkeypatch, tmp_path):
    monkeypatch.setenv("KALSHI_API_KEY_ID", "test")
    pem = os.path.join(str(tmp_path), "k.pem"); open(pem, "w").close()
    monkeypatch.setenv("KALSHI_RSA_PRIVATE_KEY_PATH", pem)
    import flatten_kalshi as fk
    pages = {"": ({"orders": [{"order_id": f"o{i}"} for i in range(100)], "cursor": "PG2"}),
             "PG2": ({"orders": [{"order_id": "o100"}], "cursor": ""})}
    calls = []
    def fake_req(method, path):
        calls.append(path)
        cur = path.split("cursor=")[1] if "cursor=" in path else ""
        return 200, pages[cur]
    monkeypatch.setattr(fk, "req", fake_req)
    out = fk.resting()
    assert len(out) == 101, "must follow the cursor past page 1"
    assert len(calls) == 2
