#!/usr/bin/env python3
"""Read-only data-integrity harness — 3 legs per table, PASS/WARN/FAIL report.

Legs:
  1. impossible-signature scan  — values that cannot be real (internal, cheap)
  2. live cross-check           — stored value vs live CLOB/gamma ground truth
  3. distribution / liveness    — cadence, staleness, coverage shape

Tables: orderbook_snapshots, market_prices, positions, trades, whale_trades,
users, markets. Born from the 2026-07-14 audit that found orderbook_snapshots
100% worst-of-book (collector bug 61541c5) — each check below codifies a real
failure mode observed in this system, so the harness doubles as a regression
gate after every data-pipeline fix.

Usage (on the VPS, or any host with DATABASE_URL + psql + internet):
    DATABASE_URL=... python scripts/verify_data_integrity.py
Or from a workstation without writing files to the VPS:
    ssh <vps> "set -a; source /opt/pa2-shared/.env; set +a; python3 -" \
        < scripts/verify_data_integrity.py

Strictly read-only: SELECT-only SQL (10s statement timeout per query), GET-only
HTTP. Exit code 1 if any FAIL, else 0.
"""
import json
import os
import subprocess
import sys
import urllib.request

RESULTS = []  # (table, check, status, detail)
UA = {"User-Agent": "pa2-data-integrity/1.0"}
CLOB_BOOK = "https://clob.polymarket.com/book?token_id="


def q(sql):
    """Run one SELECT via psql -Atc. Returns list of |-split rows ([] on error).

    The timeout is an inline SET (PGOPTIONS is rejected by PgBouncer: 'unsupported
    startup parameter in options'), so psql emits a literal 'SET' line before the
    data — filter it out of the parsed rows (both learned on first runs, 2026-07-14)."""
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("FATAL: DATABASE_URL not set", file=sys.stderr)
        sys.exit(2)
    try:
        out = subprocess.run(
            ["psql", dsn, "-Atc", f"SET statement_timeout='45s'; {sql}"],
            capture_output=True, text=True, timeout=60)
    except Exception as e:  # noqa: BLE001 — harness must not die on one query
        RESULTS.append(("-", "psql", "FAIL", f"query error: {e}"))
        return []
    if out.returncode != 0:
        RESULTS.append(("-", "psql", "WARN", (out.stderr or "").strip()[:160]))
        return []
    return [r.split("|") for r in out.stdout.strip().splitlines() if r and r != "SET"]


def live_mid(token_id):
    """Best-of-book mid from the live CLOB (sort-defensive), or None."""
    try:
        req = urllib.request.Request(CLOB_BOOK + token_id, headers=UA)
        book = json.load(urllib.request.urlopen(req, timeout=10))
    except Exception:  # noqa: BLE001
        return None
    def best(levels, want_max):
        px = []
        for lv in levels if isinstance(levels, list) else []:
            try:
                p = float(lv.get("price"))
            except (AttributeError, TypeError, ValueError):
                continue
            if 0.0 < p < 1.0:
                px.append(p)
        return (max(px) if want_max else min(px)) if px else None
    bb = best(book.get("bids"), True)
    ba = best(book.get("asks"), False)
    return (bb + ba) / 2 if bb is not None and ba is not None else None


def add(table, check, status, detail):
    RESULTS.append((table, check, status, detail))


# ── orderbook_snapshots ──────────────────────────────────────────────────────
def check_orderbook_snapshots():
    t = "orderbook_snapshots"
    r = q("SELECT count(*),"
          " round(100.0*count(*) FILTER (WHERE spread>0.9)/GREATEST(count(*),1),1),"
          " round(avg(mid_price)::numeric,4)"
          " FROM orderbook_snapshots WHERE snapshot_time > now()-interval '2 hours'")
    if not r:
        return add(t, "sig:worst-of-book", "WARN", "no data / query failed")
    n, pct_wide, avg_mid = int(r[0][0]), float(r[0][1] or 0), r[0][2]
    if n == 0:
        return add(t, "leg3:liveness", "FAIL", "0 rows in last 2h — collector down?")
    # 61541c5 regression gate: worst-of-book capture makes spread~0.998, mid~0.5
    if pct_wide > 50:
        add(t, "sig:worst-of-book", "FAIL",
            f"{pct_wide}% of {n} rows spread>0.9, avg mid {avg_mid} — worst-of-book bug signature")
    else:
        add(t, "sig:worst-of-book", "PASS", f"{pct_wide}% wide of {n} rows, avg mid {avg_mid}")


# ── market_prices ────────────────────────────────────────────────────────────
def check_market_prices():
    t = "market_prices"
    r = q("SELECT count(*), count(*) FILTER (WHERE price=0.5),"
          " round(100.0*count(*) FILTER (WHERE price BETWEEN 0.499 AND 0.501)/GREATEST(count(*),1),3)"
          " FROM market_prices WHERE timestamp > now()-interval '6 hours'")
    if not r:
        return add(t, "sig:phantom-0.5", "WARN", "query failed")
    n, exact, near_pct = int(r[0][0]), int(r[0][1]), float(r[0][2] or 0)
    if n == 0:
        add(t, "leg3:liveness", "FAIL", "0 rows in 6h — price stream down?")
        return
    # d2f5c2f regression gate: strategy-2 midpoint bug writes ~0.5 phantoms
    status = "FAIL" if near_pct > 5 else "PASS"
    add(t, "sig:phantom-0.5", status, f"{exact} exact-0.5 / {n} rows 6h ({near_pct}% near)")
    # leg 2: recent stored price vs live CLOB mid on a small sample
    rows = q("SELECT DISTINCT ON (token_id) token_id, price FROM market_prices"
             " WHERE timestamp > now()-interval '10 minutes'"
             " AND price BETWEEN 0.03 AND 0.97 ORDER BY token_id, timestamp DESC LIMIT 5")
    ok = bad = skip = 0
    for token, price in rows:
        m = live_mid(token)
        if m is None:
            skip += 1
        elif abs(float(price) - m) <= 0.05:
            ok += 1
        else:
            bad += 1
    if ok + bad == 0:
        add(t, "live:vs-clob-mid", "WARN", f"no comparable sample (skipped {skip})")
    else:
        add(t, "live:vs-clob-mid", "PASS" if bad == 0 else "FAIL",
            f"{ok}/{ok+bad} within 5pt of live CLOB mid (skipped {skip})")


