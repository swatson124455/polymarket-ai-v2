"""EsportsBot 48h trade analysis charts — S120."""
import json
from collections import defaultdict

# Raw ENTRY data from trade_events (48h, parsed from VPS query)
RAW = [
    {"g": "lol", "s": "NO", "p": 0.334, "sz": 491, "c": 0.460, "ts": "03-21 17:12"},
    {"g": "cs2", "s": "NO", "p": 0.504, "sz": 135, "c": 0.831, "ts": "03-21 17:13"},
    {"g": "cs2", "s": "YES", "p": 0.497, "sz": 157, "c": 0.721, "ts": "03-21 17:44"},
    {"g": "cs2", "s": "NO", "p": 0.717, "sz": 131, "c": 0.831, "ts": "03-21 17:49"},
    {"g": "cs2", "s": "YES", "p": 0.407, "sz": 248, "c": 0.457, "ts": "03-21 18:17"},
    {"g": "cs2", "s": "YES", "p": 0.809, "sz": 95, "c": 0.865, "ts": "03-21 18:19"},
    {"g": "valorant", "s": "YES", "p": 0.095, "sz": 161, "c": 0.500, "ts": "03-21 18:19"},
    {"g": "lol", "s": "NO", "p": 0.079, "sz": 1816, "c": 0.512, "ts": "03-21 18:28"},
    {"g": "lol", "s": "YES", "p": 0.195, "sz": 459, "c": 0.686, "ts": "03-21 18:28"},
    {"g": "lol", "s": "YES", "p": 0.199, "sz": 404, "c": 0.686, "ts": "03-21 18:38"},
    {"g": "cs2", "s": "YES", "p": 0.153, "sz": 991, "c": 0.648, "ts": "03-21 18:44"},
    {"g": "cs2", "s": "YES", "p": 0.281, "sz": 451, "c": 0.690, "ts": "03-21 18:49"},
    {"g": "lol", "s": "YES", "p": 0.086, "sz": 2171, "c": 0.686, "ts": "03-21 18:59"},
    {"g": "cod", "s": "YES", "p": 0.550, "sz": 217, "c": 0.650, "ts": "03-21 19:23"},
    {"g": "lol", "s": "NO", "p": 0.061, "sz": 2847, "c": 0.628, "ts": "03-21 19:25"},
    {"g": "lol", "s": "YES", "p": 0.300, "sz": 842, "c": 0.608, "ts": "03-21 19:34"},
    {"g": "cod", "s": "YES", "p": 0.626, "sz": 471, "c": 0.650, "ts": "03-21 19:43"},
    {"g": "cod", "s": "YES", "p": 0.002, "sz": 527, "c": 0.650, "ts": "03-21 20:25"},
    {"g": "cs2", "s": "NO", "p": 0.780, "sz": 312, "c": 0.881, "ts": "03-21 20:49"},
    {"g": "cs2", "s": "YES", "p": 0.148, "sz": 1778, "c": 0.528, "ts": "03-21 20:49"},
    {"g": "cs2", "s": "NO", "p": 0.510, "sz": 594, "c": 0.881, "ts": "03-21 21:31"},
    {"g": "cs2", "s": "NO", "p": 0.460, "sz": 741, "c": 0.696, "ts": "03-21 22:51"},
    {"g": "cod", "s": "NO", "p": 0.390, "sz": 789, "c": 0.678, "ts": "03-21 23:07"},
    {"g": "valorant", "s": "YES", "p": 0.474, "sz": 527, "c": 0.613, "ts": "03-21 23:07"},
    {"g": "cs2", "s": "YES", "p": 0.861, "sz": 284, "c": 0.926, "ts": "03-21 23:07"},
    # Mar 22
    {"g": "cs2", "s": "NO", "p": 0.303, "sz": 873, "c": 0.534, "ts": "03-22 15:40"},
    {"g": "cs2", "s": "YES", "p": 0.548, "sz": 594, "c": 0.758, "ts": "03-22 15:40"},
    {"g": "dota2", "s": "NO", "p": 0.574, "sz": 676, "c": 0.593, "ts": "03-22 15:42"},
    {"g": "valorant", "s": "YES", "p": 0.377, "sz": 658, "c": 0.636, "ts": "03-22 16:43"},
    {"g": "valorant", "s": "YES", "p": 0.660, "sz": 453, "c": 0.676, "ts": "03-22 16:43"},
    {"g": "cs2", "s": "NO", "p": 0.510, "sz": 594, "c": 0.815, "ts": "03-22 16:43"},
    {"g": "cs2", "s": "YES", "p": 0.509, "sz": 606, "c": 0.769, "ts": "03-22 16:43"},
    {"g": "cs2", "s": "YES", "p": 0.724, "sz": 387, "c": 0.729, "ts": "03-22 16:43"},
    {"g": "cs2", "s": "NO", "p": 0.667, "sz": 472, "c": 0.815, "ts": "03-22 16:43"},
    {"g": "cs2", "s": "NO", "p": 0.275, "sz": 1395, "c": 0.788, "ts": "03-22 16:43"},
    {"g": "cs2", "s": "NO", "p": 0.670, "sz": 451, "c": 0.849, "ts": "03-22 16:43"},
    {"g": "cs2", "s": "YES", "p": 0.468, "sz": 600, "c": 0.838, "ts": "03-22 16:43"},
    {"g": "cs2", "s": "YES", "p": 0.640, "sz": 472, "c": 0.791, "ts": "03-22 16:43"},
    {"g": "cs2", "s": "YES", "p": 0.620, "sz": 390, "c": 0.709, "ts": "03-22 16:43"},
    {"g": "dota2", "s": "NO", "p": 0.258, "sz": 1091, "c": 0.615, "ts": "03-22 17:06"},
    {"g": "cs2", "s": "NO", "p": 0.675, "sz": 429, "c": 0.905, "ts": "03-22 17:07"},
    {"g": "cod", "s": "NO", "p": 0.699, "sz": 353, "c": 0.754, "ts": "03-22 18:33"},
    {"g": "cs2", "s": "YES", "p": 0.668, "sz": 488, "c": 0.780, "ts": "03-22 18:33"},
    {"g": "cs2", "s": "NO", "p": 0.507, "sz": 606, "c": 0.746, "ts": "03-22 18:33"},
    {"g": "cs2", "s": "YES", "p": 0.510, "sz": 594, "c": 0.762, "ts": "03-22 18:33"},
    {"g": "cs2", "s": "YES", "p": 0.620, "sz": 304, "c": 0.767, "ts": "03-22 18:33"},
    {"g": "cod", "s": "NO", "p": 0.570, "sz": 556, "c": 0.754, "ts": "03-22 18:33"},
    {"g": "cs2", "s": "NO", "p": 0.567, "sz": 594, "c": 0.752, "ts": "03-22 18:33"},
    # Mar 23 (post-S120 deploy)
    {"g": "lol", "s": "NO", "p": 0.110, "sz": 2087, "c": 0.495, "ts": "03-23 16:53"},
    {"g": "cs2", "s": "NO", "p": 0.720, "sz": 348, "c": 0.803, "ts": "03-23 17:08"},
]

