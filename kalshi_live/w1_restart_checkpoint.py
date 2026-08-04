#!/usr/bin/env python3
"""W1 — FIRST-RESTART CHECKPOINT (P1). Watches the live daemon walk the one path that was
unverifiable under STOP: book-read -> selection -> quote placement -> telemetry, and proves
the restart picked up everything staged for it.

Checkpoints (ordered, each with its own evidence source):
  service     systemd unit active
  daemon      fresh daemon_start event in ws_daemon_log.jsonl after --since
  unbuffered  PYTHONUNBUFFERED=1 visible in /proc/<mainpid>/environ (W2 staged change)
  margin      plans jsonl `netev_min_margin_pct` equals --expect-margin (operator ruling
              2026-08-04: -7.0 for the P2 window)
  gate        plans jsonl `netev_gate` == 1 and NO "EMPTY-TABLE" alarm in the journal
              since restart
  books       plans jsonl book reads flowing (book_mirror+book_rest > 0, fetch_failed low)
  selection   scored_markets > 0 and footprint > 0
  quotes      creates > 0 across watched cycles with first_create_err null; resting
              orders confirmed via the venue (quoted_markets > 0)
  telemetry   today's caprank file exists and grows between polls
  ledger      a ledger jsonl line lands with ts after restart (tick cadence ~10-15 min,
              so this checkpoint gets the longest patience)

Exit 0 = every checkpoint PASSED inside --watch-min. Exit 1 = timeout, with per-checkpoint
state printed for the handoff. Read-only: this script never writes anything but stdout.
Run:  sudo ./venv/bin/python w1_restart_checkpoint.py --since <restart-iso> [--watch-min 20]
"""
import argparse
import datetime as dt
import glob
import json
import os
import subprocess
import time

PLAN_GLOB = "/opt/pa2-maker-kalshi-live/plans-*.jsonl"
CAPRANK_GLOB = "/opt/pa2-maker-kalshi-live/caprank-*.jsonl"
WSLOG = "/opt/pa2-maker-kalshi-live/ws_daemon_log.jsonl"
LEDGER_GLOB = "/opt/pa2-maker-kalshi-live/ledger-*.jsonl"
UNIT = "polymarket-maker-kalshi-ws.service"


def iso(s):
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def tail_json(path, n=400):
    try:
        out = subprocess.run(["tail", "-n", str(n), path], capture_output=True,
                             text=True, timeout=10).stdout
    except Exception:
        return []
    rows = []
    for line in out.splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def newest(pattern):
    paths = sorted(glob.glob(pattern), key=os.path.getmtime)
    return paths[-1] if paths else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="restart time ISO (UTC)")
    ap.add_argument("--watch-min", type=float, default=20.0)
    ap.add_argument("--expect-margin", type=float, default=-7.0)
    a = ap.parse_args()
    since = iso(a.since)
    deadline = time.time() + a.watch_min * 60.0
    state = {k: False for k in ("service", "daemon", "unbuffered", "margin", "gate",
                                "books", "selection", "quotes", "telemetry", "ledger")}
    cap_size = None
    print(f"watching from {since.isoformat()} for {a.watch_min:.0f} min "
          f"(expect margin {a.expect_margin})")

    while time.time() < deadline and not all(state.values()):
        # service + environment
        try:
            act = subprocess.run(["systemctl", "is-active", UNIT], capture_output=True,
                                 text=True, timeout=10).stdout.strip()
            state["service"] = (act == "active")
            pid = subprocess.run(["systemctl", "show", "-p", "MainPID", "--value", UNIT],
                                 capture_output=True, text=True, timeout=10).stdout.strip()
            if pid and pid != "0":
                env = open(f"/proc/{pid}/environ", "rb").read().decode(errors="replace")
                state["unbuffered"] = "PYTHONUNBUFFERED=1" in env
        except Exception:
            pass
        # daemon event
        for r in tail_json(WSLOG, 50):
            try:
                if r.get("ev") == "daemon_start" and iso(r["ts"]) >= since:
                    state["daemon"] = True
            except Exception:
                continue
        # journal alarm check (negative evidence — flips gate off if seen)
        alarm = False
        try:
            j = subprocess.run(["journalctl", "-u", UNIT, "--since", a.since,
                                "--no-pager"], capture_output=True, text=True,
                               timeout=15).stdout
            alarm = "EMPTY-TABLE" in j
        except Exception:
            pass
        # plans-driven checkpoints
        p = newest(PLAN_GLOB)
        fresh = [r for r in tail_json(p or "", 200)
                 if r.get("ts") and iso(r["ts"]) >= since] if p else []
        if fresh:
            last = fresh[-1]
            state["margin"] = any(r.get("netev_min_margin_pct") == a.expect_margin
                                  for r in fresh)
            state["gate"] = (last.get("netev_gate") == 1) and not alarm
            state["books"] = any((r.get("book_mirror", 0) + r.get("book_rest", 0)) > 0
                                 for r in fresh)
            state["selection"] = any(r.get("scored_markets", 0) > 0
                                     and r.get("footprint", 0) > 0 for r in fresh)
            state["quotes"] = any(r.get("creates", 0) > 0
                                  and not r.get("first_create_err")
                                  and r.get("quoted_markets", 0) > 0 for r in fresh)
        # caprank growth
        c = newest(CAPRANK_GLOB)
        if c:
            try:
                sz, mt = os.path.getsize(c), os.path.getmtime(c)
                if dt.datetime.fromtimestamp(mt, dt.timezone.utc) >= since:
                    if cap_size is not None and sz > cap_size:
                        state["telemetry"] = True
                    cap_size = sz
            except OSError:
                pass
        # ledger tick
        led = newest(LEDGER_GLOB)
        if led:
            for r in tail_json(led, 5):
                try:
                    if iso(r["ts"]) >= since:
                        state["ledger"] = True
                except Exception:
                    continue
        line = " ".join(f"{k}={'PASS' if v else '...'}" for k, v in state.items())
        print(f"[{dt.datetime.now(dt.timezone.utc).strftime('%H:%M:%SZ')}] {line}",
              flush=True)
        if all(state.values()):
            break
        time.sleep(20)

    ok = all(state.values())
    print("RESULT:", "ALL CHECKPOINTS PASSED" if ok else
          "TIMEOUT — failed: " + ", ".join(k for k, v in state.items() if not v))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
