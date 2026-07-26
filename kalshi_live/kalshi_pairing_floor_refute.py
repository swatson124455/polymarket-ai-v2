#!/usr/bin/env python3
"""ADVERSARIAL REFUTATION — does ladder_pairing declare a FLOORED pair that has NO floor
on a series the survivor proposal labelled SAFE-ABSTAIN?

The proposal's structure column is derived from `events_code_abstains`, i.e. from
_is_ladder_event / event_deltas. But ladder_pairing NEVER calls _is_ladder_event — it
pairs on per-ticker _strike_of parseability alone. Those are two different code paths and
a series can abstain in one while pairing in the other.

For each candidate series this script:
  1. pulls the real live ticker set + yes_sub_title + strike_type from the PUBLIC API,
  2. runs the LIVE ladder_pairing on a +low / -high probe,
  3. prints the settlement payoff table implied by the ACTUAL contract semantics
     (parsed from strike_type + yes_sub_title), and flags any outcome region paying $0.

Read-only: public API, no keys, no orders.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.parse
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from maker_kalshi_quoter import _strike_of, _is_ladder_event, ladder_pairing  # noqa: E402

PROD_BASE = "https://external-api.kalshi.com"
_last = [0.0]


def get(path, params=None):
    if params:
        path += "?" + urllib.parse.urlencode(params)
    w = 0.4 - (time.time() - _last[0])
    if w > 0:
        time.sleep(w)
    req = urllib.request.Request(PROD_BASE + path,
                                 headers={"User-Agent": "kalshi-refute-probe/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        _last[0] = time.time()
        return json.loads(r.read())


def pays_yes(st, strike, outcome):
    """Does the YES leg of a contract with this strike_type pay at `outcome`?
    Only the polarities we need: greater / greater_or_equal / less / less_or_equal."""
    if st in ("greater",):
        return outcome > strike
    if st in ("greater_or_equal",):
        return outcome >= strike
    if st in ("less",):
        return outcome < strike
    if st in ("less_or_equal",):
        return outcome <= strike
    return None                     # between/custom/structured -> not modellable here


def analyse(series, event_filter=None):
    ms = get("/trade-api/v2/markets", {"series_ticker": series, "limit": 1000}).get("markets") or []
    by_ev = defaultdict(list)
    for m in ms:
        by_ev[m.get("event_ticker")].append(m)
    out = []
    for ev, mm in by_ev.items():
        if event_filter and ev != event_filter:
            continue
        if not any(m.get("status") == "active" for m in mm):
            continue
        info = {m["ticker"]: (m.get("strike_type"), m.get("yes_sub_title"),
                              m.get("status")) for m in mm}
        tks = list(info)
        parsed = sorted([(_strike_of(t), t) for t in tks if _strike_of(t) is not None])
        if len(parsed) < 2:
            continue
        lo_s, lo = parsed[0]
        hi_s, hi = parsed[-1]
        naked = ladder_pairing({lo: 20.0, hi: -20.0})
        fully_paired = abs(naked.get(lo, 0)) < 1e-9 and abs(naked.get(hi, 0)) < 1e-9
        row = {
            "event": ev,
            "code_nets(_is_ladder_event)": _is_ladder_event(tks),
            "ladder_pairing_declares_paired": fully_paired,
            "long_yes_leg": {"ticker": lo, "strike": lo_s,
                             "strike_type": info[lo][0], "title": info[lo][1]},
            "long_no_leg": {"ticker": hi, "strike": hi_s,
                            "strike_type": info[hi][0], "title": info[hi][1]},
        }
        # settlement table
        lt, ht = info[lo][0], info[hi][0]
        probes = [lo_s - 1, (lo_s + hi_s) / 2.0, hi_s + 1]
        labels = [f"outcome < {lo_s}", f"{lo_s} < outcome < {hi_s}", f"outcome > {hi_s}"]
        table = []
        zero_region = None
        for lbl, x in zip(labels, probes):
            y = pays_yes(lt, lo_s, x)          # we are LONG YES on lo
            n = pays_yes(ht, hi_s, x)          # we are LONG NO on hi -> pays when YES does NOT
            if y is None or n is None:
                table.append({"region": lbl, "payout": "UNMODELLABLE",
                              "lo_type": lt, "hi_type": ht})
                continue
            pay = (1.0 if y else 0.0) + (0.0 if n else 1.0)
            table.append({"region": lbl, "payout_per_pair_usd": pay})
            if pay == 0.0:
                zero_region = lbl
        row["settlement_table"] = table
        row["ZERO_PAYOUT_REGION"] = zero_region
        row["FLOOR_CLAIM_FALSE"] = bool(fully_paired and zero_region)
        out.append(row)
    return out


CANDS = ["KXTRUTHSOCIAL", "KXAPRPOTUS", "KXAAAGASD", "KXNETFLIXTOPVIEWSMOVIE",
         "KXEOWEEK", "KXAMSAVO", "KXB200MON", "KXMUSKNW", "KXNHSALES",
         "KXRTX5090MON", "KXTRUMPACT", "KXNETFLIXTOPVIEWSTV"]

if __name__ == "__main__":
    res = {}
    for s in CANDS:
        try:
            res[s] = analyse(s)
        except Exception as e:
            res[s] = [{"error": repr(e)}]
        for r in res[s]:
            if "error" in r:
                print(f"{s}: {r['error']}")
                continue
            print(f"{s:24s} {r['event']:28s} nets={r['code_nets(_is_ladder_event)']!s:5s} "
                  f"PAIRED={r['ladder_pairing_declares_paired']!s:5s} "
                  f"FLOOR_FALSE={r['FLOOR_CLAIM_FALSE']}")
            print(f"    +YES {r['long_yes_leg']['ticker']} ({r['long_yes_leg']['strike_type']}, "
                  f"'{r['long_yes_leg']['title']}')")
            print(f"    -NO  {r['long_no_leg']['ticker']} ({r['long_no_leg']['strike_type']}, "
                  f"'{r['long_no_leg']['title']}')")
            for t in r["settlement_table"]:
                print(f"      {t['region']:34s} -> {t.get('payout_per_pair_usd', t.get('payout'))}")
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "pairing_floor_refute.json"), "w") as fh:
        json.dump(res, fh, indent=1)
