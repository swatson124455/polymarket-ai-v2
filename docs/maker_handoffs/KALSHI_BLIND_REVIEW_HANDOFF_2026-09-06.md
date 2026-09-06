# KALSHI MAKER — BLIND REVIEW HANDOFF (2026-09-06 ~02:4xZ)

Operator wants a FRESH session to blindly review the work this session shipped. Verify
against primary sources; assume nothing in this doc is correct until you re-check it.

## SCOPE OF THE REVIEW
Adversarially review, from scratch, everything below. Confirm or refute each claim by
your OWN reads (venue API, on-box files under `sudo -n`, deployed-vs-repo md5, code
behavior). Do NOT start the bot. Do NOT clear STOP. Read-only + your own verification.

## STATE AT HANDOFF (this session's reads — RE-VERIFY, do not trust)
- Bot OFF: `polymarket-maker-kalshi-ws` inactive (02:46:03Z). STOP sentinel PRESENT
  (`/opt/pa2-maker-kalshi-live/STOP`, from the 09-01 18:35Z auto-halt; never cleared).
- Account: $295.4119 cash, 0 resting, 0 positions (cash-feed 02:41:44Z).
- Credited: lifetime $205.85; only row since 08-16 = $0.79 August INTEREST (not a reward).
- Branch `claude/maker-kalshi-live` HEAD `fd659d8` pushed. ⚠ Worktree `.claude/worktrees/
  kalshi-live` LOST its git linkage (Windows erosion); work continued in
  `.claude/worktrees/kalshi-relink`. Verify `git branch --show-current` before any write.

## WHAT THIS SESSION DID (each is a claim to verify or refute)
1. **Live GO 09-01 17:46Z then wind-down** — first-ever GO; realized **−$19.95** on the
   ~49-min window (venue fills reconciled to the cent: one $11.91 halt event on near-money
   daily KXAAAGASDCA-5.7050 + ~$8 fast churn on 4 markets). Auto-halt fired 18:35:37Z at
   −$16.29 > $10. Same window ACCRUED +$0.6234 (est-feed). CLAIM: loss real, engine earns.
2. **Root cause = size on unproven near-money market + no receipts feedback in picker.**
3. **Rails applied to live.env** (verify each vs `kalshi_rails_manifest.json`, 25 keys):
   D3_NEWSERIES_MAX_RUNG=2 (25ct cap on zero-credit series), MID_BAND_OUT=0.20,0.80,
   SERIES_MAX_USD=200 + SERIES_PCT=0, plus fix-sheet F1-F5 (EXIT_CHEAP_CROSS_USD=0.05,
   STRAND_CROSS_S=60, MAX_TOTAL_CAPITAL=240, MAX_PRICE_DOLLARS=0.985,
   WIDEBOOK_MIN_SPREAD_TICKS=8). Backups: .bak-FAMCAP / .bak-RAILS / .bak-FIXSHEET.
4. **B4 canon RESOLVED** (`KALSHI_B4_RECONCILIATION_STUDY_2026-09-01.md`): venue two-sided-
   Target exclusion rule is OPERATIVE; the old "sub-Target accrues" claim = stale-census
   artifact. Verify the killer row (KXYTVIEWSW-YOU: 2,100ct single side + resting legs =
   0cc across the tape).
5. **Money adversarial review** (`KALSHI_MONEY_ADVERSARIAL_REVIEW_2026-09-01.md`): churn
   trigger DIAGNOSED as the strand-cross stack (STRAND_CROSS_S + EXIT_CHEAP_CROSS_USD
   lanes; size-blind above STOP_TAKER_MIN_CT=5). Verify against the code.
6. **Anti-loss safeguard suite** (the main thing to review):
   - `kalshi_preflight.py` — hard PASS/FAIL gate (sudo-safe STOP, 25-key manifest,
     config-change guard, fresh account, dry selection). Exit 1 on any FAIL.
   - `kalshi_fill_watch.py` — first-hours read-only ALARM per fill.
   - `kalshi_safe_start.sh` — the sanctioned start: preflight-gated, snapshots
     `live.env.last_started`, arms the watch.
   - `kalshi_rails_manifest.json` — 25 operator-approved keys w/ why-cites.
   - `kalshi_accrual_monitor.py` — read-only est-feed dashboard.
   VERIFY: deployed md5 == repo blob md5 (CR-stripped) for all 5.
7. **This session's OWN adversarial review of the safeguards found 5 holes** — RE-DERIVE
   them independently; do not just re-read them:
   - A1: fill-watch is fire-and-forget (`nohup … &`) — safe_start prints "armed" even if
     the watcher dies. HIGH.
   - A2: nothing forces safe_start; bare `systemctl start` bypasses preflight entirely.
   - A3: config-change guard is a WARN not a FAIL — doesn't block; "ramp-floor first
     session" is text, NOT enforced in code.
   - A4: alarm is log-only (no stop, no page), quits after 2h.
   - A5: preflight dry-selection is a different instant than the real first cycle.
   Proposed bundle (NOT built): A1 verify-PID, A2 systemd ExecStartPre gate, A3a
   FAIL-unless-ack, A4ii continuous watch, A5 relabel. Held for operator: A3b ramp-floor
   code, A4i auto-STOP watcher.

## SESSION-SELF-CAUGHT DEFECTS (verify these were real, and that the lesson holds)
- Recommended GO with the size-cap rail OFF though the review named it → −$19.95
  (feedback_arm_prevention_rails_before_go.md).
- Reported "no STOP file" TWICE via a non-sudo ls against a root-only dir (blind check);
  STOP was there the whole time (feedback_sudo_blind_existence_checks.md).
- "one market did almost all of it" was wrong — 60% halt event / 40% churn.

## PRIMARY SOURCES FOR THE REVIEWER
- Code (repo = deployed blob `a039f749` for the quoter): kalshi_live/maker_kalshi_quoter.py.
- Canon: KALSHI_R3_OFFICIAL_RULES_2026-08-25.md; dollar-cliff canon; fee-formula canon.
- Docs this session: KALSHI_SELECTION_REVIEW_AND_ALLOCATOR / _ACDG_TRIPLE_BLIND_REVIEW /
  _B4_RECONCILIATION_STUDY / _MONEY_ADVERSARIAL_REVIEW / _ALLOCATOR_V1_SPEC (all 2026-09-01).
- VPS: `KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"; ssh -i "$KEY"
  ubuntu@18.201.216.0`; live dir root-only → `sudo -n`. Venue GETs only; NO order calls.

## OPEN OPERATOR DECISIONS (do not act — list for the reviewer's awareness)
STOP clear · start (via safe_start only) · halt-$10 stance · state-daily 25ct clamp stance
· D9 stop-clock re-base · safeguard bundle A1/A2/A3a/A4ii/A5 · A3b + A4i · allocator +
underlying-map PRs.

## THE ONE RULE FOR THIS REVIEW
Every claim above is THIS session's — treat it as untrusted. Re-measure. If a number,
md5, or behavior doesn't reproduce, that IS the finding. Report confirmed / refuted /
can't-verify per item, plus any new hole this session missed. Nothing goes live.
