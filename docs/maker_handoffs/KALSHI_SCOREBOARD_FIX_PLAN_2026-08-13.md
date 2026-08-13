# SCOREBOARD FIX PLAN — resolve all 10 review findings + 2 session observation defects
Status: EXECUTED IN FULL (operator "proceed", 2026-08-13 ~00:3xZ). All 10 findings fixed in commits `262b6db`..`4834832` (one per finding, suite 1320/2 exit 0 after each); deployed 00:30:53Z md5 scoreboard `660fc479` / reward_pnl `55545585` = git blobs; real runs both exit 0, scoreboard row 00:31:02Z identity_gap 0.0 funded-basis, drag −$21.9397 (final through the 19:50Z stop), credit buckets all-zero (F1 semantics correct); negative test dead-recorder → exit 3 with row written. S1 tape-compress fixed on box: `-exec gzip -9 {} +` (backup .bak-GZEXIT-*), real run exit 0, negative test + → 1 vs ; → 0. S2 resolved by F3's min_ts. Authored 2026-08-13 ~00:2xZ, HEAD `fc266db`.
Scope: `kalshi_live/kalshi_window_scoreboard.py` (+tests), one small exported helper in
`kalshi_live/reward_pnl_report.py`, one systemd wrapper. Bot is WOUND DOWN (§0-A) — every fix
is observation-layer only; nothing touches trading code or the frozen state.

Deadline logic: F1/F2/F8 change what the daily gauge and the 08-19 §5 scoring read — land
before the next 07:40Z scoreboard run if possible, hard-before 2026-08-19. The rest have no
date pressure (bot down; truncation cliffs are months away at zero fill growth).

## The 10 findings → 10 surgical commits (one fix per commit, suite green after each)

**F1 — lifetime-credit inflation (CONFIRMED, verdict-relevant).**
Count only credit rows with `T0 <= created_at <= OBS_DEADLINE`, grouped per event from the RAW
rows, THEN bucket by program end_date. Removes the lifetime-paid shortcut through
`credits_by_event`. Test: event with a pre-T0 credit + in-window end counts only the post-T0
credit. (This also implements F8's deadline cap — one mechanism, two findings.)

**F2 — pass_now abs() inversion (CONFIRMED, verdict-relevant).**
`pass_now = counted + drag_total > 0` (identical to "credits > |drag|" when drag is a loss;
correct when the window nets a trading profit). Tests: both drag signs.

**F8 — OBS_DEADLINE unenforced (CONFIRMED, verdict-relevant).**
Credit side solved by F1's created_at cap. Add `window_state: "open" | "post_window" |
"concluded"` to the row; after OBS_DEADLINE print "window concluded" and stamp rows so a later
reader can't mistake a stale `pass_now` for a live gauge. Timer stays (operator may name its
removal after 08-21 — not in this plan).

**F3 — fills 10k truncation cliff + dropped recon self-check (CONFIRMED, latent).**
Root fix at the artifact's own altitude: fetch fills and settlements with `min_ts = T0`
(prod-verified param, `maker_kalshi_client.py:437-462`) — valid because flat-at-T0 is §10-C
ESTABLISHED, so the T0-anchored tape IS the full position history for this window. Kills the
cliff and the unbounded daily refetch (efficiency finding) in one line each. Belt-and-braces:
alarm (nonzero exit) if pagination consumes 50 pages with a live cursor, and compare replayed
end-positions vs `/portfolio/positions` on unsettled tickers (the ledger's recon check,
reused) — mismatch → nonzero exit.

**F4 — credit_history limit=1000 unguarded (CONFIRMED, latent).**
Assert `len(credits) < 1000`; breach → nonzero exit naming truncation. (Pagination upgrade in
the client is a shared-module change — out of this plan, noted for the client's owner list.)

**F5 — naive-timestamp TypeError (PLAUSIBLE).**
Normalize in ONE place: `reward_pnl_report.parse_iso` gains naive→UTC attachment (venue is
UTC); scoreboard imports it (see F10). Broaden the scoreboard's excepts to
`(ValueError, TypeError)` with drop-counters (see F7). Tests: naive end_date, naive
created_at, bare date.

**F6 — three uncoordinated event derivations (PLAUSIBLE).**
Export one `ticker_to_event()` from reward_pnl_report; scoreboard imports it. Safety net
regardless of derivation quality: any credit row with paid > 0, created_at >= T0, landing
unmapped → counted in a new `unmapped_inwindow_usd` row field + nonzero exit (in-window money
the gauge cannot attribute is exactly the alarm case).

**F7 — silent/exit-0 alarm paths (CONFIRMED).**
Adopt the lane convention (alarms → nonzero exit, §13): `no_ts > 0` → exit 3 (no benign
cause); `cash_now is None` → WARNING + exit 3 (self-check dead); dropped credit rows
(missing/unparseable created_at) → row counter + exit 3 if nonzero; identity gap > $2 →
exit 2, with a documented one-shot ack file (`SCOREBOARD_GAP_ACK`) the operator touches after
a named deposit so a known money movement doesn't flap the unit.

**F9 — identity check basis + staleness (PLAUSIBLE, moot while wound down).**
Use `funded_cash` when present (falls back `cash + resting_reservation`, then `cash`), record
which basis was used in the row; include `cash_age_s` and require < 600s for the gap check
(else WARNING + exit 3 as a dead-recorder signal per F7).

**F10 — duplication (CONFIRMED, reuse).**
Delete the scoreboard's local `parse_iso`; import from reward_pnl_report (post-F5). Keep
`event_end_map` but built on the imported `ticker_to_event`. The wider 10-copies-of-parse_iso
cleanup across kalshi_live/ is a separate consolidation task — listed, not bundled (Rule:
one fix per commit; touching 9 other deployed scripts needs its own review).

## The 2 session observation defects (same alarm class, operator-optional)

**S1 — tape-compress gzip-exit mask** (`find -exec gzip \;` swallows gzip failures): replace
ExecStart with a 3-line wrapper script that propagates the worst exit, same pattern as the
w16 fix, backup + negative test. Cost ~10 min. Consequence today is only a delayed
compression retry — include or skip at your word.

**S2 — settlements/fills daily refetch growth**: resolved by F3's `min_ts=T0` (no separate
work). Listed so the efficiency finding has a named disposition, not a silent drop.

## Execution protocol (when named)
1. One commit per item above, `pytest kalshi_live` green after each (exit code captured).
2. Deploy = scp the 2 changed .py files, `tr -d '\r'`, md5-vs-git-blob verify, run the
   scoreboard unit once for real, capture exit code + row, negative-test one alarm path
   (temporarily point KALSHI_DATA_DIR at a dir with no cash files → expect exit 3).
   reward_pnl_report redeploy also re-runs its unit once (exit code) — its timer next fires
   07:30Z.
3. Handoff §0-A appendix + memory updated with new md5s; ReportFindings re-called with
   outcomes.

Estimated effort: ~2–3h total. Out of scope (unchanged, parked on their named triggers):
EST_FEED+guards, Proposal A/B, scale rungs, 9a/9b, halt-meter basis, cfg_stamp, defects 13/14
naming, WB test failures (other lane).
