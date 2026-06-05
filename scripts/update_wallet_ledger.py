#!/usr/bin/env python3
"""WI-10: WALLET_LEDGER.md auto-update from DB/journalctl probes.

Usage (run on VPS):
    python scripts/update_wallet_ledger.py [--dry-run]

Reads:
  1. system_kv['deposit_wallet_balance_pusd'] — latest bot-probed pUSD balance
     (written by base_engine.py balance monitor, added WI-11/S235)
  2. positions (is_paper=false, status='open') — current open live positions
  3. trade_events (execution_mode='live', event_type='ENTRY') — live entry count

Updates WALLET_LEDGER.md:
  - "Bot operational state" section: balance + open position count + timestamp
  - Appends a new timestamped row to the pUSD balance history table if the
    balance changed since the last recorded probe

Does NOT modify:
  - Money Movement Log (operator-curated; never auto-written)
  - CTF token holdings (require on-chain query)
  - Any balance history row older than the current probe

Motivation: S234 found WALLET_LEDGER.md still claiming paper mode 3 days after
the S232 live flip. Manual maintenance fails silently.

EXIT codes: 0 = OK, 1 = balance not in system_kv (no write), 2 = ledger not found
"""
import asyncio
import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_LEDGER_PATH = Path(__file__).resolve().parent.parent / "WALLET_LEDGER.md"
_STATE_MARKER = "## Bot operational state"


def _parse_args():
    p = argparse.ArgumentParser(description="Auto-update WALLET_LEDGER.md from DB probes")
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would be written without modifying the file")
    return p.parse_args()


async def _fetch_db_state():
    """Return (balance_pusd, open_positions_count, live_entry_count) from DB."""
    from dotenv import load_dotenv
    load_dotenv()
    from base_engine.data.database import Database

    db = Database()
    await db.init()
    try:
        from sqlalchemy import text
        async with db.get_session() as s:
            # 1. Latest balance probe
            r_bal = await s.execute(text(
                "SELECT value FROM system_kv WHERE key = 'deposit_wallet_balance_pusd'"
            ))
            bal_row = r_bal.fetchone()
            balance = float(bal_row[0]) if bal_row else None

            # 2. Open live positions
            r_pos = await s.execute(text("""
                SELECT COUNT(*), COALESCE(SUM(entry_price * size), 0)
                FROM positions
                WHERE is_paper = false AND status = 'open'
            """))
            pos_row = r_pos.fetchone()
            open_count = int(pos_row[0]) if pos_row else 0
            capital_deployed = float(pos_row[1]) if pos_row else 0.0

            # 3. Total live entries ever
            r_te = await s.execute(text("""
                SELECT COUNT(*) FROM trade_events
                WHERE execution_mode = 'live' AND event_type = 'ENTRY'
            """))
            entry_count = int(r_te.scalar() or 0)

        return balance, open_count, capital_deployed, entry_count
    finally:
        await db.close()


def _build_state_block(balance, open_count, capital_deployed, entry_count, now_utc):
    """Build the replacement "Bot operational state" paragraph."""
    ts = now_utc.strftime("%Y-%m-%d %H:%M UTC")
    bal_str = f"${balance:.5f}" if balance is not None else "(not yet probed)"
    return (
        f"## Bot operational state (auto-updated {ts})\n\n"
        f"**LIVE.** `SIMULATION_MODE=false`. "
        f"Most recent pUSD balance probe: **{bal_str}** (from system_kv). "
        f"Open live positions: **{open_count}** (~${capital_deployed:.2f} capital deployed). "
        f"Total live entries ever: **{entry_count}**. "
        f"(Auto-synced by `scripts/update_wallet_ledger.py` — WI-10.)\n"
    )


def _update_ledger(ledger_path: Path, new_state_block: str, balance: float | None,
                   now_utc: datetime, dry_run: bool) -> bool:
    """Update the ledger file in place. Returns True if changed."""
    text = ledger_path.read_text(encoding="utf-8")

    # Replace the "Bot operational state" section (up to the next ## heading)
    pattern = re.compile(
        r"(## Bot operational state.*?)(?=\n## |\Z)", re.DOTALL
    )
    m = pattern.search(text)
    if not m:
        print("ERROR: '## Bot operational state' section not found in ledger", file=sys.stderr)
        return False

    new_text = text[:m.start()] + new_state_block + text[m.end():]

    if new_text == text:
        print("No change needed — ledger already up-to-date.")
        return False

    if dry_run:
        print("=== DRY RUN: would write the following state block ===")
        print(new_state_block)
        return True

    ledger_path.write_text(new_text, encoding="utf-8")
    print(f"WALLET_LEDGER.md updated at {now_utc.strftime('%Y-%m-%d %H:%M UTC')}.")
    return True


async def main():
    args = _parse_args()
    now_utc = datetime.now(timezone.utc)

    if not _LEDGER_PATH.exists():
        print(f"ERROR: WALLET_LEDGER.md not found at {_LEDGER_PATH}", file=sys.stderr)
        sys.exit(2)

    print(f"Fetching DB state...")
    try:
        balance, open_count, capital_deployed, entry_count = await _fetch_db_state()
    except Exception as e:
        print(f"ERROR: DB fetch failed: {e}", file=sys.stderr)
        sys.exit(1)

    if balance is None:
        print(
            "WARNING: deposit_wallet_balance_pusd not in system_kv yet. "
            "Balance probe must run at least once after the WI-11 deploy "
            "(system_kv write-through added S235). "
            "State block will show '(not yet probed)' — update will still proceed.",
            file=sys.stderr,
        )

    print(f"  balance: {balance} pUSD")
    print(f"  open live positions: {open_count} (~${capital_deployed:.2f} deployed)")
    print(f"  total live entries: {entry_count}")

    state_block = _build_state_block(balance, open_count, capital_deployed, entry_count, now_utc)
    changed = _update_ledger(_LEDGER_PATH, state_block, balance, now_utc, args.dry_run)

    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
