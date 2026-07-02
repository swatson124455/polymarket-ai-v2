#!/usr/bin/env python3
"""M0-DB: read-only verification of the MirrorBot salvage DATA-table claims.

The SALVAGE_PACKAGE.json / MB_SALVAGE_MANIFEST.md verdicts for the PostgreSQL
data assets rest on a 2026-07-01 audit that a fresh clone cannot reproduce (no
DB reachable outside the VPS). This script re-measures each data-table claim
against the live `polymarket` DB and prints PASS / FAIL / REVIEW per asset. It
is the M0-DB gate in MB_REBUILD_PLAN.md: all data-dependent rebuild work
(fill-replay backtest, label extraction) is blocked until this passes.

WHY A SEPARATE SCRIPT: the manifest itself lists the gate-stage labeled
intersection of mirror_rejected_signals as NEVER VERIFIED (MB_SALVAGE_MANIFEST.md
"What could NOT be verified": "Exact full-table labeled COUNT ... planner
estimate ~80%, skewed old"). This measures it directly.

SAFETY (the live bots share this DB; it was storm-crippled during the original
audit):
  * READ-ONLY — SELECT / pg_class metadata only. No INSERT/UPDATE/DDL.
  * Gentle by default — huge-table counts use the pg_class.reltuples planner
    estimate (instant, no scan). Pass --exact for real COUNT(*) (slower; only
    when the DB is quiet).
  * Every heavy statement runs under a statement_timeout (default 20s, tune with
    --timeout) and degrades to a REVIEW row on timeout instead of hammering.
  * Date ranges use primary-key id extremes (index-only) rather than MIN/MAX on
    unindexed time columns.

INVOCATION (on the VPS):
    cd /opt/polymarket-ai-v2 && \
      sudo -u polymarket env PYTHONPATH=/opt/polymarket-ai-v2 \
      venv/bin/python scripts/verify_salvage_data.py            # gentle (default)
    ... scripts/verify_salvage_data.py --exact                  # exact counts
    ... scripts/verify_salvage_data.py --timeout 60 --exact     # quiet-DB deep run

Exit code 0 if no FAIL rows, 1 if any FAIL. REVIEW rows (timeouts / manifest
value unstated) do not fail the run but must be eyeballed.

Provenance rule (CLAUDE.md Forbidden Patterns 7/8/10): the numbers this script
prints are the ONLY sanctioned source for these data-table facts going forward.
Do NOT carry the manifest's quoted figures into commits/reports as fact — cite
THIS script's output.
"""
from __future__ import annotations

import argparse
import asyncio
from typing import Any, Optional

from dotenv import load_dotenv
from sqlalchemy import text

from base_engine.data.database import Database

load_dotenv()

# ── Manifest claims to check against (source: SALVAGE_PACKAGE.json / MB_SALVAGE_MANIFEST.md,
#    2026-07-01). Ranges are generous — we are validating order-of-magnitude + shape, not
#    exact reproduction. A value inside [lo, hi] PASSes; outside REVIEWs/FAILs per note. ──
MANIFEST = {
    "orderbook_snapshots_rows": (30_000_000, 60_000_000, "~37.7M rows"),
    "mirror_rejected_rows": (12_000_000, 20_000_000, "~16.4M live rows (id seq ~17.5M)"),
    "whale_movements_rows": (5_000, 20_000, "~9,121 rows, ~68/day"),
    "shadow_fills_mb_total": (10_000, 20_000, "13,855 rows"),
    "shadow_fills_mb_executed": (4_000, 8_000, "5,823 executed"),
    "positions_mb_sell_paper": (800, 3_000, "~1,421 paper rows side='SELL'"),
    "positions_mb_sell_live": (10, 200, "~55 live rows side='SELL'"),
}

MB = "MirrorBot"


