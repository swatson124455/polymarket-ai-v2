#!/usr/bin/env python3
"""Apply the remaining config fixes. Atomic write + timestamped backup.

KALSHI_PRECLOSE_FLATTEN=1
    The purpose-built defence against riding naked inventory into settlement: naked-only,
    within PRECLOSE_FLATTEN_MIN of MARKET CLOSE, capped at |naked| by _taker_cross_capped,
    maker-first with a STOP_ESCALATE_S grace, additive (cancels nothing). It does NOT read
    TAKER_FLATTEN, so this does not re-open the blanket launch-day crossing.
    Justification: naked contracts realize -$0.13645/ct vs -$0.02248/ct hedged
    (70 settlements / 4522.1 ct, API 2026-07-26T04:48:23Z) -- 7.2% of contracts carrying
    31.9% of settled loss.
    INERT while the STOP sentinel is present (run_once returns before the preclose block).

KALSHI_MAX_UNWIND_LOSS 0.02 -> 0.10
    The cap is measured against COST BASIS, so the further underwater a position, the
    further its exit sits from the market -- we close winners and strand losers. Two
    independent measurements put the right value near 0.10: the frozen study's break-even
    ($0.0999/ct) and the realized cost of NOT exiting ($0.13645/ct). 0.10 is also the
    code's own default; 0.02 appears in all 27 live.env.bak-* files and was never
    supported by a measurement.
    Checked against the CURRENT book (API 2026-07-26T15:27:04Z): only KXAAAGASW-26JUL27
    -4.080 becomes fillable, realizing -$0.80 versus -$1.00 at settlement. The other three
    stay parked (cap 0.32/0.55/0.32 against a 0.99 bid).

KALSHI_CAPTURE_GATE / STANDDOWN / NETEV_GATE = 0 (explicit)
    Unchanged in VALUE -- these were already 0 by code default. Written explicitly so the
    new startup audit records them as a CHOICE rather than an omission, and stops warning.
    They rank on models this session could not validate; leaving them off is deliberate.
"""
import os, shutil, datetime

P = "/opt/pa2-maker-kalshi-live/live.env"
CHANGES = {
    "KALSHI_PRECLOSE_FLATTEN": "1",
    "KALSHI_MAX_UNWIND_LOSS": "0.10",
    "KALSHI_CAPTURE_GATE": "0",
    "KALSHI_STANDDOWN": "0",
    "KALSHI_NETEV_GATE": "0",
}

txt = open(P).read()
lines = txt.splitlines()
stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
bak = f"{P}.bak-allfixes-{stamp}"
shutil.copy2(P, bak)
print("BACKUP", bak)

seen = set()
out = []
for ln in lines:
    s = ln.strip()
    if s and not s.startswith("#") and "=" in s:
        k = s.split("=", 1)[0].strip()
        if k in CHANGES:
            old = s.split("=", 1)[1]
            out.append(f"{k}={CHANGES[k]}")
            seen.add(k)
            print(f"  CHANGED {k}: {old} -> {CHANGES[k]}")
            continue
    out.append(ln)
for k, v in CHANGES.items():
    if k not in seen:
        out.append(f"{k}={v}")
        print(f"  ADDED   {k}={v}")

tmp = P + ".tmp"
with open(tmp, "w") as fh:
    fh.write("\n".join(out) + "\n")
shutil.copymode(P, tmp)
os.replace(tmp, P)

after = dict(l.split("=", 1) for l in open(P).read().splitlines()
             if l.strip() and not l.startswith("#") and "=" in l)
print("\nVERIFY:")
for k, v in CHANGES.items():
    got = after.get(k)
    print(f"  {k:<28} = {got}  {'OK' if got == v else '*** MISMATCH ***'}")
print("  KALSHI_MAX_TOTAL_CAPITAL     =", after.get("KALSHI_MAX_TOTAL_CAPITAL"), "(park state, unchanged)")
print("  KALSHI_TAKER_FLATTEN         =", after.get("KALSHI_TAKER_FLATTEN"), "(unchanged)")
print("  KALSHI_THROTTLE_SMART        =", after.get("KALSHI_THROTTLE_SMART"), "(unchanged)")
print("  total keys", len(after))
