#!/usr/bin/env python3
"""W17 — COVERAGE LEDGER + IDENTITY CENSUS (operator mindset ruling 2026-08-06:
"stop worrying about specific markets — find what caused them to be overlooked").

THE INVARIANT (feedback_class_not_instance): every dollar of active pool in the
receipt-proven universe is in exactly one bucket, daily —
  EARNING   quoted, size beyond probe scale
  LIMITED   quoted at probe/ramp scale, limiter NAMED (ramp/probe/exit-only/explore)
  EXCLUDED  a gate or selection stage said no, stage NAMED
  UNKNOWN   we cannot say why it isn't earning  <-- THE ALARM, always the headline
Plus the identity-layer standing detectors from the 2026-08-06 adversarial review:
  CENSUS    every SERIES_ALLOW/DENY entry must resolve to a live venue series; ALLOW
            entries with zero active programs = the automated rename/decay alarm (A-4);
            DENY prefixes expanded to the series they actually kill (A-3)
  LIVENESS  selected/probed markets whose close_time is PAST or UNKNOWN (B-1/B-2 class)
  FEED      credit_history parse-rate, len==limit truncation alarm, last-credit
            staleness for "paid" series (A-1/B-7/B-4 classes)

READ-ONLY. Never writes config or state. Output is decisions + alarms for the operator.
Run (VPS): sudo ./venv/bin/python w17_coverage_ledger.py [--cycles 5]
"""
import argparse
import collections
import datetime as dt
import glob
import json
import os
import sys
import time
import urllib.request

BASE = "https://api.elections.kalshi.com/trade-api/v2"
DATA = "/opt/pa2-maker-kalshi-live"
CREDIT_PREFIX = "Liquidity Incentive for event "
PROBE_SCALE_CT = 5.0


def _get(path):
    return json.load(urllib.request.urlopen(BASE + path, timeout=30))