# ── positions ────────────────────────────────────────────────────────────────
def check_positions():
    t = "positions"
    rows = q("SELECT token_id, current_price FROM positions"
             " WHERE status='OPEN' AND token_id IS NOT NULL AND current_price IS NOT NULL"
             " ORDER BY opened_at DESC LIMIT 5")
    if not rows:
        return add(t, "live:marks-vs-clob", "WARN", "no open positions to check")
    ok = bad = skip = 0
    worst = 0.0
    for token, cur in rows:
        m = live_mid(token)
        if m is None:
            skip += 1
            continue
        diff = abs(float(cur) - m)
        worst = max(worst, diff)
        ok, bad = (ok + 1, bad) if diff <= 0.10 else (ok, bad + 1)
    if ok + bad == 0:
        add(t, "live:marks-vs-clob", "WARN", f"no live books for {skip} sampled tokens")
    else:
        add(t, "live:marks-vs-clob", "PASS" if bad == 0 else "FAIL",
            f"{ok}/{ok+bad} marks within 10pt of live mid (worst {worst:.3f}, skipped {skip})")


# ── trades ───────────────────────────────────────────────────────────────────
def check_trades():
    t = "trades"
    r = q("SELECT now()-max(timestamp) < interval '2 hours', max(timestamp) FROM trades")
    if not r:
        return add(t, "leg3:recency", "WARN", "query failed")
    fresh, latest = r[0][0], r[0][1]
    add(t, "leg3:recency", "PASS" if fresh == "t" else "FAIL",
        f"latest print {latest} ({'fresh' if fresh == 't' else 'STALE — tape ingestion down?'})")


# ── whale_trades ─────────────────────────────────────────────────────────────
def check_whale_trades():
    t = "whale_trades"
    r = q("SELECT now()-max(event_time) < interval '7 days', max(event_time) FROM whale_trades")
    if not r:
        return add(t, "leg3:staleness", "WARN", "query failed")
    fresh, latest = r[0][0], r[0][1]
    # Known-broken since 2026-03-19 — this check exists so a revived feed flips to PASS
    add(t, "leg3:staleness", "PASS" if fresh == "t" else "FAIL",
        f"latest event {latest} ({'fresh' if fresh == 't' else 'feed dead since 2026-03-19'})")


# ── users ────────────────────────────────────────────────────────────────────
def check_users():
    t = "users"
    r = q("SELECT count(*), count(*) FILTER (WHERE roi=0), count(*) FILTER (WHERE total_profit>0)"
          " FROM users WHERE total_trades >= 100")
    if not r:
        return add(t, "sig:degenerate-scores", "WARN", "query failed")
    n, roi0, prof = (int(x) for x in r[0])
    if n == 0:
        return add(t, "sig:degenerate-scores", "WARN", "no users with >=100 trades")
    pct0 = 100.0 * roi0 / n
    # Degenerate-scoring signature found 2026-07-14: roi never populated
    add(t, "sig:degenerate-scores", "FAIL" if pct0 > 95 else "PASS",
        f"{pct0:.1f}% of {n} scored users have roi=0 ({prof} 'profitable')")


# ── markets ──────────────────────────────────────────────────────────────────
def check_markets():
    t = "markets"
    r = q("SELECT count(*) FILTER (WHERE resolved AND resolution IS NULL),"
          " count(*) FILTER (WHERE active AND NOT resolved AND end_date_iso < now()-interval '30 days')"
          " FROM markets")
    if not r:
        return add(t, "sig:resolution-consistency", "WARN", "query failed")
    unres, zombie = int(r[0][0]), int(r[0][1])
    add(t, "sig:resolution-consistency", "PASS" if unres == 0 else "WARN",
        f"{unres} resolved-with-NULL-resolution; {zombie} active markets ended >30d ago (informational)")


def main():
    for fn in (check_orderbook_snapshots, check_market_prices, check_positions,
               check_trades, check_whale_trades, check_users, check_markets):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — one broken check must not hide the rest
            add(fn.__name__, "harness", "WARN", f"check crashed: {e}")

    width = max(len(r[0]) for r in RESULTS) + 2
    fails = 0
    print(f"\n{'table':<{width}}{'check':<26}{'status':<7}detail")
    print("-" * 100)
    for table, check, status, detail in RESULTS:
        fails += status == "FAIL"
        print(f"{table:<{width}}{check:<26}{status:<7}{detail}")
    print(f"\n{fails} FAIL / {sum(1 for r in RESULTS if r[2]=='WARN')} WARN"
          f" / {sum(1 for r in RESULTS if r[2]=='PASS')} PASS")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
