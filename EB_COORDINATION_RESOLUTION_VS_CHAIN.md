# EB → MB Coordination: Resolution Source-of-Truth Bug

**Filed by:** EB session 2026-05-26.
**Why this is at master root:** `base_engine/data/resolution_backfill.py` is a shared module. Per CLAUDE.md SESSION PRIORITY and Memory RULE ONE-A, shared-module edits require MB session signoff. Filing here following the precedent set by `WB_COORDINATION_POOL_RIGHTSIZE.md` and `EB_COORDINATION_CLOSE_WAIT_LEAK.md`.

**Severity:** P0 for live readiness. P1 for paper.

## TL;DR

The system records `trade_events.RESOLUTION` rows with `price=1.0` or `price=0.0` based on `markets.resolution` (a text column 'YES'/'NO'). For at least one market in the last 14 days (`0x5bcc52fb0270..`), `markets.resolution = 'NO'` was written when the chain settled to YES. That caused a NO-side position to record a winning resolution when on-chain it lost. This is an EB-detected instance of a bug class that affects every bot (MB, WB, EB) because the writer is in a shared ingestion path.

## What was verified (chain-vs-DB)

- Sample window: last 336 hours, EB family resolutions, 22 events total.
- Resolution outcomes cross-checked against `https://clob.polymarket.com/markets/{condition_id}` `outcome_prices`.
- **21 of 22 matched chain. 1 mismatched.**
- Direction of the one mismatch: **in our favor (phantom-gain)**.
- All 32 in-window entry/exit prices matched chain mid within $0.05.
- A parallel agent is extending the verification to all-time resolutions (count source: `bot_pnl.py EsportsBotV2 336` reported 378 raw / 364 clean all-time resolutions).

## The mismatched market

| Field | Source | Value |
|---|---|---|
| condition_id | trade_events / markets | `0x5bcc52fb0270567e273a77d5446835621639596ab45c30552a3e950a0b81b909` |
| question | `markets.question` | "Counter-Strike: GenOne vs megoshort - Map 2 Winner" |
| bot's recorded entry | `bot_pnl.py` ENTRY display | `NO sz=303.0 @ 0.3300 fee=$0.00` |
| bot's recorded resolution | `bot_pnl.py` RESOLUTION display | side=NO @ price=1.00, realized_pnl=`+$198.48` |
| markets.resolution column | direct DB read | **'NO'** ← WRONG |
| markets.outcome_prices | direct DB read | empty |
| chain CLOB outcome_prices | `https://clob.polymarket.com/markets/0x5bcc52fb02..` | `[1, 0]` → YES (GenOne) won |
| chain CLOB resolution text | same API | "GenOne" |

The bot's `trade_events` write is internally consistent — it correctly trusted `markets.resolution='NO'` and computed `_payout=1.0` because `side="NO" == resolution="NO"`. The defect is upstream of the bot.

## Root cause

`base_engine/data/resolution_backfill.py:426`:

```python
res = m.get("resolution") or m.get("outcome") or m.get("resolutionPrice")
```

This trusts the **text fields** from Polymarket's gamma-api response. Direct chain queries during this session showed those text fields are unreliable on Polymarket's side:

- Some closed-and-settled markets return `"resolution": null` (e.g., `0x11e6ef7fe3..`)
- Some closed-and-settled markets return stale `"resolution": "Pending - market scheduled for May 20, 2026..."` (e.g., `0xb184cfef89..`)
- The numeric `outcome_prices` array (e.g., `[1, 0]`) is consistently correct on these same markets.

There IS already an inferrer helper at `resolution_backfill.py:208`:

```python
md["resolution"] = _infer_resolution_from_outcome_prices(m)
```

…but it is only invoked as a **fallback** when the text fields are missing entirely. The text fields take precedence.

For `0x5bcc52fb02..` we have not yet identified WHICH text field (resolution / outcome / resolutionPrice) returned the wrong value at ingest time — that requires log archaeology or re-querying Polymarket's response. The fix doesn't depend on which one — flipping the priority makes either case correct.

## Two RESOLUTION writers downstream (both trust `markets.resolution` / `paper_trades.resolution`)

Both are in `base_engine/data/database.py`:

1. **Phase 4b** — paper_trades-driven. Reads `paper_trades.resolution` (which is itself written from `markets.resolution`). Writes `trade_events.RESOLUTION` with `price=1.0 if pt_pnl.resolution == side else 0.0`. Site: `database.py:3641` and the `insert_trade_event` call at `database.py:3644`.
2. **Phase 4b-alt** — positions-driven. Reads `markets.resolution` directly. Writes `trade_events.RESOLUTION` with `price=1.0 if side == markets.resolution else 0.0`. Site: `database.py:3739` and the `insert_trade_event` call at `database.py:3756`.

