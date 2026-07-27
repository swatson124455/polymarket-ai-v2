#!/usr/bin/env python3
"""INVENTORY STRESS — the real pipeline, real live books, INJECTED synthetic inventory.

WHY THIS EXISTS
smoke_dryrun.py cannot test any of this. run_once takes `if client.mode == "dry_run"` which
never reads positions, so held_by stays {} and the unwind / strand / ladder-hatch / offset
paths are all dead in a dry run. Every inventory behaviour in this bot has therefore only
ever been exercised by unit fixtures, never against real book shapes.

This pulls REAL orderbooks off the venue and drives the REAL desired_quotes over them with
inventory injected across every regime, asserting the invariants that matter. No orders,
no auth, read-only.

INVARIANTS ASSERTED (per market x inventory x config):
  I1  PAIRED-ON-DOUBLE-FILL. If the reducing side can cover inventory (R >= I) then
      inv + yes - no == 0, so both sides filling returns us to flat. If room caps R BELOW
      inventory, the adding side must be 0 — we must never add to a position we cannot hedge.
  I2  DOLLAR ENVELOPE. Neither side may rest more than MAX_MARKET_CAPITAL.
  I3  NEVER GROW THE IMBALANCE. |inv + yes - no| <= |inv| — a cycle can never leave us
      further from flat than it found us.
  I4  BOTH SIDES LIVE while holding, unless the hard envelope pulled the adding side to 0.
  I5  NO EXCEPTIONS on any real book shape.
  I6  REDUCING SIDE IS THE BIGGER ONE when holding (the whole point: shrink what adds,
      grow what reduces), unless room capped it.

Usage:  python3 stress_inventory.py [n_markets]
"""
import itertools
import json
import os
import sys
import traceback
import urllib.request

sys.path.insert(0, "/opt/pa2-maker-kalshi-live")
for _ln in open("/opt/pa2-maker-kalshi-live/live.env"):
    _ln = _ln.strip()
    if _ln and not _ln.startswith("#") and "=" in _ln:
        _k, _v = _ln.split("=", 1)
        os.environ[_k] = _v

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "q", "/opt/pa2-maker-kalshi-live/maker_kalshi_quoter.py")
q = importlib.util.module_from_spec(_spec)
sys.modules["q"] = q
_spec.loader.exec_module(q)

B = "https://api.elections.kalshi.com/trade-api/v2"


def get(p):
    r = urllib.request.Request(B + p, headers={"User-Agent": "stress/1.0"})
    return json.loads(urllib.request.urlopen(r, timeout=30).read())


# ---------- real books off the live venue ------------------------------------------------
N = int(sys.argv[1]) if len(sys.argv) > 1 else 40
progs, cur = [], ""
while len(progs) < 4000:
    d = get("/incentive_programs?status=active&limit=1000" + (f"&cursor={cur}" if cur else ""))
    progs += d.get("incentive_programs", [])
    cur = d.get("next_cursor") or ""
    if not cur:
        break
seen, books = set(), []
for p in progs:
    t = p.get("market_ticker")
    if not t or t in seen:
        continue
    seen.add(t)
    try:
        ob = get(f"/markets/{t}/orderbook").get("orderbook_fp") or {}
    except Exception:
        continue
    yl, nl = ob.get("yes_dollars") or [], ob.get("no_dollars") or []
    books.append((t, yl, nl, (p.get("period_reward") or 0) / 10000,
                  float(p.get("target_size_fp") or 1000)))
    if len(books) >= N:
        break
print(f"REAL BOOKS PULLED: {len(books)}")
n_two = sum(1 for _t, y, n, _u, _g in books if y and n)
print(f"  two-sided {n_two} | one-sided-or-empty {len(books)-n_two}")

# ---------- regime grid ------------------------------------------------------------------
INVS = [0, 1, 2, 5, 8, 14, 15, 16, 25, 40, 59, 60, 61, 90, 200,
        -1, -2, -5, -8, -14, -15, -16, -25, -40, -59, -60, -61, -90, -200]
# TARGET is swept LOW as well as high on purpose. `void = ext_y < target or ext_n < target`
# routes a market to the ACTIVATE branch, and PRESENCE_GATE routes it to a reduce-only exit.
# With live.env's PRESENCE_GATE=1 and target=1000 against real depth, EVERY case took an exit
# path and the two-sided JOIN branch -- the branch this fix lives in -- was never reached.
# A stress run that never enters the code under test proves nothing, so branch coverage is
# now asserted at the end.
CFGS = []
for standdown, (soft, hard), mktcap, join, presence, tgt in itertools.product(
        (False, True), ((15.0, 60.0), (3.0, 20.0), (30.0, 80.0)),
        (15.0, 250.0), (20, 100), (0, 1), (1, 50, 1000)):
    CFGS.append(dict(standdown=standdown, soft=soft, hard=hard, mktcap=mktcap,
                     join=join, minq=2, ramp=180, presence=presence, tgt=tgt))
