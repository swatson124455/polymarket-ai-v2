# KALSHI MAKER LANE — HANDOFF FOR NEW SESSION (2026-07-21)

**Scope: MAKER-KALSHI ONLY.** This session works the Kalshi venue satellite of the Maker
initiative. It does NOT touch: Polymarket Maker (`claude/maker-bot`, held by its own session),
MB/WB/EB/SB code, shared modules, or any other bot's env. Kalshi ≠ "MB"/"MM" — Maker naming rule.

**Branch: `claude/maker-kalshi-live`** (this branch — cut from `claude/maker-bot` tip `3531d83`).
`claude/maker-bot` is CHECKED OUT in another session's worktree — never check it out or commit
to it from here. Commit Kalshi work HERE.

**⚠ The main local checkout `C:/lockes-picks/polymarket-ai-v2` is held by SB on
`claude/sports-bot-owls-backdata`. NEVER write/commit there. Use a linked worktree of THIS
branch (`git worktree add <scratchpad>/kalshi-wt claude/maker-kalshi-live`). Bash cwd drifts to
the SB checkout — use `git -C <worktree>` + absolute paths on every repo op.**

---

## §0 STATE AT HANDOFF (verified 2026-07-21 ~01:00 UTC)

| Thing | State |
|---|---|
| Bot | **STOPPED.** Timer `polymarket-maker-kalshi-live.timer` disabled+inactive; **STOP sentinel present** at `/opt/pa2-maker-kalshi-live/STOP` (any manual service start = flatten-only, no trading) |
| Account | Balance ~$55.35 free; ~$0.72 dust across 6 sub-1-ct fragments (below 1-ct minimum, will settle out); **0 resting orders** |
| Session P&L | Started $100 funded. ~-$45 realized, the bulk from TWO taker fire-sales (see §3 lessons). NUMBERS RULE: these are read-only API pulls w/ citation (kalshi_status_readonly.py), not bot_pnl.py — Kalshi is off-DB |
| VPS deployed code | `/opt/pa2-maker-kalshi-live/maker_kalshi_quoter.py` = md5 `fb9cac8c…` = the OLD "fixes A–H" build. **DOES NOT contain the retool or audit fixes. DO NOT re-enable the timer on it.** Backup of prior: `maker_kalshi_quoter.py.bak-20260721_001143` |
| Retooled code (THE build to carry forward) | **`kalshi_live/maker_kalshi_quoter.py` on THIS branch, md5 `ea28fa38a32462653d6cf3449366c3a7`.** Client `e0b4c9c0…` (matches VPS — no drift). Tests `kalshi_live/test_live_hardening.py` (35/35 pass), smoke `dryrun_smoke.py` clean |
| live.env on VPS | mode=live, ARMED phrase set, `MAX_TOTAL_CAPITAL=90`, `MAX_MARKET_CAPITAL=15`, `INV_SOFT_CT=15`, `INV_HARD_CT=60`, `INV_TOLERANCE=3`, 7-series allowlist (5 temp cities + KXAAAGASD/W), timer unit `OnUnitActiveSec=2min` (operator approved 2-min cadence). Backups: `live.env.bak-*`, timer `.bak-*` |
| Ledger of record | `docs/maker_handoffs/KALSHI_RUNNING_TAB.md` (this branch — append-only; §F fix log, §G live-go post-mortem window, §H widening spec) |
| Keys | prod key id `89314df3-b170-4d3d-9a7c-fc49336365f2`; PEM local `~/.kalshi/prod_key.pem` + VPS `/opt/pa2-maker-kalshi-live/prod_key.pem` (600 polymarket) |

**Read-only status anytime (no side effects):** `python kalshi_live/kalshi_status_readonly.py`
and `python kalshi_live/kalshi_delta_check.py` (per-event net delta — the throttle signal).

---

## §1 THE TRADING MODEL (operator-taught, hard-won — BINDING)

1. **Rewards are paid for QUOTES RESTING ON THE BOOK, not inventory held.** A held YES+NO pair
   is dead weight worth $1 earning nothing. The resting bid+ask near the mid is the paycheck.
   Never reason "I hold a balanced pair, so I'm earning" — that kills the income.