total = len(RAW)

# ═══ CHART 1: Trades by Game ═══
games = defaultdict(lambda: {"count": 0, "vol": 0.0})
for e in RAW:
    games[e["g"]]["count"] += 1
    games[e["g"]]["vol"] += e["p"] * e["sz"]

print("=" * 62)
print("  CHART 1: ENTRIES BY GAME (48h)")
print("=" * 62)
for g in sorted(games, key=lambda x: games[x]["count"], reverse=True):
    v = games[g]
    pct = v["count"] / total * 100
    bar = "\u2588" * int(pct / 2)
    print(f"  {g:10s} {v['count']:3d} ({pct:4.1f}%)  ${v['vol']:>8,.0f}  {bar}")
print(f"  {'TOTAL':10s} {total:3d}          ${sum(v['vol'] for v in games.values()):>8,.0f}")

# ═══ CHART 2: Entry Price Distribution ═══
print()
print("=" * 62)
print("  CHART 2: ENTRY PRICE DISTRIBUTION")
print("=" * 62)
pbuckets = [("0-10c", 0), ("10-20c", 0), ("20-30c", 0), ("30-40c", 0),
            ("40-50c", 0), ("50-60c", 0), ("60-70c", 0), ("70-80c", 0), ("80-90c", 0)]
for e in RAW:
    idx = min(int(e["p"] * 10), 8)
    name, cnt = pbuckets[idx]
    pbuckets[idx] = (name, cnt + 1)
