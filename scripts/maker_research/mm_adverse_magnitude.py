"""MAGNITUDE-OF-ADVERSE PASS — how big adverse selection is, not how often.

The all-sector toxicity table reports FREQUENCY (adverse>1pt/2pt rates); the
standing hypothesis was freq != magnitude (weather = frequent-small
defendable, geo = rare-huge jumps, esports = both). This pass measures the
SIZE distribution per sector on the same corrected extraction as the refit
(mm_refit_a.py section 2): v1 UNGATED control fills (the uncensored
toxicity surface — gated arms censor the hot zones), fill side inferred
from pos delta (flat round-trips excluded), outcome = signed 30-min mid
move; $ impact = signed move x |dpos| shares.

Read-only over /opt/pa2-maker-sim/samples-*.jsonl(.gz). Run ad hoc on VPS.
Method caveats inherited from the refit: sign attenuation on multi-fill
minutes (magnitudes are FLOORS), marks not resolutions.
"""
import bisect
import collections
import glob
import gzip
import json


def rows_of(pattern):
    for fp in glob.glob(pattern):
        op = gzip.open if fp.endswith(".gz") else open
        try:
            with op(fp, "rt") as f:
                for ln in f:
                    try:
                        yield json.loads(ln)
                    except Exception:
                        continue
        except OSError:
            continue


series = collections.defaultdict(list)
sec_of = {}
for r in rows_of("/opt/pa2-maker-sim/samples-*"):
    if r.get("mid") is None:
        continue
    series[r["id"]].append((r["t"], r["mid"], r.get("pos", 0), r.get("fills", 0)))
    if r.get("sec"):
        sec_of[r["id"]] = r["sec"]
for v in series.values():
    v.sort()

per_sec = collections.defaultdict(list)   # sector -> [(move, usd)]
skipped_flat = 0
for mkt, s in series.items():
    sec = sec_of.get(mkt, "?")
    for i in range(1, len(s)):
        t, mid, pos, fills = s[i]
        if not fills:
            continue
        dpos = pos - s[i - 1][2]
        if abs(dpos) < 1e-9:
            skipped_flat += 1
            continue
        side = 1 if dpos > 0 else -1
        j = bisect.bisect_left(s, (t + 1800, -1e18, 0, 0))
        best = None
        for k in (j - 1, j, j + 1):
            if 0 <= k < len(s) and abs(s[k][0] - (t + 1800)) <= 600:
                if best is None or abs(s[k][0] - (t + 1800)) < abs(best[0] - (t + 1800)):
                    best = s[k]
        if best is None:
            continue
        move = (best[1] - mid) * side
        per_sec[sec].append((move, move * abs(dpos)))

print("=== MAGNITUDE OF ADVERSE (v1 ungated fills, side-inferred, 30-min marks) ===")
print("flat round-trips excluded: %d" % skipped_flat)
hdr = ("sector", "n", "mean_pt", "adv%", ">2pt%", "advP50", "advP75",
       "advP90", "advMAX", "mean$", "adv_mean$", "advP90$")
print("%-14s %5s %8s %6s %6s %7s %7s %7s %7s %8s %9s %8s" % hdr)
rows = sorted(per_sec.items(), key=lambda kv: -len(kv[1]))
rows.append(("ALL", [x for v in per_sec.values() for x in v]))
for sec, xs in rows:
    if not xs:
        continue
    moves = sorted(m for m, _ in xs)
    usds = [u for _, u in xs]
    adv = sorted(-m for m, _ in xs if m < 0)          # adverse magnitudes, pts
    adv_usd = sorted(-u for m, u in xs if m < 0)
    n = len(moves)
    q = lambda arr, p: arr[min(int(p * len(arr)), len(arr) - 1)] if arr else 0.0
    print("%-14s %5d %+8.4f %5.1f%% %5.1f%% %7.3f %7.3f %7.3f %7.3f %+8.2f %9.2f %8.2f"
          % (sec, n, sum(moves) / n,
             100 * len(adv) / n,
             100 * sum(1 for m in moves if m < -0.02) / n,
             q(adv, 0.50), q(adv, 0.75), q(adv, 0.90),
             adv[-1] if adv else 0.0,
             sum(usds) / n,
             (sum(adv_usd) / len(adv_usd)) if adv_usd else 0.0,
             q(adv_usd, 0.90)))
print("\nreading: advP50..MAX are ADVERSE-ONLY magnitudes in points; mean$ is")
print("net per fill event (favorable fills offset); adv_mean$/advP90$ = the")
print("cost when it goes wrong. freq != magnitude — compare adv%% vs advP90.")
