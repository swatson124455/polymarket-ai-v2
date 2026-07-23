# KALSHI MAKER — NEXT-SESSION KICKOFF PROMPT (paste this as the opening message)

Copy everything between the lines into the next session's first message.

---

KALSHI MAKER LANE — new session, maker-kalshi ONLY.

**STEP ZERO — do this before anything else, in order:**

1. **Worktree.** The main checkout is another bot's. Create your own:
   `git worktree add <your-scratchpad>/kalshi-wt claude/maker-kalshi-live`
   Bash cwd drifts back to the main checkout — use `git -C <worktree>` + absolute paths on every repo op.

2. **Read, in order (all on branch `claude/maker-kalshi-live`, HEAD `f9946aa`):**
   * `docs/maker_handoffs/KALSHI_HANDOFF_2026-07-23_EOD.md` — full state, every live change, all open items. THIS IS THE MASTER DOC.
   * `docs/maker_handoffs/KALSHI_LIP_RULE_CANON.md` — the rules (R1–R4), the term glossary (§T), and every measurement §M1–M13. Pull rules from here, never memory.
   * Then the 4 dive deliverables as needed: `KALSHI_INVENTORY_DOCTRINE_2026-07-23.md`, `KALSHI_DRIFT_AWARENESS_2026-07-23.md`, `KALSHI_EXPANSION_PROPOSAL_2026-07-23.md`, and (if it landed) `KALSHI_CAPITAL_ACCOUNTING_ROOTFIX_2026-07-23.md`.

3. **Verify live state yourself before believing the handoff (read-only):**
   ```
   KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"; VPS="ubuntu@18.201.216.0"
   python kalshi_live/kalshi_status_readonly.py ; python kalshi_live/kalshi_delta_check.py
   ssh -i "$KEY" $VPS 'sudo md5sum /opt/pa2-maker-kalshi-live/maker_kalshi_quoter.py'   # expect 727ca7c59840a42b51c19e24c65a0982
   ssh -i "$KEY" $VPS 'sudo sha256sum /opt/pa2-maker-kalshi-live/live.env'              # expect 4092ac26a5a61dfca7edf8657e7eb6e812e94ce4ce082330f4b803e2a6386303
   ssh -i "$KEY" $VPS 'sudo ls /opt/pa2-maker-kalshi-live/STOP; systemctl is-active polymarket-maker-kalshi-live.timer'
   ssh -i "$KEY" $VPS 'sudo python3 -c "import json;print(json.load(open(\"/opt/pa2-maker-kalshi-live/quoter_state.json\")).get(\"equity_day_start\"))"'  # expect ~247.54
   ```

**⚠⚠ THREE THINGS THAT WILL BITE YOU IF YOU MISS THEM:**

1. **BRANCH HEAD ≠ DEPLOYED. Do NOT deploy HEAD blindly.** Deployed quoter = `727ca7c5…` (ran all
   day, unchanged). Branch HEAD quoter = `9a24f605…` because this session COMMITTED code fixes that
   are NOT deployed and NOT all deploy-ready. Specifically: the loss-meter fix needs an equity-jump
   clamp first (task #13), the ledger fix has 4 preconditions (task #11), and the categorical guard
   fails OPEN not safe. `git show HEAD:file | md5sum` will NOT match the box — that is correct and
   expected. Any deploy is a per-file, md5-gated, reviewed decision, never `deploy HEAD`.

2. **BOTH CAPITAL BRAKES ARE INERT.** `HELD_MAX_USD=100` and `MAX_TOTAL_CAPITAL=250` both sit ABOVE
   account value (~$215). The ONLY live risk brake is `DAILY_LOSS_HALT_USD=40` (trips at $207.54).
   This is a deliberate bridge until the capital root-fix ships. **If the operator moves money, you
   MUST re-baseline the loss meter** (clear `equity_day`/`equity_day_start` from `quoter_state.json`)
   or the deposit reads as profit and loosens the only brake.

3. **The capital root-fix dive (`wef4ikfcf`) may still be running or just finished.** Check
   `/workflows` or read `docs/maker_handoffs/KALSHI_CAPITAL_ACCOUNTING_ROOTFIX_2026-07-23.md`. It is
   the fix for the cap-raising treadmill and the inert-brake state — the highest-value open thread.

**FIRST TASKS, in priority order (from EOD handoff §7):**

1. **Read the capital root-fix output** when it lands. It's the real fix for why the cap got raised
   4× today (the committed guard counts GROSS; paired/binary exposure barely uses net capital).
   Likely proposes net accounting + a per-event cap. CODE change → full ship discipline.
2. **Rank the footprint by REWARD DENSITY, not `usd_day` (volume).** The one lever all 4 dives point
   at: 74.7% of quotable contracts pay $0, and we widened the allowlist to 14 series today.
3. **2026-07-27T04:00Z: re-export the transaction CSV**, recompute per-family net-of-FULL-rewards
   (credits lag one Time Period — §M13), then decide temp. **Do NOT cut `KXTEMP*` before this** — it
   is 91% of reward income and the −$13.06 "temp loss" was a withdrawn partial ledger.
4. **Watch `KXTRUMPENDORSEMENTS`** (added today): A-prefixed strikes silently disable its ladder
   pairing (task #12), and it's the highest-toxicity shape on the allowlist. Consider removing if it
   accumulates naked.

**HARD RULES (unchanged):**
* Kalshi venue ONLY. Never touch `claude/maker-bot`, MB/WB/EB/SB, or shared modules. Commit only on
  `claude/maker-kalshi-live` via your worktree.
* Bot is LIVE + trading real money. Stays live unless a guard halts it or the operator says otherwise.
* NEVER deploy without md5-gating the specific artifact and telling the operator the hash.
* NEVER quote the ledger's `rewards_residual` (garbage). NEVER quote a number that isn't from a cited
  working source (operator rule: only not-factual/guessed numbers are banned — dollars are fine when
  sourced). Trading model is BINDING: rewards pay for resting QUOTES; fills are a COST; flatten is
  MAKER-FIRST; taker is a genuine last resort (`TAKER_FLATTEN=0` — kept off, it de-hedges live pairs
  when on; see EOD §2).

**METHOD (earned the hard way — this session self-caught 6 measurement bugs and reversed 3 live
calls):** Measure before claiming. If a number looks impossible, it IS wrong — find the bug, don't
explain it away. Pin every fix against the pre-fix build. The operator moved fast today and the
research caught up and corrected several calls — the posture now is: read the root-fix, rank by
reward density, stop tuning knobs, and decide temp at 07-27. Report honestly; verify the operator's
premises before building on them (three were measured false today).

---