for name, cnt in pbuckets:
    bar = "\u2588" * cnt
    print(f"  {name:8s} {cnt:3d}  {bar}")

# ═══ CHART 3: YES vs NO ═══
print()
print("=" * 62)
print("  CHART 3: YES vs NO ENTRIES")
print("=" * 62)
sides = defaultdict(lambda: {"count": 0, "vol": 0.0})
for e in RAW:
    sides[e["s"]]["count"] += 1
    sides[e["s"]]["vol"] += e["p"] * e["sz"]
for s in ["YES", "NO"]:
    v = sides[s]
    pct = v["count"] / total * 100
    bar = "\u2588" * int(pct)
    print(f"  {s:4s}  {v['count']:3d} ({pct:4.1f}%)  ${v['vol']:>8,.0f}  {bar}")

print()
print("  By Game:")
gs = defaultdict(lambda: defaultdict(int))
for e in RAW:
    gs[e["g"]][e["s"]] += 1
for g in sorted(gs, key=lambda x: sum(gs[x].values()), reverse=True):
    y, n = gs[g].get("YES", 0), gs[g].get("NO", 0)
    t = y + n
    ybar = "\u2588" * y
    nbar = "\u2591" * n
    print(f"  {g:10s}  YES:{y:2d} NO:{n:2d}  {ybar}{nbar}")

# ═══ CHART 4: Confidence Distribution ═══
print()
print("=" * 62)
print("  CHART 4: MODEL CONFIDENCE AT ENTRY")
print("=" * 62)
cbuckets = [("45-50%", 0), ("50-55%", 0), ("55-60%", 0), ("60-65%", 0),
            ("65-70%", 0), ("70-75%", 0), ("75-80%", 0), ("80-85%", 0),
            ("85-90%", 0), ("90%+", 0)]
for e in RAW:
    c = e["c"]
    idx = min(max(int((c - 0.45) / 0.05), 0), 9)
    name, cnt = cbuckets[idx]
    cbuckets[idx] = (name, cnt + 1)
for name, cnt in cbuckets:
    bar = "\u2588" * cnt
    print(f"  {name:7s} {cnt:3d}  {bar}")

print()
print("  Avg Confidence by Game:")
gc = defaultdict(list)
for e in RAW:
    gc[e["g"]].append(e["c"])
for g in sorted(gc, key=lambda x: sum(gc[x])/len(gc[x]), reverse=True):
    avg = sum(gc[g]) / len(gc[g])
    bar = "\u2588" * int(avg * 30)
    print(f"  {g:10s}  {avg:.3f}  {bar}")

# ═══ CHART 5: Calibration Status ═══
print()
print("=" * 62)
print("  CHART 5: CALIBRATION STATUS (BetaCalibrator)")
print("=" * 62)
cal = [("CS2", 3, 15), ("Valorant", 0, 15), ("Dota2", 0, 15),
       ("LoL", 0, 15), ("CoD", 0, 15), ("R6", 0, 15), ("SC2", 0, 15), ("RL", 0, 15)]
for game, resolved, needed in cal:
    filled = int(resolved / needed * 20)
    empty = 20 - filled
    bar = "\u2588" * filled + "\u2591" * empty
    pct = resolved / needed * 100
    print(f"  {game:10s}  [{bar}] {resolved:2d}/{needed} ({pct:.0f}%)")
print()
print("  ETA first fit: ~24-48h as matches resolve")
print("  Predictions accumulating since S118 (2026-03-22 20:08 UTC)")
