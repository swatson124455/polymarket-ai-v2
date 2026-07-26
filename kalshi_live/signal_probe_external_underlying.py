#!/usr/bin/env python3
"""SIGNAL INVENTORY part 4 — is there a CHEAP PUBLIC source that tells us a TEMP
contract's outcome is already determined, BEFORE the contract stops trading?

READ-ONLY. Public NWS API (no key, no account). New file; edits nothing.
Deliberately self-contained: uses only api.weather.gov and the Kalshi public market
object. Touches no other project's code, data or configuration.

WHY THIS IS THE QUESTION
  Kalshi rules_primary for a temp contract (read live from the market object):
    "If the temperature recorded at Chicago, IL for Jul 22, 2026 12 PM EDT as reported
     by The Weather Company (for coordinates KORD), is above 69.99, then ... Yes."
  So the contract resolves to the reading at ONE CLOCK HOUR. The market's own
  close_time is that same hour; settlement posts at occurrence_datetime = hour + 5 min.
  Routine ASOS METARs for the same station are published a few minutes BEFORE the hour.
  If those observations are (a) public, (b) free, and (c) close to the settled value,
  then the outcome is knowable to any participant while we are still quoting — which is
  a description of exactly the flow that ran us over.

⚠ BASIS RISK, STATED UP FRONT: settlement is The Weather Company, NOT the NWS. NWS is a
PROXY for the settlement source, not the source. Any disagreement is real and unhedged.
This script MEASURES that disagreement rather than assuming it away.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
NWS = "https://api.weather.gov"
UA = {"User-Agent": "kalshi-maker-signal-inventory (read-only research)",
      "Accept": "application/geo+json"}

# contracts from our own loss set (kalshi_transactions_2026-07-23.csv, exit at 0.00)
TARGETS = [
    "KXTEMPCHIH-26JUL2212-T69.99",
    "KXTEMPLAXH-26JUL2212-T71.99",
    "KXTEMPCHIH-26JUL2123-T70.99",
    "KXTEMPCHIH-26JUL2207-T59.99",
    "KXTEMPCHIH-26JUL2211-T66.99",
    "KXTEMPDCH-26JUL2021-T79.99",
    "KXTEMPNYCH-26JUL2206-T69.99",
    "KXTEMPAUSH-26JUL2123-T84.99",
]
_last = [0.0]


def get(url, hdr=UA, spacing=0.4):
    dt = time.time() - _last[0]
    if dt < spacing:
        time.sleep(spacing - dt)
    _last[0] = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=hdr),
                                    timeout=25) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:200].decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return -1, str(e)[:200]


def station_of(rules):
    """Pull the 'for coordinates KXXX' station out of rules_primary."""
    if not rules or "coordinates" not in rules:
        return None
    tail = rules.split("coordinates", 1)[1].strip()
    tok = tail.split(")")[0].split(",")[0].strip()
    return tok if tok.startswith("K") and len(tok) == 4 else None


def c_to_f(c):
    return None if c is None else c * 9.0 / 5.0 + 32.0


def main():
    print("=" * 100)
    print("STEP 1 — read settlement rules + settled value from the PUBLIC Kalshi market")
    print("=" * 100)
    mkts = {}
    for tk in TARGETS:
        st, d = get(f"{KALSHI}/markets/{tk}", hdr={"User-Agent": "probe",
                                                   "Accept": "application/json"})
        if st != 200 or not isinstance(d, dict):
            print(f"  {st} {tk} -> {str(d)[:100]}")
            continue
        m = d["market"]
        stn = station_of(m.get("rules_primary"))
        mkts[tk] = (m, stn)
        print(f"  {tk:<30} station={stn}  strike={m.get('floor_strike')}  "
              f"settled={m.get('expiration_value')}  result={m.get('result')}")
        print(f"     close={m.get('close_time')}  occurrence={m.get('occurrence_datetime')}")

    print()
    print("=" * 100)
    print("STEP 2 — NWS observations for the same station around that hour.")
    print("   'lead' = minutes the observation was published BEFORE the market closed.")
    print("=" * 100)
    hdr = ("  %-30s %-6s %-7s %-9s %-22s %6s %8s %8s" %
           ("contract", "stn", "settled", "strike", "obs_time(UTC)", "lead", "obs_F",
            "err_F"))
    print(hdr)
    agree = dec = 0
    for tk, (m, stn) in mkts.items():
        if not stn:
            print(f"  {tk:<30} (no station parsed)")
            continue
        close = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00"))
        a = (close - timedelta(minutes=75)).strftime("%Y-%m-%dT%H:%M:%SZ")
        b = (close + timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
        st, d = get(f"{NWS}/stations/{stn}/observations?start={a}&end={b}")
        if st != 200 or not isinstance(d, dict):
            print(f"  {tk:<30} NWS {st} {str(d)[:80]}")
            continue
        obs = sorted(d.get("features", []),
                     key=lambda f: f["properties"]["timestamp"])
        settled = float(m["expiration_value"]) if m.get("expiration_value") else None
        strike = m.get("floor_strike")
        shown = 0
        for f in obs:
            p = f["properties"]
            t = datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00"))
            tf = c_to_f((p.get("temperature") or {}).get("value"))
            if tf is None:
                continue
            lead = (close - t).total_seconds() / 60.0
            if lead < -20 or lead > 75:
                continue
            err = (tf - settled) if settled is not None else None
            print("  %-30s %-6s %-7s %-9s %-22s %6.1f %8.1f %8s" %
                  (tk if shown == 0 else "", stn, settled, strike,
                   t.strftime("%Y-%m-%d %H:%M:%SZ"), lead, tf,
                   ("%.1f" % err) if err is not None else "-"))
            # last obs strictly BEFORE close: would it have called the outcome right?
            if 0 <= lead <= 15 and settled is not None and strike is not None:
                dec += 1
                pred_yes = tf > float(strike)
                act_yes = (m.get("result") == "yes")
                agree += 1 if pred_yes == act_yes else 0
            shown += 1
        if shown == 0:
            print(f"  {tk:<30} {stn}  (no usable NWS obs in window)")

    print()
    print("=" * 100)
    print("STEP 3 — VERDICT")
    print("=" * 100)
    print(f"  observations landing 0-15 min BEFORE close that could be scored: {dec}")
    if dec:
        print(f"  of those, the NWS reading called the YES/NO outcome correctly: "
              f"{agree}/{dec} = {agree/dec:.0%}")
    print("  [!] NOT COVERED: settlement is The Weather Company, not NWS. Agreement here")
    print("    is evidence the outcome is PUBLICLY KNOWABLE pre-close, not that NWS can")
    print("    be used as a settlement substitute. Sample is our own loss set only —")
    print("    selected on having lost, so it is NOT an unbiased estimate of accuracy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