def _env():
    """Config for this report — os.environ FIRST, live.env only as a fallback.

    The timer unit already carries `EnvironmentFile=/opt/pa2-maker-kalshi-live/live.env`, so
    systemd (root) reads the 0600 root-owned file and injects KALSHI_* into this process. The
    direct file read was therefore redundant AND fatal: the unit runs as `polymarket`, which
    cannot open a root-owned 0600 file, so EVERY timer run died with PermissionError before
    printing a single bucket (measured: w17 broken from 2026-08-06T14:00:01Z, w16 from at least
    2026-08-07T14:00Z). Fallback retained so a manual run by a user that CAN read the file still
    works. Fixed 2026-08-08."""
    env = {k: v for k, v in os.environ.items() if k.startswith("KALSHI_")}
    src = "environ"
    if not env:
        src = "live.env"
        try:
            with open(os.path.join(DATA, "live.env")) as fh:
                for line in fh:
                    if "=" in line and not line.startswith("#"):
                        k, _, v = line.strip().partition("=")
                        env[k] = v
        except OSError as exc:
            # STANDING DETECTOR: an empty allow/deny list silently makes every bucket read
            # "nothing to cover" — which is indistinguishable from a clean run. Say it out loud.
            src = f"NONE ({exc.__class__.__name__})"
            print(f"# ALARM config unreadable: no KALSHI_* in the environment and live.env "
                  f"could not be opened ({exc}) — allow/deny are EMPTY and every coverage "
                  f"bucket below is meaningless", flush=True)
    print(f"# config source: {src} ({len(env)} KALSHI_* keys)", flush=True)
    return env


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=5,
                    help="recent telemetry cycles to classify from")
    a = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    print(f"# W17 coverage ledger — {now.isoformat()}")
    env = _env()
    allow = [s for s in env.get("KALSHI_SERIES_ALLOW", "").split(",") if s.strip()]
    deny = [s for s in env.get("KALSHI_SERIES_DENY", "").split(",") if s.strip()]

    # ---- venue truth: active programs + the proven set ----
    progs, cursor = [], ""
    for _ in range(5):
        d = _get("/incentive_programs?status=active&limit=10000"
                 + (f"&cursor={cursor}" if cursor else ""))
        progs += d.get("incentive_programs", [])
        cursor = d.get("next_cursor") or ""
        if not cursor:
            break
    active = {}
    for p in progs:
        t = p.get("market_ticker") or ""
        if t and (p.get("incentive_type") or "liquidity") == "liquidity":
            active[t] = (p.get("period_reward") or 0) / 10000
    active_series = collections.defaultdict(float)
    for t, pool in active.items():
        active_series[t.split("-")[0]] += pool

    try:
        fb = json.load(open(os.path.join(DATA, "kalshi_credit_feedback.json")))
        proven = {s for s, r in (fb.get("series") or {}).items()
                  if (r.get("credits_n") or 0) > 0}
    except Exception:
        fb, proven = {}, set()
    trusted = set(allow) | proven

    # ---- telemetry: what the bot actually did, last N cycles ----
    qfiles = sorted(glob.glob(os.path.join(DATA, "quotes-*.jsonl")))[-2:]
    cycles = collections.OrderedDict()
    for qf in qfiles:
        for line in open(qf):
            try:
                r = json.loads(line)
            except Exception:
                continue
            cycles.setdefault(r["ts"], {})[r["ticker"]] = r
    recent = list(cycles.values())[-a.cycles:]
    seen = {}
    for cyc in recent:
        for t, r in cyc.items():
            seen[t] = r          # newest row wins

    pfiles = sorted(glob.glob(os.path.join(DATA, "plans-*.jsonl")))[-2:]
    last_plan = {}
    for pf in pfiles:
        for line in open(pf):
            try:
                last_plan = json.loads(line)
            except Exception:
                continue
    fam_dropped = set(last_plan.get("family_dropped_tickers") or [])
    probe_slots = set(last_plan.get("probe_slots") or [])

    # ---- THE LEDGER: bucket every trusted-series active-program market ----
    buckets = collections.defaultdict(list)
    for t, pool in sorted(active.items(), key=lambda kv: -kv[1]):
        s = t.split("-")[0]
        if s not in trusted:
            continue
        r = seen.get(t)
        if r is not None:
            ct = max(float(r.get("y_ct") or 0), float(r.get("n_ct") or 0))
            gates = r.get("gates") or {}
            if ct > PROBE_SCALE_CT:
                buckets["EARNING"].append((t, pool, f"{ct:.0f}ct"))
            elif ct > 0:
                lim = ("explore/probe" if "explore_probe_capped" in gates else
                       "ramp" if "d3_ramp_capped" in gates else "small-join")
                buckets["LIMITED"].append((t, pool, lim))
            elif gates:
                buckets["EXCLUDED"].append((t, pool, "+".join(sorted(gates))))
            else:
                buckets["UNKNOWN"].append((t, pool, "in footprint, no size, no gate"))
        elif t in fam_dropped:
            buckets["EXCLUDED"].append((t, pool, "family_budget"))
        else:
            # never reached the quote loop — attribute what we can, else UNKNOWN
            reason = None
            if any(t.startswith(p) for p in deny) and s not in allow:
                reason = "series_deny"
            else:
                try:
                    m = _get(f"/markets/{t}").get("market", {})
                    ct2 = m.get("close_time")
                    if ct2:
                        c = dt.datetime.fromisoformat(ct2.replace("Z", "+00:00"))
                        if c <= now:
                            reason = "CLOSE IN PAST (B-1 class!)"
                        elif c > now + dt.timedelta(days=8) and s not in proven:
                            reason = "far_close (unproven)"
                    time.sleep(0.2)
                except Exception:
                    pass
            buckets["EXCLUDED" if reason else "UNKNOWN"].append(
                (t, pool, reason or "never selected — NO ATTRIBUTED REASON"))

    print("\n## LEDGER (trusted-series active-program markets)")
    for b in ("UNKNOWN", "EXCLUDED", "LIMITED", "EARNING"):
        rows = buckets.get(b, [])
        tot = sum(p for _, p, _ in rows)
        print(f"### {b}: {len(rows)} markets, ${tot:.0f}/day pool"
              + ("   <<< HEADLINE" if b == "UNKNOWN" and rows else ""))
        for t, pool, why in rows[:15]:
            print(f"    {t:36s} ${pool:6.0f}/day  {why}")

    # ---- IDENTITY CENSUS (A-3/A-4/A-5) ----
    print("\n## IDENTITY CENSUS")
    for s in allow:
        n_act = sum(1 for t in active if t.split("-")[0] == s)
        ok = None
        if n_act == 0:
            try:
                ok = bool(_get(f"/series/{s}").get("series", {}).get("ticker"))
                time.sleep(0.2)
            except Exception:
                ok = False
        tag = ("OK" if n_act else
               "PROGRAMLESS (rename/decay watch — see W16)" if ok else
               "!! NOT A LIVE VENUE SERIES (typo or retired) !!")
        print(f"  ALLOW {s:26s} active_mkts={n_act:3d}  {tag}")
    for p in deny:
        hits = {t.split("-")[0] for t in active if t.startswith(p)}
        flag = ""
        if len(hits) > 1:
            flag = "  !! PREFIX KILLS MULTIPLE SERIES (A-3) !!"
        if hits & proven:
            flag += "  !! KILLS A RECEIPTS SERIES !!"
        print(f"  DENY  {p:26s} kills={sorted(hits) if hits else 'nothing-active'}{flag}")

    # ---- LIVENESS of resource holders (B-1/B-2) ----
    print("\n## LIVENESS (probe slots + footprint holders)")
    for t in sorted(probe_slots):
        try:
            m = _get(f"/markets/{t}").get("market", {})
            ct2 = m.get("close_time")
            c = dt.datetime.fromisoformat(ct2.replace("Z", "+00:00")) if ct2 else None
            if c is None:
                print(f"  probe {t}: close UNKNOWN  !! (B-2 class)")
            elif c <= now:
                print(f"  probe {t}: CLOSED {ct2}  !! (B-1 class)")
            elif c > now + dt.timedelta(days=8):
                print(f"  probe {t}: closes {ct2} — beyond horizon !! (warmup fail-open)")
            else:
                print(f"  probe {t}: ok (closes {ct2})")
            time.sleep(0.2)
        except Exception as e:
            print(f"  probe {t}: read failed ({e})")

    # ---- FEED INTEGRITY (A-1/B-7/B-4) ----
    print("\n## FEED INTEGRITY")
    try:
        sys.path.insert(0, DATA)
        from maker_kalshi_client import KalshiOrderClient
        c = KalshiOrderClient(mode="live")
        creds = c.get_credit_history().get("credits") or []
        matched = [x for x in creds if (x.get("reason") or "").startswith(CREDIT_PREFIX)]
        print(f"  credit_history: {len(creds)} rows, {len(matched)} parse-matched"
              + ("  !! PARSE RATE DEGRADED (A-1) !!"
                 if creds and len(matched) < len(creds) * 0.9 else ""))
        if len(creds) >= 1000:
            print("  !! credit_history len==limit — TRUNCATION LIKELY (B-7) !!")
        last_by_series = {}
        for x in matched:
            ev = (x.get("reason") or "")[len(CREDIT_PREFIX):].strip()
            s = ev.split("-")[0]
            ts = x.get("created_at") or ""
            if ts > last_by_series.get(s, ""):
                last_by_series[s] = ts
        stale_cut = (now - dt.timedelta(days=14)).isoformat()
        for s in sorted(proven):
            last = last_by_series.get(s, "never-in-window")
            stale = last < stale_cut
            noprog = s not in active_series
            if stale and noprog:
                print(f"  paid-but-STALE+PROGRAMLESS {s} last_credit={last[:10]} (B-4)")
    except Exception as e:
        print(f"  credit feed read failed: {e}")

    print("\n(read-only; alarms are operator decisions — nothing was changed)")


if __name__ == "__main__":
    main()