Both inherit the upstream defect. Both are run by `polymarket-ingestion.service` from `/opt/polymarket-ai-v2/` (master path, NOT the EB splinter).

## Proposed fix — hardcode CLOB `outcome_prices` as source-of-truth

### Layer 1 — Ingest (primary):

In `base_engine/data/resolution_backfill.py`, change the priority. Make `outcome_prices` (numeric, reliable) the FIRST source, with text fields as fallback only if `outcome_prices` is missing/malformed:

```python
# Before (current line ~426):
res = m.get("resolution") or m.get("outcome") or m.get("resolutionPrice")

# After (proposed):
res = _infer_resolution_from_outcome_prices(m)
if res is None:
    res = m.get("resolution") or m.get("outcome") or m.get("resolutionPrice")
```

And require `_infer_resolution_from_outcome_prices(m)` to return `None` (not a default) if `outcome_prices` isn't exactly one-`1`-one-`0`. That defers the write to next backfill cycle rather than recording a guess.

### Layer 2 — Write-time guard (defense in depth):

At `database.py:3641` and `database.py:3739`, before writing `trade_events.RESOLUTION`, re-check chain. If `markets.resolution` disagrees with a freshly-fetched `outcome_prices`, log loudly and defer. This catches drift between ingest time and write time.

### Layer 3 — Backfill correction:

Once Layer 1 is live, run a one-off script (model: `cleanup_phantom_resolutions.py`) that:
1. Re-queries CLOB for every `markets.condition_id` where `markets.resolved=true`.
2. Identifies rows where DB's stored `resolution` disagrees with CLOB's `outcome_prices`.
3. For each, deletes the bad `trade_events.RESOLUTION` row and updates `markets.resolution` to the chain truth, allowing phase4b to re-emit correctly.

Once Layer 3 runs, `bot_pnl.py` will report a corrected canonical Net P&L.

## What this EB session does NOT do

- Does NOT modify `base_engine/data/resolution_backfill.py` or `database.py` — these are shared modules. Awaiting MB signoff.
- Does NOT modify `markets.resolution` rows directly in the DB — corrections happen in Layer 3 after the writer fix.
- Does NOT extend the `cleanup_phantom_resolutions.py` script — different bug class (size inflation vs outcome inversion).

## What this EB session DID do

- Diagnosed the bug class end-to-end (this memo).
- Wrote project memo `project_eb_resolution_mismatch_5bcc52fb02_2026_05_26.md` in EB session memory.
- Launched parallel agent extending verification across all-time EB resolutions (in progress — results will be filed as memo extension).
- The 0752852 EB splinter deploy (zero-stake observability log) is unrelated to this bug class.

## Verification path for next session

```bash
# Sanity-check the canonical sample
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 \
  "sudo -u postgres psql polymarket -t -A -F '|' -c \"SELECT condition_id, resolution, outcome_prices FROM markets WHERE condition_id='0x5bcc52fb0270567e273a77d5446835621639596ab45c30552a3e950a0b81b909';\""

# Compare to chain
curl -s 'https://clob.polymarket.com/markets/0x5bcc52fb0270567e273a77d5446835621639596ab45c30552a3e950a0b81b909' | python -c "import json,sys; d=json.load(sys.stdin); print('outcomes:',d.get('outcomes'),'\noutcome_prices:',d.get('outcome_prices'),'\nresolution:',d.get('resolution'))"

# After fix lands + cleanup script runs, re-verify with bot_pnl.py
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 \
  "cd /opt/polymarket-ai-v2 && PYTHONPATH=/opt/polymarket-ai-v2 ./venv/bin/python scripts/bot_pnl.py EsportsBotV2 336"
```

## Blast radius (per CLAUDE.md cross-bot verification)

`resolution_backfill.py` and `database.py:phase4b_*` are called by `polymarket-ingestion.service` which writes resolutions for ALL bots:
- MirrorBot
- WeatherBot
- EsportsBot (v1)
- EsportsBotV2
- (any future bots using `trade_events`)

This bug class likely affects every bot's recorded P&L proportionally to its trade volume. MB has highest volume → likely most affected in absolute dollar terms.

## Related artifacts

- `project_eb_resolution_mismatch_5bcc52fb02_2026_05_26.md` (EB session memo, with bot_pnl.py-canonical citations only)
- `scripts/verify_resolutions.py` — existing script that has the same brittleness (compares text fields, not outcome_prices). Should be updated as part of Layer 1.
- `scripts/cleanup_phantom_resolutions.py` — different bug class (S134 size inflation), not the same code path. Model for Layer 3 cleanup script.
- `base_engine/audit/checks/resolution_verification_check.py` — currently checks for duplicates only, not chain-disagreement. Could be extended.

## Hand-off

MB session: please ack receipt and signoff before any of Layers 1-3 land on master. EB session is not touching shared modules.
