#!/usr/bin/env python3
"""STORM DETECTOR — live per-market volatility state from the D4 tape (read-only).

Operator directive 2026-08-24 (ruling #2): the danger model is "a market goes
nuts for maybe a few hours; we sit out exactly those hours and print beside
them." NOT clock schedules. This classifies every D4-watched ticker CALM or
STORMY from the last two hours of recorder tape:

  STORMY when, inside the trailing 30 minutes:
    - |mid move| >= MOVE_TRIG (default 0.04 = 4 ticks), OR
    - traded volume >= VOL_TRIG contracts (default 50; these books baseline ~0).
  A STORMY ticker returns to CALM only after QUIET_MIN minutes (default 90)
  with neither trigger — storms get a wide berth on both ends.

Thresholds are INITIAL (labeled in output); calibrate after days of tape.
Output: storm_state.json {ticker: {state, since, last_trigger, dmid30, vol30}}
(tmp+rename) + one history line per run in storm_history-YYYYMM.jsonl.
The quoter's future gate CONSUMES storm_state.json; this script never trades.
"""
import datetime
import glob
import json
import os
import sys

DATA = "/opt/pa2-maker-kalshi-live"
OUT = os.path.join(DATA, "storm_state.json")
MOVE_TRIG = float(os.environ.get("STORM_MOVE_TRIG", "0.04"))
VOL_TRIG = float(os.environ.get("STORM_VOL_TRIG", "50"))
QUIET_MIN = float(os.environ.get("STORM_QUIET_MIN", "90"))
LOOKBACK_MIN = 130          # covers the 30-min trigger window + 90-min quiet check


def parse_ts(s):
    return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def classify(now, mids, trades, prev):
    """Pure state machine. mids: [(dt, mid)], trades: [(dt, count)] within
    LOOKBACK. prev: prior entry dict or None. Returns the new entry dict."""
    w30 = now - datetime.timedelta(minutes=30)
    m30 = [m for t, m in mids if t >= w30]
    dmid30 = (max(m30) - min(m30)) if len(m30) >= 2 else 0.0
    vol30 = sum(c for t, c in trades if t >= w30)
    trig = dmid30 >= MOVE_TRIG or vol30 >= VOL_TRIG
    last_trigger = prev.get("last_trigger") if prev else None
    state, since = (prev.get("state", "CALM"), prev.get("since")) if prev \
        else ("CALM", now.isoformat())
    if trig:
        last_trigger = now.isoformat()
        if state != "STORMY":
            state, since = "STORMY", now.isoformat()
    elif state == "STORMY":
        lt = parse_ts(last_trigger) if last_trigger else None
        if lt is None or (now - lt).total_seconds() >= QUIET_MIN * 60:
            state, since = "CALM", now.isoformat()
    return {"state": state, "since": since, "last_trigger": last_trigger,
            "dmid30": round(dmid30, 4), "vol30": round(vol30, 2)}


def load_rows(pattern, ts_key):
    now = datetime.datetime.now(datetime.timezone.utc)
    cut = now - datetime.timedelta(minutes=LOOKBACK_MIN)
    days = {(now - datetime.timedelta(days=d)).strftime("%Y%m%d") for d in (0, 1)}
    out = []
    for day in sorted(days):
        for fp in glob.glob(os.path.join(DATA, pattern.format(day=day))):
            try:
                with open(fp) as f:
                    for ln in f:
                        try:
                            r = json.loads(ln)
                            ts = parse_ts(r.get(ts_key))
                        except (ValueError, TypeError):
                            continue
                        if ts >= cut:
                            out.append((ts, r))
            except OSError:
                continue
    return out


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    books = load_rows("d4_books-{day}.jsonl", "ts")
    trades = load_rows("d4_trades-{day}.jsonl", "ts")
    mids, tvol = {}, {}
    for ts, r in books:
        if r.get("mid") is not None:
            mids.setdefault(r.get("ticker"), []).append((ts, float(r["mid"])))
    for ts, r in trades:
        n = sum(float(x.get("count") or x.get("count_fp") or 0)
                for x in (r.get("trades") or []))
        if n:
            tvol.setdefault(r.get("ticker"), []).append((ts, n))
    try:
        prev = json.load(open(OUT)).get("tickers", {})
    except (OSError, ValueError):
        prev = {}
    tickers = sorted(set(mids) | set(tvol) | set(prev))
    state = {t: classify(now, mids.get(t, []), tvol.get(t, []), prev.get(t))
             for t in tickers}
    doc = {"ts": now.isoformat(), "thresholds_status": "INITIAL-UNCALIBRATED",
           "move_trig": MOVE_TRIG, "vol_trig": VOL_TRIG, "quiet_min": QUIET_MIN,
           "tickers": state}
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=1)
    os.replace(tmp, OUT)
    hist = os.path.join(DATA, f"storm_history-{now.strftime('%Y%m')}.jsonl")
    stormy = {t: s for t, s in state.items() if s["state"] == "STORMY"}
    with open(hist, "a") as f:
        f.write(json.dumps({"ts": now.isoformat(), "n": len(state),
                            "stormy": stormy}) + "\n")
    print(f"{now.isoformat()} storm: {len(stormy)}/{len(state)} STORMY "
          f"{sorted(stormy) if stormy else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