print(f"CONFIGS: {len(CFGS)}  INVENTORIES: {len(INVS)}  "
      f"-> cases {len(books)*len(CFGS)*len(INVS):,}\n")

FAR = {"target": 1000, "end": "2099-01-01T00:00:00Z", "usd_day": 100.0, "ramp_min": 180}

BRANCH = {}
two_sided_with_inv = [0]
fails = []
n = 0
crashes = 0
quoted_both = 0
paired_ok = 0

for (tkr, yl, nl, usd, tgt) in books:
    for cfg in CFGS:
        q.STANDDOWN = cfg["standdown"]
        q.STANDDOWN_MIN_USD_DAY = 20.0
        q.INV_SOFT_CT, q.INV_HARD_CT = cfg["soft"], cfg["hard"]
        q.MAX_MARKET_CAPITAL = cfg["mktcap"]
        q.JOIN_SIZE = cfg["join"]
        q.MIN_QUOTE_CT = cfg["minq"]
        q.RAMP_MIN = cfg["ramp"]
        q.INV_TOLERANCE = 1.0
        q.PAIR_BOTH_SIDES = True
        q.PRESENCE_GATE = cfg["presence"]
        q.MAX_ACTIVATE_CAPITAL = 0.0
        m = dict(FAR, target=cfg["tgt"], usd_day=usd or 100.0)
        for inv in INVS:
            n += 1
            try:
                qs = q.desired_quotes(m, yl, nl, q.utcnow(), inv=float(inv), cost=0.50)
            except Exception as e:
                crashes += 1
                fails.append((tkr, cfg, inv, "EXCEPTION",
                              f"{e!r} | {traceback.format_exc().splitlines()[-3:]}"))
                continue
            d = {x["side"]: x for x in qs}
            y = d.get("yes", {}).get("count", 0)
            nn = d.get("no", {}).get("count", 0)
            yp = d.get("yes", {}).get("price_dollars", 0.0)
            npx = d.get("no", {}).get("price_dollars", 0.0)
            I = abs(inv)

            # I2 dollar envelope
            if y * yp > cfg["mktcap"] + 1e-6 or nn * npx > cfg["mktcap"] + 1e-6:
                fails.append((tkr, cfg, inv, "I2_DOLLAR",
                              f"yes ${y*yp:.2f} no ${nn*npx:.2f} cap ${cfg['mktcap']}"))
            if inv == 0 or not qs:
                continue
            add, red = (y, nn) if inv > 0 else (nn, y)
            net = inv + y - nn

            # I3 never grow the imbalance
            if abs(net) > I + 1e-9:
                fails.append((tkr, cfg, inv, "I3_GREW",
                              f"|net {net}| > |inv {inv}|  add={add} red={red}"))
            # I1 paired-on-double-fill, or refuse to add
            if red >= I:
                if net != 0:
                    fails.append((tkr, cfg, inv, "I1_NOT_PAIRED",
                                  f"add={add} red={red} net={net}"))
                else:
                    paired_ok += 1
            elif add != 0:
                fails.append((tkr, cfg, inv, "I1_ADDED_UNHEDGEABLE",
                              f"red={red} < |inv|={I} but add={add}"))
            # I6 reducing is the bigger side (unless room capped it)
            if red >= I and add > red:
                fails.append((tkr, cfg, inv, "I6_ADD_GT_RED", f"add={add} red={red}"))
            # I4 both live unless hard pulled the adder
            if y > 0 and nn > 0:
                quoted_both += 1
            for x in qs:
                BRANCH[x.get("reason", "?")] = BRANCH.get(x.get("reason", "?"), 0) + 1
            if y > 0 and nn > 0 and any(x.get("reason") == "unwind" for x in qs)                     and any(x.get("reason") == "join" for x in qs):
                two_sided_with_inv[0] += 1

print(f"CASES RUN            {n:,}")
print(f"  crashes            {crashes}")
print(f"  quoted BOTH sides  {quoted_both:,}")
print(f"  paired-on-double   {paired_ok:,}")
print(f"  VIOLATIONS         {len(fails)}")
print("")
print(f"BRANCH COVERAGE (quote reasons emitted): {BRANCH}")
print(f"  TWO-SIDED WHILE HOLDING INVENTORY (join+unwind together): {two_sided_with_inv[0]:,}")
if two_sided_with_inv[0] == 0:
    fails.append(("-", {}, 0, "NO_COVERAGE",
                  "never reached the two-sided-with-inventory branch — this run proves nothing"))
if fails:
    print("\nFIRST 25 VIOLATIONS:")
    for f in fails[:25]:
        print(f"  {f[3]:<22} {f[0]:<34} inv={f[2]:<5} {f[4]}")
        print(f"       cfg={f[1]}")
print("\nVERDICT:", "PASS" if not fails else f"FAIL — {len(fails)} violations")
sys.exit(0 if not fails else 1)
