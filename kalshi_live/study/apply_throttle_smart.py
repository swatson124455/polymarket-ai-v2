#!/usr/bin/env python3
"""Enable KALSHI_THROTTLE_SMART=1 in live.env.

WHY (measured, frozen dataset quotes_frozen.jsonl md5 7d7023857c07cdb1b14bd1aab3cc73c5,
2026-07-26T00:59:58Z..04:35:01Z, 4159 snapshots / 408 markets / 77 series):

  The throttle steps the accumulating quote back THROTTLE_STEP_TICKS=1 tick when
  inventory exceeds INV_SOFT_CT. Under the R4 qualifying walk, a bid one tick back
  earns credit ONLY if it is still inside the qualifying set. In 1817 of 3560
  qualifying YES snapshots (51.0%) the depth AT the reference alone already meets
  Target Size, so the walk terminates at the reference and a quote one tick back
  scores EXACTLY ZERO -- the throttle pays full reward for its risk reduction.

  THROTTLE_SMART detects precisely that case (depth_at_best >= target) and keeps the
  quote AT reference, taking the risk reduction from SIZE instead. Built, and pinned by
  test_throttle_skips_step_when_it_would_zero_credit (which also asserts the risk brake
  survives: throttled size < un-throttled join). It has been OFF in production because
  the key was never present in live.env -- the flag reads
  os.environ.get("KALSHI_THROTTLE_SMART") == "1".

  The prior sandbox A/B measured this at 12% of n=612 snapshots on the weather/temp
  allowlist. 51.0% is the same statistic re-measured on the CURRENT allowlist-open
  universe. Different denominators, both stated; the mechanism is identical.

SAFETY: the bot is PARKED (MAX_TOTAL_CAPITAL=1), so the throttle branch -- which needs an
accumulating quote and inventory over INV_SOFT_CT -- cannot fire today. This change is
INERT until the operator un-parks, and correct thereafter. Fully reversible.

Atomic write (tmp + os.replace) so a cycle reading live.env mid-write cannot see a
partial file. Timestamped backup per the existing live.env.bak-* convention.
"""
import os, shutil, datetime

P = "/opt/pa2-maker-kalshi-live/live.env"
KEY = "KALSHI_THROTTLE_SMART"
VAL = "1"

txt = open(P).read()
if any(l.strip().startswith(KEY + "=") for l in txt.splitlines()):
    print("ALREADY PRESENT — no change made")
    for l in txt.splitlines():
        if l.strip().startswith(KEY):
            print("  ", l)
    raise SystemExit(0)

stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
bak = f"{P}.bak-throttlesmart-{stamp}"
shutil.copy2(P, bak)
print("BACKUP", bak)

new = txt if txt.endswith("\n") else txt + "\n"
new += f"{KEY}={VAL}\n"
tmp = P + ".tmp"
with open(tmp, "w") as fh:
    fh.write(new)
shutil.copymode(P, tmp)
os.replace(tmp, P)
print("WROTE", f"{KEY}={VAL}")

after = open(P).read().splitlines()
print("VERIFY:", [l for l in after if l.startswith(KEY)])
print("LINE_COUNT", len(after), "(was", len(txt.splitlines()), ")")