2. **Two separate things, managed separately:** POSITION (contracts owned from fills — keep net
   delta ≈ 0 so the outcome can't hurt) and QUOTES (keep BOTH sides live — that's what earns).
3. **Flat = zero net delta, NOT zero footprint.** Equal contract counts YES vs NO (dollars
   differ). Flatten = one offsetting trade, opposite direction, SAME SIZE as the overhang.
   Long → sell that many. Short → buy that many. NEVER more than |position| (overshoot flips
   the sign — that's a new bet, not a de-risk).
4. **Maker-first always.** Offset by RESTING the reducing-side order and letting it fill ($0
   fee, keeps earning). Taker (cross the spread) = genuine last resort only: it realizes the
   loss, pays the spread on top, and stops the rewards.
5. **Expect to bleed 1–2¢ per pair on the trading leg.** Pair normally costs ~$1.01–1.02 and
   settles at $1.00. That bleed is NORMAL — the cost of qualifying for rewards. Do not "fix"
   it, do not panic on it. Sub-$1 pairs (arb) are the exception, never the plan.
   Income = rewards − bleed. Temp slice = #1 because rewards outrun bleed by the most.
6. **NEVER taker fire-sale on "flatten"/STOP.** Violated twice; cost real money (§3).

## §2 WHAT THE RETOOLED BOT DOES (build `ea28fa38…` — cycle process, honest)

1. **Read state fail-closed** (resting orders + positions). Read fails → no actions this cycle.
   NEW (audit MED-4): every good read persists resting order ids; after 2 consecutive blind
   cycles the bot best-effort CANCELS the last-known ids (blind quotes can't keep filling).
2. **Quote both sides of each selected market** at reference — both ALWAYS live below HARD
   (quotes are the paycheck). Floor `MIN_QUOTE_CT=2`.
3. **Position control by skew:** per-ticker inv + per-event aggregate (`event_deltas`,
   correlated "above X" ladders are additive; trigger = max(|inv|,|event|)).
   - over SOFT: accumulating side shrinks toward the floor + steps 1 tick inside; reducing side
     GROWS toward |inv| at reference, tagged `unwind` (exempt from every capital/budget gate,
     capped at |inv| — can't overshoot past flat).
   - at/over HARD (audit MED-3): accumulating side IS pulled to zero. HARD = hard position
     envelope; max one-way position ≈ INV_HARD_CT + one fill. Bounded risk beats one side's
     reward there.
4. **Settlement ramp (audit HIGH-2):** inside `RAMP_MIN=180` min of end, join sizes scale down
   linearly (unwind quotes NEVER ramped). Goal: BE SMALL at settlement so the settle-taker is a
   rare backstop, not the exit at the worst tick. Full quote pull at `WIND_DOWN_MIN=45`, but a
   held position keeps its reducing-side maker quote (never abandoned into resolution).
5. **Taker fires in exactly one autonomous case:** material position on a market settling
   within `SETTLE_UNWIND_MIN=30` min. Hard-breach alone does NOT taker (the skew handles it).
6. **Capital: accumulating creates stop at `MAX_TOTAL_CAPITAL`; `unwind` creates are never
   blocked** (held value = real `market_exposure_dollars`, not |pos|×$1).
7. **STOP sentinel (audit HIGH-1): maker-first with bounded escalation** — cancel all quotes →
   rest passive offsets (≤|pos|) → wait `STOP_ESCALATE_S=90` → re-read → only residuals still
   ≥ `STOP_TAKER_MIN_CT=5` get taker-crossed, sized to the residual. No more fire-sale, no more
   hanging exposure.

**Known-honest caveats:** skew leaks between cycles on a one-way market (bounded by HARD, not
eliminated); units are mixed (SOFT/HARD in contracts, market cap in dollars; held-$ bounded
only indirectly = HARD ct × price — flagged to the auditor, unresolved); STOP offsets after
escalation-threshold residue are left resting; the S214 caveat — author wrote both code and
tests, 35 green ≠ independent verification.

## §3 LESSONS PAID FOR IN CASH (do not repeat)

- **Fire-sale #1 & #2:** taker-dumped inventory on "flatten"/STOP. Operator: "flatten as a
  maker", then "STOP FUCKING SELLING LIKE A FIRE SAIL". ~$45 gone. The retool §2.7 exists
  because of this. Any new flatten path must be maker-first + bounded escalation.
- **"Multiple stances":** correlated gas-ladder strikes each under SOFT summed to a big
  directional short. Per-event aggregate (`event_deltas`) exists because of this.
- **Stuck bot:** held-value was faked at |pos|×$1 → capital cap blocked the ONLY de-risking
  order. `unwind` exemption + real `market_exposure_dollars` exist because of this.
- **Kalshi decoding traps:** positions field = `position_fp` (signed string; `position` doesn't
  exist). Resting orders: `side` is ALWAYS "yes"; `action` buy/sell distinguishes the real side
  (buy=yes-bid, sell=no-side). 12 two-sided orders LOOK like 12 one-sided if you read `side`.
  `client_order_id` must be alphanumeric+dash (a "." = 400 invalid_parameters).
- **Margin netting:** Kalshi nets correlated ladder strikes (~4.3× observed) — `committed` in
  the cycle line overstates real reserved cash.

## §4 WHAT IS OWED, IN ORDER (operator-agreed sequence)

1. **CODE-LEVEL AUDIT of build `ea28fa38…`** (operator's reviewer offered; sequence "fix HIGHs,
   then audit" — HIGHs are now fixed, so the audit is NEXT). Check-points: code-matches-§2;
   reducing/accumulating classified against LIVE per-cycle position (not stale); units
   SOFT/HARD-ct vs $-caps reconcile; the four audit fixes actually do what §2 claims.
2. **Independent adversarial review** (multi-agent; was blocked by monthly spend limit —
   operator approved overage for fable use, check limit state). Targeted plan B (fix A+B) was
   done inline and caught the `_unwind_size` overshoot; the FULL pass is still owed.
3. **Deploy** retooled quoter to `/opt/pa2-maker-kalshi-live/` (md5-verify `ea28fa38…`),
   remove STOP sentinel, re-enable timer — **ONLY after 1+2 and explicit operator go.**
4. **Then watch:** first 3 cycles for 429s (2-min cadence is 5× the old read rate), per-event
   delta stays bounded, `taker_flattens=0` in normal operation, rewards accrual (first
   settle/deposit timing was an open operator question).
5. Parked: `KXAAAGASM` widening (RUNNING_TAB §H — only after stable green cycles);
   operator flags "3.25 int on open" and ">$250 on handoff" (unexplored, ask operator);
   Sep-1 LIP sunset tripwire.

## §5 COMMANDS

```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"; VPS="ubuntu@18.201.216.0"
# status (read-only)
ssh -i "$KEY" $VPS 'systemctl is-active polymarket-maker-kalshi-live.timer; sudo ls /opt/pa2-maker-kalshi-live/STOP 2>&1; sudo tail -3 /opt/pa2-maker-kalshi-live/plans-*.jsonl'
python kalshi_live/kalshi_status_readonly.py      # balance/positions/resting
python kalshi_live/kalshi_delta_check.py          # per-event net delta
# tests (from kalshi_live/)
python -m pytest test_live_hardening.py -q        # 35/35 expected
python dryrun_smoke.py                            # PROBLEMS: NONE expected
# deploy (ONLY after §4.1+2 + operator go): scp quoter -> /tmp, md5-verify, sudo install -o polymarket,
#   rm STOP, systemctl enable --now polymarket-maker-kalshi-live.timer
# kill: sudo systemctl disable --now polymarket-maker-kalshi-live.timer && sudo -u polymarket touch /opt/pa2-maker-kalshi-live/STOP
# manual flatten fallback: python kalshi_live/flatten_kalshi.py (cancels resting ONLY; positions reported, not dumped)
```

**Step zero for the new session: read this file top to bottom, then `KALSHI_RUNNING_TAB.md`
§F–§H, then verify §0 state yourself (status commands above) before believing it.**
