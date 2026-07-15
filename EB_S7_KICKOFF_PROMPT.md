# EB Session-7 Kickoff Prompt (paste everything below the line into a new session)

---

**EsportsBot sharp-line rebuild, session 7 — run the WHOLE session from this
prompt and STAY IN LANE. The plan is already decided; your job is to execute
it, not improve it.**

**Setup:** `git checkout claude/esports-sharp-line-rebuild-gqy1na && git pull`.
Read `EB_SHARP_LINE_NEXT_SESSION.md` §0-S7 (top) → §0-S6f, then `CLAUDE.md`.
Verify `git branch --show-current` before ANY write (WB shares this checkout).
If LOCAL on the operator's Windows box: SSH key
`C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem` → `ubuntu@18.201.216.0`
works directly (read-only checks + scp fine). If CLOUD: relay VPS commands via
the operator; ops scripts ship as `deploy/vps/eb_*.sh` + one curl|md5sum -c|bash
line, md5 from the GIT BLOB (`git show HEAD:<file> | md5sum`), never the CRLF
working copy. NEVER run the collector manually (PinnOdds quota).

**THE LANE (do these, in this order, and NOTHING else):**

**STEP 0 — verify, don't assume (~10 min, read-only):** branch correct;
`tail -3 collect.log` shows `appended>0 pm_matched≈books dur<10s`; deployed
collector md5 == `git show HEAD:deploy/vps/collect_pinnodds_standalone.py |
md5sum`; spot-check the newest tick's marquee matches BY NAME (aggregate
pm_matched health does NOT prove marquee coverage — the 07-14 outage lesson).
Run the related test suite; expect ~670 green. Anything broken → fix THAT
first (root cause, one fix per commit, full related suite GREEN before every
commit), then continue.

**STEP 1 — critical path, not time-gated:** ask the operator whether the
PinnOdds vendor email went out / got a reply (historical esports odds? how far
back + closing odds included? limits + price? SAMPLE BEFORE PAYING).
- Reply = YES with usable sample → BUILD the historical readout (this is
  pre-authorized): enumerate resolved PM esports match-winner markets via
  gamma (closed=true, /events pattern), pull CLOB
  `/prices-history?market=<token_id>&interval=max&fidelity=60` (VERIFIED
  serving resolved markets), match to vendor historical closing odds, run
  through the EXISTING audited backtest (frozen orientation, Shin, touch-fill
  where possible; history is hourly MIDs → apply the measured 0.5–1.5pt
  slippage haircut and SAY SO). Judge by the §0-S7 go-criteria. This can
  deliver the verdict in one session.
- No reply / NO → skip; hand the operator the 3-question email text if not
  yet sent, and move on.

**STEP 2 — time-gated PRIMARY (only if 2026-07-20+ and the 07-15..19 slate
has resolved):** have `pip3 install shin` run on the VPS (propose the exact
line; operator or direct-SSH executes), re-pull a fresh md5-verified snapshot
backup to `data/backups/`, then run the audit one-liner
(`deploy/vps/eb_label_audit.sh`, re-clones HEAD). Judge BOTH de-vig methods
against the PRE-REGISTERED go-criteria — ROI>0 with 95% CI excluding zero,
≥100 settled flat-stake bets, not one-bucket-concentrated; n<50 prints
UNSTABLE = report, do NOT act. Focus the 2–5pt gap band per-bucket table,
fill coverage, and capacity lines.
- POSITIVE under Shin at the touch → STOP. Report it. The settlement/void
  ("panda") cross-check now becomes REQUIRED before believing any win —

  propose it as the next session's work. NO un-halt talk, NO config changes,
  NO sizing — operator decisions.
- NEGATIVE/flat → report it plainly. The taker strategy is dead at our speed;
  the pivot options (uncrowded corners / maker / settlement) are §0-S7-chat
  context — PROPOSE, never build unsolicited.

**STEP 3 — only if neither Step 1 nor Step 2 is actionable:** maintenance
only — health checks, backup re-pull if the slate finished, remind operator
re key rotation (`PANDASCORE_API_KEY`+`PINNACLE_ODDS_API_KEY`, chat-exposed;
shared env → operator executes). Then STOP and close the session with a
short handoff delta in §0-S7. Do NOT invent work.

**OUT OF LANE (refuse, flag to operator instead):** new features or analyses
not named above; threshold/config changes (the 2607765 sweep stays
EXPLORATORY until the readout passes); touching MB/WB files or shared runtime
infra (propose-only); acting on any n<50 number; deploying the trading bot
(EB stays HALTED — odds/results crons are not bot deploys); "while I'm here"
refactors. If something genuinely urgent appears outside the lane (data loss,
collector outage class), fix the minimum to protect data, document, continue.

**DISCIPLINE:** correct-or-absent everywhere (doubt → None, never a wrong
bool); numbers only from cited canonical sources with UNSTABLE labels at
n<50; one fix per commit; full related suite green BEFORE commit (the
62c3677 lesson); commit+push each step; update §0-S7→§0-S8 handoff at close.
