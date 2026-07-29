#!/usr/bin/env python3
"""MAKER SIGNAL-ONLY MONITOR — filter the journal down to what matters
(session-8 E-F; the Kalshi signal-only monitor norm re-derived Poly-native).

Reads engine journal lines on STDIN, prints ONLY signals; quiet ticks get no
prose (the operator watches signal, not wallpaper):

  - KILL / HALT / ERROR / Traceback lines (verbatim)
  - halted flag TRANSITIONS (False->True / True->False)
  - day_pnl moving more than --move dollars between heartbeats (paper marks
    are NOISE-tier: the jump is the signal, the level is not a quotable P&L)
  - derisk1 INCREASING (one-sided de-risk fired — cap pressure)
  - q collapsing to 0 while not halted
  - zombies / lmiss / feedfail / anom going NONZERO
  - one heartbeat echoed every --hb-every seconds as liveness proof

READ-ONLY; consumes text, touches nothing. Usage:
  journalctl -u polymarket-maker-live -f | python mm_live_monitor.py
  journalctl -u polymarket-maker-live --since -6h | python mm_live_monitor.py
"""
import argparse
import re
import sys
import time

HB = re.compile(r"hb\[(?P<mode>\w+)\]: q=(?P<q>\d+)/(?P<n>\d+) .*?"
                r"dayPnL=\$(?P<pnl>-?[\d.]+) .*?halted=(?P<halted>\w+) "
                r"zombies=(?P<z>\d+) lmiss=(?P<lm>\d+) feedfail=(?P<ff>\d+) "
                r"derisk1=(?P<d1>\d+) anom=(?P<an>\d+)/\d+")
ALERT = re.compile(r"KILL:|HALT|ERROR|Traceback|Exception|FAILED", re.I)


def scan(lines, move=5.0, hb_every=3600.0, out=print, clock=time.time):
    prev, last_echo, n_sig = None, 0.0, 0
    for line in lines:
        line = line.rstrip("\n")
        m = HB.search(line)
        if not m:
            if ALERT.search(line):
                out(f"SIGNAL {line}")
                n_sig += 1
            continue
        cur = {k: (v if k in ("mode", "halted") else float(v))
               for k, v in m.groupdict().items()}
        now = clock()
        if prev:
            if cur["halted"] != prev["halted"]:
                out(f"SIGNAL halted {prev['halted']} -> {cur['halted']}: {line}")
                n_sig += 1
            if abs(cur["pnl"] - prev["pnl"]) >= move:
                out(f"SIGNAL dayPnL moved {cur['pnl'] - prev['pnl']:+.2f} "
                    f"(NOISE-tier marks; the JUMP is the signal): {line}")
                n_sig += 1
            if cur["d1"] > prev["d1"]:
                out(f"SIGNAL derisk1 {prev['d1']:.0f} -> {cur['d1']:.0f} "
                    f"(one-sided de-risk fired; frequent firing = cap "
                    f"sizing pressure, GAP-4): {line}")
                n_sig += 1
            if cur["q"] == 0 and prev["q"] > 0 and cur["halted"] != "True":
                out(f"SIGNAL quoting collapsed q={prev['q']:.0f} -> 0 "
                    f"while NOT halted: {line}")
                n_sig += 1
        for k, label in (("z", "zombies"), ("lm", "lmiss"),
                         ("ff", "feedfail"), ("an", "anom")):
            if cur[k] > 0 and (not prev or prev[k] == 0):
                out(f"SIGNAL {label} nonzero: {line}")
                n_sig += 1
        if now - last_echo >= hb_every:
            out(f"hb-echo {line}")
            last_echo = now
        prev = cur
    return n_sig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--move", type=float, default=5.0,
                    help="dayPnL jump ($) between heartbeats that signals")
    ap.add_argument("--hb-every", type=float, default=3600.0,
                    help="echo one heartbeat this often (liveness proof)")
    args = ap.parse_args()
    n = scan(sys.stdin, move=args.move, hb_every=args.hb_every)
    print(f"# monitor done — {n} signal(s)", flush=True)


if __name__ == "__main__":
    main()
