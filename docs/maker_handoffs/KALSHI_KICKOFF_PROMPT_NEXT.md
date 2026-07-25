# KICKOFF PROMPT — paste into the new Kalshi session

```
KALSHI MAKER LANE — new session. Kalshi venue ONLY. Real money. Bot is PARKED.

STEP ZERO, in this order, before you say anything with a number in it:
1. Read docs/maker_handoffs/KALSHI_HANDOFF_2026-07-25_NEW_SESSION.md — §0 is a
   RETRACTION TABLE of 10 claims the previous session got wrong, including the
   lane's own R1 pool formula. Do not inherit them. Anything contradicting §0 in
   an older doc or commit message is void.
2. Read memory: RULE ZERO, RULE SIX, RULE SEVEN, and
   project_kalshi_r1_formula_wrong.md. A UserPromptSubmit hook injects these
   before every turn — they are binding, not advisory.
3. Verify live state yourself, do not trust this prompt's numbers:
   git branch --show-current   (expect claude/maker-kalshi-live @ ab46182)
   ssh -i "C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" ubuntu@18.201.216.0 \
     'sudo -n md5sum /opt/pa2-maker-kalshi-live/maker_kalshi_quoter.py; \
      sudo -n grep -E "MAX_TOTAL_CAPITAL|TAKER_FLATTEN|REDUCE_ONLY" /opt/pa2-maker-kalshi-live/live.env'
   cd kalshi_live && python kalshi_ledger.py --no-snapshot
   (the ubuntu login CANNOT read /opt/pa2-maker-kalshi-live — use sudo -n bash -c so globs expand as root)

OPERATOR DIRECTIVE THIS SESSION:
  "Proceed as if temp is live. Just monitor until it's back up."
  → Do NOT write temp off. Temp (KXTEMPAUSH/CHIH/DCH/LAXH/NYCH) earned $55.03 of
    the $88.07 reward ledger — 18 of 31 items, the largest earning family. It has
    been ABSENT from every active pull since its last program ended
    2026-07-22T17:00:00Z. Absent ≠ gone. Temp programs are ~58-minute hourly
    windows that only exist while that hour's market is live.
  → FIRST ACTION: restart the watcher. It does not survive a session change:
    Monitor(command: 'cd <scratch> && python -u temp_watch.py',
            persistent: true, description: 'KXTEMP programs returning')
    Script already written at
    .../8289ccb6-6121-4cea-8b80-ab6ee71a2ab1/scratchpad/temp_watch.py
    It emits TEMP BACK on return, ERROR on failures, HEARTBEAT hourly, and
    appends every poll to temp_poll.jsonl (≈88+ polls of history already there).
  → Plan the temp-live configuration NOW so it is ready when temp returns.
    Tension to resolve, both measured: temp is the BEST earner ($55.03) and the
    WORST fill-cost family (maker round trips −6.12¢/ct vs gas −1.55¢/ct).

HARD RULES:
- Kalshi venue only. Never touch claude/maker-bot (Polymarket Maker), MB/WB/EB/SB,
  or shared modules. Commit only on claude/maker-kalshi-live via your worktree.
- The wind-down is enforced by KALSHI_MAX_TOTAL_CAPITAL=1, NOT by reduce-only.
  Raising it IS un-parking a real-money bot and needs explicit operator sign-off.
  A "just flip a flag" plan rests NOTHING at cap=1 (a 20ct order costs >> $1).
- PENDING ≠ ZERO. An event whose reward window has not closed has nothing to
  credit yet. KXAAAGASW is a WEEKLY closing 07-27; a previous session scored it
  as "earned zero" and built a false conclusion on it. Put this rule in the
  SCRIPT, not in your head — it was violated twice in one session.
- Every number needs its SOURCE and its DENOMINATOR in the same breath. Never
  extrapolate a subset to a total. Label ESTABLISHED / INFERRED / HYPOTHESIS.
- Losses: ~61–77% were agent implementation defects (a RANGE, not a single %,
  and only valid against the −$122.57 basis). Never cite a loss total bare.
  Framing on record: reward-positive, defect-negative.
- Verified brevity over exhaustive lists. The operator has called out 60-bullet
  dumps as waste. Lead with the number that changes the decision.

THE HIGHEST-VALUE UNBLOCKED WORK (from §3 of the handoff):
  "What actually pays" is currently UNANSWERABLE, and the reason is telemetry,
  not analysis. Plan logs are per-CYCLE, not per-market: in the hour closing
  2026-07-22T10:00Z we quoted three events that paid $12.94 / $1.51 / $0.00
  while sharing the same cycles, so they share one at_ref_pct value. Pool size
  does NOT separate paid from zero (975 vs 942 $/day).
  → Log PER MARKET PER CYCLE: our resting size, our price vs the reference, and
    the competing qualifying depth. Nothing else makes a reward model testable.
  Note the largest single credit in the ledger ($12.94) came from our SMALLEST
  footprint — one fill, 20 contracts, one strike — so the dominant variable is
  competition in the R4 score denominator, which we do not currently measure.

OPEN OPERATOR DECISIONS (do not decide these yourself):
  1. Exact deposit total — is it $365? A ~$9.70 difference closes the entire
     $9.72 derived-vs-UI reward gap. Deposits is the one input with no receipt.
  2. Re-read KXTEMPNYCH-26JUL2206 $12.94 off the UI — 14.7% of the total, a 9×
     outlier on reward÷notional, uncorroborable.
  3. Un-park or not (requires raising MAX_TOTAL_CAPITAL).
  4. KXAAAGASM — rank 3 on the venue ($5,400/day, 152h window), a gas sibling we
     have never quoted.

ALSO NOT DEPLOYED, on the branch, do not arm without review:
  WS daemon (KALSHI_WS_HOT default 0; 11/11 mutants killed; owed a round-3 review
  + one observed live fill — the fill channel payload has never been seen).
  ⚠ Its original justification (fast-flatten) was RETRACTED — it is robustness
  infrastructure, not a proven money-maker.
  HTTP pooling (KALSHI_HTTP_POOL default 0; measured 173ms → 63ms p50).
  kalshi_ledger.py owes two fixes: drop unrealized from the reward derivation so
  the mark cancels, and stop printing "IDENTITY CHECK closes" (it is vacuous).
```
