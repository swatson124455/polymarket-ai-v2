#!/usr/bin/env python3
"""KALSHI PREFLIGHT — hard PASS/FAIL gate that must pass before ANY bot start.

Run ON the box, UNDER SUDO (it verifies it can actually read the live dir — a
permission-denied read is reported as UNREADABLE, never as "absent"; that exact
confusion hid a live STOP sentinel on 2026-09-01/02).

Checks (each prints PASS/FAIL/WARN; exit 0 only if zero FAILs):
  1  live dir readable (sudo present)
  2  no STOP sentinel
  3  live.env matches the operator-approved rails manifest (every key)
  3b config-change guard: drift since last clean start (or a first start with no
     baseline) is a hard FAIL unless acked (--ack-config-change / CONFIG_CHANGE_ACK)
  4  service not already active (pre-start expectation)
  5  venue account: fresh cash-feed row (<=6 min old), 0 resting (FAIL if not),
     positions printed (WARN if held), cash >= MAX_TOTAL_CAPITAL sanity
  6  DRY SELECTION PRINT — runs the real picker + quote logic offline against
     live books and prints exactly what would be quoted at what (ramp-clamped)
     sizes and estimated committed $. ZERO order risk: no order client is ever
     constructed; only public API GETs.

NEVER places, cancels, or modifies any order. Read-only everywhere.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

LIVE = "/opt/pa2-maker-kalshi-live"
MANIFEST = os.path.join(LIVE, "kalshi_rails_manifest.json")
# Ack can come as a flag (manual runs) or as this file (the systemd ExecStartPre gate
# cannot take per-invocation flags; kalshi_safe_start.sh materializes the flag into the
# file before start and removes it after the baseline snapshot).
ACK_FILE = os.path.join(LIVE, "CONFIG_CHANGE_ACK")
FAILS, WARNS = [], []


class _Args:
    ack_config_change = "--ack-config-change" in sys.argv
    # --pre-start: the systemd ExecStartPre gate mode — runs checks 1-5 (all hard gates)
    # and SKIPS the advisory dry-selection print (venue GETs, slow, and A5-different-instant
    # anyway). The full run remains the kalshi_safe_start.sh path.
    pre_start = "--pre-start" in sys.argv


ARGS = _Args()


def res(ok, label, detail=""):
    tag = "PASS" if ok else "FAIL"
    if not ok:
        FAILS.append(label)
    print(f"[{tag}] {label}" + (f" — {detail}" if detail else ""))


def warn(label, detail=""):
    WARNS.append(label)
    print(f"[WARN] {label}" + (f" — {detail}" if detail else ""))


def main():
    now = datetime.now(timezone.utc)
    print(f"KALSHI PREFLIGHT  {now.strftime('%Y-%m-%dT%H:%M:%SZ')}")

    # 1 — dir readable (the sudo-blindness guard)
    try:
        names = os.listdir(LIVE)
        res(True, "live dir readable", f"{len(names)} entries")
    except PermissionError:
        res(False, "live dir readable",
            "PERMISSION DENIED — run under sudo; absence claims from here would be blind")
        return finish()

    # 2 — STOP sentinel
    stop = os.path.join(LIVE, "STOP")
    if os.path.exists(stop):
        head = open(stop).read(160).strip()
        res(False, "no STOP sentinel", f"STOP present: {head!r} — operator clear required")
    else:
        res(True, "no STOP sentinel")

    # 3 — rails manifest vs live.env
    envmap = {}
    for line in open(os.path.join(LIVE, "live.env")):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            envmap[k] = v
    manifest = json.load(open(MANIFEST))["rails"]
    drift = []
    for k, spec in manifest.items():
        got = envmap.get(k)
        if got != spec["value"]:
            drift.append(f"{k}: env={got!r} approved={spec['value']!r} ({spec['why']})")
    if drift:
        res(False, f"rails manifest ({len(manifest)} keys)", "; ".join(drift))
    else:
        res(True, f"rails manifest ({len(manifest)} keys)", "all approved values in place")

    # 3b — CONFIG-CHANGE GUARD (safeguard 3): any rail changed since the last actual start
    # must run its first session at the ramp FLOOR + fill-watch. Blind-review hole A3
    # (2026-09-06): this used to WARN only, and the ack flag changed nothing but the text.
    # Now: drift (or a missing snapshot = first start) is a hard FAIL unless explicitly
    # acknowledged via --ack-config-change or the CONFIG_CHANGE_ACK file. The ack is an
    # operator statement "I know the config changed; first session runs ramp-floor +
    # fill-watch" — the ramp-floor itself is still NOT code-enforced (held item A3b).
    # Snapshot 'live.env.last_started' is written by kalshi_safe_start.sh AFTER a clean
    # gated start (blind-review hole N1: it must never be written by an ungated path).
    snap = os.path.join(LIVE, "live.env.last_started")
    ack = ARGS.ack_config_change or os.path.exists(ACK_FILE)
    if not os.path.exists(snap):
        if ack:
            warn("config-change guard", "no last-started snapshot (FIRST start) — "
                 "ACKNOWLEDGED: run this first session at ramp floor + fill-watch")
        else:
            res(False, "config-change guard",
                "no last-started snapshot — a FIRST start needs an explicit ack: "
                f"--ack-config-change (safe_start) or touch {ACK_FILE}")
    else:
        prev = {}
        for line in open(snap):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                prev[k] = v
        changed = [f"{k}: {prev.get(k)!r}->{envmap.get(k)!r}"
                   for k in set(prev) | set(envmap) if prev.get(k) != envmap.get(k)]
        if changed:
            if ack:
                warn("config-change guard",
                     f"{len(changed)} key(s) changed since last start — ACKNOWLEDGED "
                     "(first session = ramp floor + fill-watch): " + "; ".join(changed))
            else:
                res(False, "config-change guard",
                    f"{len(changed)} key(s) changed since last start, UNACKED: "
                    + "; ".join(changed)
                    + f" — ack via --ack-config-change or touch {ACK_FILE}")
        else:
            res(True, "config-change guard", "live.env unchanged since last clean start")

    # 4 — service state
    state = subprocess.run(["systemctl", "is-active", "polymarket-maker-kalshi-ws"],
                           capture_output=True, text=True).stdout.strip()
    if state == "active":
        warn("service already active", "preflight is meant to run before start")
    else:
        res(True, "service not yet active", state)

    # 5 — venue account via freshest cash-feed row
    import glob
    cash_files = sorted(glob.glob(os.path.join(LIVE, "cash-2026*.jsonl")))
    row, age_min = None, None
    if cash_files:
        last = None
        with open(cash_files[-1]) as fh:
            for line in fh:
                if line.strip():
                    last = line
        try:
            row = json.loads(last)
            age_min = (now - datetime.fromisoformat(row["ts"])).total_seconds() / 60.0
        except Exception:
            row = None
    if row is None or age_min is None or age_min > 6.0:
        res(False, "fresh venue account read",
            f"cash-feed row age={age_min if age_min is not None else 'n/a'} min (recorder down?)")
    else:
        res(True, "fresh venue account read",
            f"${row['cash']:.4f} cash | {row['n_resting']} resting | "
            f"{row['n_positions']} positions ({row['ts']})")
        if row["n_resting"]:
            res(False, "zero resting orders before start",
                f"{row['n_resting']} resting while service off — stale orders, investigate")
        else:
            res(True, "zero resting orders before start")
        if row["n_positions"]:
            warn("open positions at start", f"{row['n_positions']} held — operator judgment")
        cap = float(envmap.get("KALSHI_MAX_TOTAL_CAPITAL", "0"))
        if row["cash"] < cap:
            res(False, "cash covers MAX_TOTAL_CAPITAL", f"${row['cash']:.2f} < ${cap:.0f}")
        else:
            res(True, "cash covers MAX_TOTAL_CAPITAL",
                f"${row['cash']:.2f} vs cap ${cap:.0f} (buffer ${row['cash']-cap:.2f})")

    # 6 — dry selection print (no order client is EVER constructed).
    # NOTE (quoter module import): this imports maker_kalshi_quoter, whose module-level
    # code reads env/state files but constructs no client (blind-review N5: side effects
    # not exhaustively audited — kept out of the systemd gate path for that reason too).
    if ARGS.pre_start:
        print("--- pre-start mode: dry-selection print skipped (advisory only; "
              "run kalshi_preflight.py without --pre-start for it) ---")
        return finish()
    print("--- DRY SELECTION (ADVISORY — snapshot at the preflight instant; the real "
          "first cycle re-reads books and may differ [hole A5]; read-only) ---")
    try:
        sys.path.insert(0, LIVE)
        import maker_kalshi_quoter as q
        import urllib.request

        def api(p):
            with urllib.request.urlopen(
                    "https://api.elections.kalshi.com/trade-api/v2" + p, timeout=15) as r:
                return json.load(r)

        progs, cursor = [], ""
        for _ in range(6):
            page = api("/incentive_programs?status=active&limit=10000"
                       + (f"&cursor={cursor}" if cursor else ""))
            progs += page.get("incentive_programs", [])
            cursor = page.get("cursor") or page.get("next_cursor") or ""
            if not cursor:
                break
        picked = q.select_footprint(progs, now)
        print(f"programs={len(progs)} -> picked={len(picked)} "
              f"(drops: { {k: v for k, v in q.FP_DROPS.items() if v} })")

        try:
            st = json.load(open(os.path.join(LIVE, "quoter_state.json")))
            first_seen = st.get("d3_first_seen", {})
        except Exception:
            first_seen = {}
        try:
            fb = json.load(open(os.path.join(LIVE, "kalshi_credit_feedback.json")))
            fb = fb.get("series", fb)
        except Exception:
            fb = {}

        total = 0.0
        for m in picked[:8]:
            t = m["ticker"]
            try:
                ob = api(f"/markets/{t}/orderbook").get("orderbook", {})
            except Exception as e:
                print(f"  {t}: book read failed ({e})")
                continue
            yl = [[p / 100.0, s] for p, s in (ob.get("yes") or [])]
            nl = [[p / 100.0, s] for p, s in (ob.get("no") or [])]
            stats = {}
            quotes = q.desired_quotes(m, yl, nl, now, own={"yes": 0.0, "no": 0.0},
                                      inv=0.0, event_delta=0.0, stats=stats,
                                      cost=0.0, own_orders=None)
            ramp_ct = q._d3_ramp_ct(t, now.timestamp(), dict(first_seen), fb)
            out = []
            for x in quotes:
                ct = min(int(x["count"]), ramp_ct) if x.get("reason") != "unwind" else int(x["count"])
                total += x["price_dollars"] * ct
                out.append(f"{x['side']} {ct}@{x['price_dollars']:.2f}")
            gates = {k: v for k, v in stats.items() if v and k != "dropped_book_rows"}
            print(f"  {t}: " + (", ".join(out) if out else "NO QUOTE") +
                  (f"  [ramp cap {ramp_ct}ct]" if out else "") +
                  (f"  gates={gates}" if gates else ""))
        cap = float(envmap.get("KALSHI_MAX_TOTAL_CAPITAL", "0"))
        if total > cap:
            res(False, "dry committed within cap", f"~${total:.2f} > ${cap:.0f}")
        else:
            res(True, "dry committed within cap", f"~${total:.2f} of ${cap:.0f}")
    except Exception as e:
        res(False, "dry selection ran", repr(e)[:200])

    return finish()


def finish():
    print(f"=== PREFLIGHT {'FAIL (' + str(len(FAILS)) + ')' if FAILS else 'PASS'}"
          + (f", warns={len(WARNS)}" if WARNS else "") + " ===")
    for f in FAILS:
        print("  FAILED:", f)
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
