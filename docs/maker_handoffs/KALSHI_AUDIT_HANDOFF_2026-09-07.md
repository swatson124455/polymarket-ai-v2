# KALSHI MAKER — AUDIT HANDOFF (2026-09-07)

Operator's order for the next session, verbatim intent: **this session was largely
useless and error-prone; do NOT trust its conclusions or its "fixes." Audit every gap,
logic error, and bug it introduced or missed, from scratch, and produce a REAL fix.**
The session's own self-caught errors are listed below as starting leads — they are NOT
the full list, and the audit must not assume they are.

Treat this doc as a map of where to look, not as truth. Every claim below is this
session's and is UNTRUSTED. Re-derive from primary sources (venue API + on-box `sudo -n`
reads + code + git blobs).

## LIVE STATE AT HANDOFF (re-verify)
- Bot **durably OFF**: `polymarket-maker-kalshi-ws`, `-fillwatch`, `-allocator.timer` all
  inactive AND disabled (15:43Z). STOP sentinel written ("HELD OFF by operator 2026-09-07").
- Account (venue read 15:34:25Z): **$294.1427, 0 positions, 0 orders.**
- **Zero trades ever executed under the allocator** — it never quoted a live order this
  build. All this session's allocator/gate work is UNVALIDATED against real fills.
- Branch `claude/maker-kalshi-live`, HEAD `0d56d404` pushed. Deployed md5s at handoff:
  kalshi_allocator.py `a1d2636d` · maker_kalshi_quoter.py `273578bf` · kalshi_preflight.py
  `185243f0` (repo-tree) — but the DEPLOYED preflight on box may differ from repo (the
  session pushed preflight edits mid-stream; VERIFY deployed==blob per file before trusting).

## THE OPEN QUESTION THE SESSION NEVER ANSWERED (the actual task)
"Can this account earn Kalshi maker rewards at $294 capital, and if so where?" It is
UNANSWERED. The blocking mechanism the session *believes* (UNVERIFIED — audit it):
reward = share × pool; on the gas-family books rival depth is ~1,000-4,700ct, so at any
size $294 affords (~25-100ct) our share is ~1-5%; the $1/period floor then pays $0 on
small share. The session's own "size up to 100ct" idea was rejected by the operator as a
BANDAID because it justifies real capital risk (~$98 naked-settlement tail/market) using
the replica capture model that this project has measured 2-43x optimistic. **The real fix
was NOT found.** The session was mid-measurement (family depth distribution — does a THIN
qualifying book exist where our affordable size is a large share?) when halted. That
measurement is the right next step; the answer decides between "select different markets"
vs "this scale cannot work" vs "more capital." Do it from scratch; do not trust the
session's half-run probe.

## SELF-CAUGHT ERRORS THIS SESSION (starting leads — NOT exhaustive; verify each)
1. **`orderbook_fp` canon trap** — the session's own book-readers (preflight dry-selection
   AND allocator) parsed the legacy `orderbook` key, which returns EMPTY; venue depth is
   under `orderbook_fp` (yes_dollars/no_dollars). This produced a FALSE "161/161 family
   books empty" claim that was reported to the operator, then retracted. Audit: are there
   OTHER readers in the codebase with the same wrong key? (memory already warns of this
   exact trap — it was ignored.)
2. **Fabricated "unexplained 25ct bug"** — the session told the operator a proven series
   "still showed 25ct, unexplained" while the plan FILE showed it at 51ct; it had read
   console row 1 (an unproven series at 25ct) and never read row 2. No bug existed. Audit
   every "unexplained"/"can't-verify" claim this session made for the same not-reading-own-
   output failure.
3. **Over-narrowed plan starves the quoter** — DAILY_ENTRIES_MAX=2 + series-diversity cut
   the allocator plan to 2 markets; if both get gate-refused the WHOLE bot quotes nothing
   (fail-closed file mode). The operator flagged "shouldn't stop all trading." The plan
   should be a BROAD ranked set so the quoter always has passing markets. LIKELY REAL BUG.
4. **Allocator vs capture-gate model conflict** — allocator selected deep books by an
   INHERITED measured rate that the W2 capture gate (replica model) then refused at clamped
   size. Two different reward models gate the same decision and disagree ~5-9x. Which (if
   either) predicts credited $ is UNKNOWN — never validated against a credit. Audit whether
   the allocator should rank on the SAME model the gate enforces (achievable share × pool
   at affordable size), so it never plans markets the gate will reject.
5. **Timer birth-time thrash** — allocator cadence was wrong twice (04:10 vs the venue's
   ~14:02Z weekly program birth); ended at 15-min. Audit the cadence vs actual program
   birth times, and whether "empty universe → empty plan → dark" is handled sanely.
6. **W2 effective-size gate ↔ D3 ramp cold-start** — the session identified but did NOT
   resolve: a fresh ticker at ramp rung-0 (5ct) is judged by W2 at 5ct → refused → never
   rests → ramp never advances. Catch-22. The session reverted its attempted fix. Real,
   open.
7. **Inherited rate basis is a single 3.4h floored-session window (5ct)** — the entire
   allocator projection for un-credited series rests on one small measurement the session
   itself flagged as thin; DILUTION_BUFFER 0.25 and DIESELW 0.0343 cc/min/ct are session-
   derived, unvalidated. Audit whether ANY of the allocator's projections are trustworthy.

## BROADER AUDIT SCOPE (operator: "dozens of examples")
- Every code change this session shipped (git log `claude/maker-kalshi-live` since
  `45b5096`): W2 gate, allocator v1, footprint file mode, near-money clamp, ramp-floor,
  the safeguard suite (preflight/fill-watch/safe_start/systemd units). Each was written
  and self-reviewed by the same session now judged unreliable — re-review adversarially.
- Deployed-vs-repo md5 for ALL of them (the session deployed from the wrong cwd at least
  once — a deploy may have silently not landed).
- The safeguard suite's OWN blind-review holes (A1-A5, N1-N5, W1-W2) — confirm the shipped
  fixes actually hold, not just that tests pass (the session's tests passed while its live
  behavior was wrong repeatedly).
- Test suite: 1510 pass / 2 xfail claimed — but green pins coexisted with broken live
  behavior all session. Audit whether the pins actually assert real behavior or tautologies.

## WHAT IS PROBABLY SOUND (still verify)
- The machine safety rails FIRED correctly when tested: the $10 daily-loss halt fired live
  09-01 (−$19.95 window) and 09-06 (carried-drawdown re-halt); flatten + STOP machinery
  worked. The layered worst-case sheet (KALSHI_WORST_CASE_SHEET_2026-09-06.md) bounded real
  losses to the single-digit-dollar class all of 09-06/07. Losses were rail-bounded, not
  runaway — but ZERO rewards were earned to offset even those.

## PRIMARY SOURCES
- Code: `kalshi_live/` on branch `claude/maker-kalshi-live`.
- Canon: KALSHI_R3_OFFICIAL_RULES_2026-08-25.md; dollar-cliff canon; fee-formula canon;
  KALSHI_ALLOCATOR_V1_SPEC_2026-09-01.md (the spec the allocator was supposed to implement
  — check the build against it).
- Blind-review ledger this session built: memory project_kalshi_blind_review_20260906.md.
- VPS: `KEY=... ssh ubuntu@18.201.216.0`; live dir root-only → `sudo -n`; venue GETs only,
  NO order calls, do not clear STOP or start anything without operator GO.