class Checker:
    def __init__(self, db: Database, exact: bool, timeout_s: int):
        self.db = db
        self.exact = exact
        self.timeout_ms = timeout_s * 1000
        self.rows: list[tuple[str, str, str]] = []  # (verdict, asset, detail)
        self.failed = False

    async def _scalar(self, session, sql: str, **params) -> Any:
        """Run a single scalar SELECT under the statement timeout. Returns the value,
        or the string 'TIMEOUT'/'ERR:<msg>' on failure (never raises)."""
        try:
            await session.execute(text(f"SET LOCAL statement_timeout = {self.timeout_ms}"))
            r = await session.execute(text(sql), params)
            return r.scalar()
        except Exception as e:
            msg = str(e).lower()
            if "timeout" in msg or "canceling statement" in msg:
                return "TIMEOUT"
            return f"ERR:{str(e)[:80]}"

    def _record(self, verdict: str, asset: str, detail: str):
        self.rows.append((verdict, asset, detail))
        if verdict == "FAIL":
            self.failed = True

    def _check_range(self, key: str, value: Any, asset: str):
        lo, hi, claim = MANIFEST[key]
        if isinstance(value, str):  # TIMEOUT / ERR
            self._record("REVIEW", asset, f"{value} (claim {claim})")
            return
        if value is None:
            self._record("FAIL", asset, f"NULL/absent (claim {claim})")
            return
        v = int(value)
        verdict = "PASS" if lo <= v <= hi else "FAIL"
        self._record(verdict, asset, f"measured={v:,} vs claim {claim} [{lo:,}–{hi:,}]")

    async def _count(self, session, table: str, where: str = "") -> Any:
        """reltuples estimate by default; exact COUNT(*) under --exact."""
        if self.exact:
            return await self._scalar(session, f"SELECT COUNT(*) FROM {table} {where}")
        if where:
            # reltuples is whole-table only; fall back to exact for filtered counts.
            return await self._scalar(session, f"SELECT COUNT(*) FROM {table} {where}")
        return await self._scalar(
            session, "SELECT reltuples::bigint FROM pg_class WHERE relname = :t", t=table
        )

    async def _date_range_by_id(self, session, table: str, time_col: str) -> str:
        """MIN/MAX time via PK id extremes (index-only)."""
        lo = await self._scalar(
            session, f"SELECT {time_col} FROM {table} WHERE id = (SELECT MIN(id) FROM {table})"
        )
        hi = await self._scalar(
            session, f"SELECT {time_col} FROM {table} WHERE id = (SELECT MAX(id) FROM {table})"
        )
        return f"{lo} → {hi}"

    async def run(self):
        async with self.db.get_session() as s:
            # 1. orderbook_snapshots — the best rebuild asset (fill-replay feasibility)
            rows = await self._count(s, "orderbook_snapshots")
            self._check_range("orderbook_snapshots_rows", rows, "orderbook_snapshots.rows")
            rng = await self._date_range_by_id(s, "orderbook_snapshots", "snapshot_time")
            self._record("INFO", "orderbook_snapshots.window", rng)

            # 2. mirror_rejected_signals — whale signal → outcome labels
            rows = await self._count(s, "mirror_rejected_signals")
            self._check_range("mirror_rejected_rows", rows, "mirror_rejected_signals.rows")
            rng = await self._date_range_by_id(s, "mirror_rejected_signals", "event_time")
            self._record("INFO", "mirror_rejected_signals.window", rng)

            # 2b. THE never-verified number: gate-stage labeled (feature-rich AND trainable)
            gate_total = await self._scalar(
                s, "SELECT COUNT(*) FROM mirror_rejected_signals WHERE rejection_stage = 'gate'"
            )
            gate_labeled = await self._scalar(
                s,
                "SELECT COUNT(*) FROM mirror_rejected_signals "
                "WHERE rejection_stage = 'gate' AND resolution IS NOT NULL",
            )
            # Approx dedup to (trader, market, side) + hour window (proxy for the tx window
            # in the manifest cleaning recipe). Reported as APPROX — the true window is tx-based.
            gate_dedup = await self._scalar(
                s,
                "SELECT COUNT(*) FROM (SELECT DISTINCT trader_address, market_id, side, "
                "date_trunc('hour', event_time) AS w FROM mirror_rejected_signals "
                "WHERE rejection_stage = 'gate' AND resolution IS NOT NULL) q",
            )
            self._record("INFO", "gate_rows.total", _fmt(gate_total))
            self._record("REVIEW", "gate_rows.labeled (never-verified)", _fmt(gate_labeled))
            self._record("REVIEW", "gate_rows.labeled_deduped ~(trader,market,side,hour)", _fmt(gate_dedup))
            # per-day shape → is it really "low hundreds/day"?
            try:
                await s.execute(text(f"SET LOCAL statement_timeout = {self.timeout_ms}"))
                r = await s.execute(text(
                    "SELECT DATE(event_time) d, COUNT(*) c FROM mirror_rejected_signals "
                    "WHERE rejection_stage='gate' AND resolution IS NOT NULL "
                    "GROUP BY 1 ORDER BY 1 DESC LIMIT 7"
                ))
                recent = "; ".join(f"{row[0]}={row[1]}" for row in r.fetchall())
                self._record("INFO", "gate_labeled.per_day (last 7d)", recent or "(none)")
            except Exception as e:
                self._record("REVIEW", "gate_labeled.per_day", f"ERR:{str(e)[:60]}")

            # 3. whale_movements — thin smart-money stream; smart_money_rank coverage
            rows = await self._count(s, "whale_movements")
            self._check_range("whale_movements_rows", rows, "whale_movements.rows")
            rng = await self._date_range_by_id(s, "whale_movements", "timestamp")
            self._record("INFO", "whale_movements.window", rng)
            rank_cov = await self._scalar(
                s, "SELECT ROUND(100.0 * AVG(CASE WHEN smart_money_rank IS NOT NULL THEN 1 ELSE 0 END), 1) "
                   "FROM whale_movements"
            )
            self._record("INFO", "whale_movements.smart_money_rank_pct", f"{rank_cov}% populated")

            # 4. shadow_fills(MB) — execution mechanics usable; shadow_pnl NOT usable (should be 100% NULL)
            tot = await self._scalar(s, "SELECT COUNT(*) FROM shadow_fills WHERE bot_name = :b", b=MB)
            self._check_range("shadow_fills_mb_total", tot, "shadow_fills(MB).total")
            ex = await self._scalar(
                s, "SELECT COUNT(*) FROM shadow_fills WHERE bot_name = :b AND trade_executed = true", b=MB
            )
            self._check_range("shadow_fills_mb_executed", ex, "shadow_fills(MB).executed")
            pnl_null = await self._scalar(
                s, "SELECT ROUND(100.0 * AVG(CASE WHEN shadow_pnl IS NULL THEN 1 ELSE 0 END), 1) "
                   "FROM shadow_fills WHERE bot_name = :b", b=MB
            )
            # manifest: shadow_pnl 100% NULL for MB → NOT usable for P&L. Confirm.
            _v = "PASS" if isinstance(pnl_null, (int, float)) and float(pnl_null) >= 99.0 else "REVIEW"
            self._record(_v, "shadow_fills(MB).shadow_pnl_null_pct",
                         f"{pnl_null}% NULL (manifest: 100% → not P&L-usable)")

            # 5. positions(MB) side='SELL' column-integrity corruption (recoverable via token_id)
            paper = await self._scalar(
                s, "SELECT COUNT(*) FROM positions WHERE (source_bot = :b OR bot_id = :b) "
                   "AND side = 'SELL' AND is_paper = true", b=MB
            )
            live = await self._scalar(
                s, "SELECT COUNT(*) FROM positions WHERE (source_bot = :b OR bot_id = :b) "
                   "AND side = 'SELL' AND is_paper = false", b=MB
            )
            self._check_range("positions_mb_sell_paper", paper, "positions(MB).side=SELL.paper")
            self._check_range("positions_mb_sell_live", live, "positions(MB).side=SELL.live")

    def print_report(self):
        print("\n" + "=" * 78)
        print("  M0-DB — MirrorBot salvage data-table verification")
        print(f"  mode: {'EXACT counts' if self.exact else 'planner-estimate counts (--exact for real)'}"
              f" · statement_timeout {self.timeout_ms}ms")
        print("=" * 78)
        w = max(len(a) for _, a, _ in self.rows) if self.rows else 20
        for verdict, asset, detail in self.rows:
            tag = {"PASS": "PASS ", "FAIL": "FAIL ", "REVIEW": "?????", "INFO": "  ·  "}.get(verdict, verdict)
            print(f"  [{tag}] {asset.ljust(w)}  {detail}")
        print("=" * 78)
        n_fail = sum(1 for v, _, _ in self.rows if v == "FAIL")
        n_review = sum(1 for v, _, _ in self.rows if v == "REVIEW")
        print(f"  {n_fail} FAIL · {n_review} REVIEW · "
              f"{sum(1 for v,_,_ in self.rows if v=='PASS')} PASS")
        if n_fail:
            print("  RESULT: FAIL — a data-table claim did not hold; M0-DB gate NOT passed.")
        else:
            print("  RESULT: no FAILs — eyeball the REVIEW rows (esp. the gate-labeled counts),")
            print("          then the M0-DB gate is cleared for data-dependent rebuild work.")
        print("=" * 78 + "\n")


def _fmt(v: Any) -> str:
    return f"{int(v):,}" if isinstance(v, (int, float)) else str(v)


async def _main(exact: bool, timeout_s: int) -> int:
    db = Database()
    await db.init()
    try:
        checker = Checker(db, exact=exact, timeout_s=timeout_s)
        await checker.run()
        checker.print_report()
        return 1 if checker.failed else 0
    finally:
        await db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Read-only M0-DB salvage data verification")
    ap.add_argument("--exact", action="store_true",
                    help="use exact COUNT(*) for huge tables (slower; only on a quiet DB)")
    ap.add_argument("--timeout", type=int, default=20,
                    help="per-statement timeout in seconds (default 20)")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_main(args.exact, args.timeout)))
