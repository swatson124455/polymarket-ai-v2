# MirrorBot Rebuild — Living State / Handoff (docs/MB_STATE.md)

> **⚠ BRANCH-VERSIONED DOCUMENT — the copy you are reading may be stale.**
> This doc advances on session branches; master's copy lags until the
> end-of-session docs-sync PR merges. Before trusting ANY fact here, find
> the newest copy: `git ls-remote origin 'refs/heads/claude/*'` then
> compare `Last updated` lines via
> `git fetch origin <branch> && git show FETCH_HEAD:docs/MB_STATE.md | head -5`.
> Newest wins. (Protocol: CLAUDE.md "STATE DOCS ARE BRANCH-VERSIONED";
> 2026-07-11 incident: a fresh session read master's stale copy and
> recommended the BANNED circular validate rerun it found there.)

**Last updated:** 2026-08-21 (bidsim AMENDMENT 1 deployed + scout sweep #1 CLOSED; read the 2026-08-21 block first) · *(prior: 2026-08-20 session close — forward-data-only rule codified; four forward instruments live (band test n=11 e=0.717, bidsim 13/13 fills, cohort4/5, fbfd probe); RTDS gap 3.0%; scout sweep 3 REJECT + 6 running; final plan = let the instruments vote, one scoreboard build allowed. HANDOFF: read the 2026-08-20 SESSION CLOSE block first)* · **Branch:** `claude/repo-setup-docs-fq9bhn` (head = this commit)
**Read first:** `CLAUDE.md` (binding directives), then this file, then **`docs/MB_COPYTRADER_CONTEXT.md` (FULL context brief for the live copy-trader investigation — the complete reasoning chain, API gotchas, and decision tree)**. `MB_REBUILD_PLAN.md` holds the older plan + operator decisions.
**Protocol for updating this file:** `docs/MB_HANDOFF_PROTOCOL.md`.

---

## 0. IMMEDIATE RESUME (read this block first)

> ## 2026-08-25 (~20:10Z) — ALL NINE RULINGS EXECUTED ("go with rec 1-9")
>
> **1 LEGACY WOUND DOWN:** final canonical P&L (bot_pnl 2160h) +
> mirror_rejected_signals archived (1.1GB pg_dump) to
> /opt/pa2-backups/mb_evidence/ (NOTE: the 1.1GB archive is same-box only -
> the nightly bundle carries the small tier; a one-time manual pull of the
> big dump is open); `systemctl stop + disable polymarket-mirror` at
> ~20:04Z; mirror3/weather/esports/ingestion all verified active. The 8
> ~$1 legacy paper positions freeze in the DB as-is. Legacy code
> disposition (removal/archive) proceeds via the ledger sign-offs.
> **2 ITEM #21 DONE:** shadow_fills excluded from prune_old_data (retention
> map + DELETE query + CLI choice together - a partial removal would have
> KeyError'd the nightly run); deployed into the live release
> (.pre-item21 backup), dry-run verified: 'all' = 3 tables, count 9,519
> and protected.
> **3 FILL-SIDE RULED** (BIDSIM_DESIGN Amendment 2): strict chain-truth
> taker-SELL headline + any-print bracket; maker wins only if BOTH clear
> 74%; default chase.
> **4+5 RE-REGISTRATION LIVE** (docs/COHORT5_REREG_AND_PROBES_AMENDMENT.md):
> the 15 unconsumed original looks + the 12 INSUFFICIENT probes graded by
> the unified e-process (fresh epoch 2026-08-25T18:00:00Z - fresh because
> their diagnostics were visible; the 9 C1 keep 08-24T17:00Z; 5 consumed
> locks immutable). ROSTER 31->43 (probe group 13; backup
> chain_audit.json.pre-probes12-20260825); watcher restarted 20:01:01Z,
> 0 errors, dedup-seeded (restarts no longer corrupt first_buy).
> **6 SCOUT SWEEP #2 RUNNING:** 82 human-scale candidates (fixed band),
> launched 20:05:56Z (`/tmp/scout_sweep2_main.log`, out
> deep_dive_scout2/); serial ETA days. Repo copy scripts/vps_jobs/
> scout_queue3.sh. Hit the /tmp protected_regular landmine AGAIN - fresh
> filename roster2 is the fix (recorded).
> **7 CLOUD LEG DORMANT-READY:** mb_evidence_s3_sync.sh chained into the
> nightly backup; activates when the operator creates /root/.mb_backup_s3
> (steps in the script header) + installs awscli. Until then the Windows
> pull remains the off-box leg.
> **8 PR #5 MERGED to master** (docs sync). PR #6 remains open pending the
> operator's eyeball of the shared RedisCache commit. NOTE: master's MB
> docs are the 08-21 snapshot - a fresh sync PR is the next session-close
> duty.
> **9 CONVICTION RETIRED** from the watcher (both paths + median seeding;
> sizing.py kept as tested library; ledger #30 EXECUTED). AST-level
> no-live-reference check performed because the network loops are
> untested - it caught my own first patch attempt failing to write.
>
> ## 2026-08-25 (~17:05Z) — HYGIENE FIX BATCHES A/B/C/E EXECUTED + D RATIFIED
> (operator "proceed with recs"; commits d95027e, e335d9b, 72448ab, 2086513)
>
> **A canon-alignment (verdict pipeline):** repair_record gains
> PRICE_NO_UPSIDE parity in evaluate_gates' exact order (charter-RESTORING;
> amendment in BAND_PREREGISTRATION; error direction while it stood =
> conservative); merge_outcomes now routes through mb_canon.merge_labels
> (conflicts EXCLUDED + printed, silent DB-wins retired at the live joins);
> cohort5 eligibility: re-review verdict VETOES base dir + unreadable files
> warn loudly (post-fix eligible count unchanged at 20 = the defect was
> latent); fee-map loads refuse corrupt/empty; fee-source counters printed.
> **B ops:** scoreboard band line >26h old now reads STALE (never served as
> current); cron band stage keeps tracebacks (grep filter removed).
> **C watcher recording integrity (deployed 17:03:19Z, backup
> copy_watcher.py.pre-batchc-20260825):** quote_book -> (bid, ask,
> quote_err); records carry quote_error + one_sided_book; block_ts fallback
> stamps block_ts_est; cursor-init RPC wrapped; **first-buy dedup persists:
> seeded 4,724 pairs (chain) + 3,660 (rtds) from the sinks at boot — the
> 16.4% restart artifact is CLOSED forward.** 0 errors since restart;
> cohort5 + scoreboard verified end-to-end as cron user.
> **D RATIFIED:** v3 verdict field satisfies the Decision-5 rejection-
> logging precondition — legacy wind-down ELIGIBLE, execution awaits the
> explicit sign-off (#1 on the ledger); legacy defect fixes stay frozen
> until that ruling. **E:** ledger items 29-35 appended.
> NOTE: bidsim at 83 resolved of ~100 — the chase-vs-post tripwire is
> ~1 day out; fill-side ruling still OPEN ahead of it.
>
> ## 2026-08-25 (~16:45Z) — HYGIENE REVIEW: MEASUREMENT BATTERY + ADVERSARIAL
> RE-VERIFICATION + OPERATOR RULINGS ("ok to all recs")
>
> **HEADLINE CORRECTION (the review catching itself):** the interim finding
> "the spread gate rejects +0.0587 edge" was an ARTIFACT of grading rejects
> at the whale price (unattainable by construction). Re-graded at the
> RECORDED best_ask (the real crossing price at detection): SPREAD_TOO_WIDE
> = **-0.1096, P(>0)=0.000 (253 labeled mkts)**; PRICE_RAN_AWAY = -0.0188.
> The finding was ALSO 50%-concentrated in 0x216509be (LOO all-negative).
> **The gates are doing their jobs. Withdrawn on the record.**
>
> **VERIFIED SURVIVORS of the battery (canon-graded, denominators inline):**
> * OK first-buys, whole roster: edge **-0.0024** on 2,411 labeled mkts of
>   4,550 OK first-buy records (label-timing selection caveat applies).
> * PRICE_NO_UPSIDE (0.98 cap): blocked buys graded -0.0265 despite 0.971
>   win rate (35 labeled mkts) - the cap makes money sense. KEEP (ruled).
> * Conviction/confidence carries no measurable signal in EITHER bot:
>   v3 conviction_r vs edge r=-0.013 (2,939 labeled fills; band-only
>   r=+0.055 n=234) - measured on restart-corrupted annotations, so verdict
>   = UNPROVEN not dead; legacy confidence split >=0.60 did WORSE than <0.60
>   (33.7%% vs 32.3%% WR, scripts/bot_pnl.py MirrorBot 168h). RULED: no
>   sizing built on conviction until measured clean.
> * Legacy funnel: ~1.57M rejections vs 794 trades / 7d
>   (mirror_rejected_signals counts); book = eight ~$1 positions (bot_pnl).
> * Stuck-position reframe: the ~678h position is in profit at the MARK
>   (entry .55, marked .68) but the real bid is .31 - the slippage guard
>   correctly refuses the fill; the defects are the forever-retry and the
>   misleading current_price mark.
> * Detect-lag tail (p99 27.5s) = recorder burst-queueing (lag>10s records
>   sit in 5s-windows with median 14 siblings vs 2 overall), NOT chain
>   slowness. RULED: no speed infra; record quote staleness instead.
>
> **"SECOND DEDUP BUG" RESOLVED - NOT A BUG:** journal retention starts
> 08-09 while the sink starts 07-13; the 347 "unexplained" dup first-buy
> flags cluster in the pre-journal era, 0 of 924 dup pairs have gap<60s,
> sink is pure chain records. The ONE known defect (memory-only dedup reset
> at restart) explains all 924. Dup rate remains real: 16.4%% of first-buy
> records (924 excess / 560 pairs) - the restart artifact is material to the
> estimand and dedup persistence is the fix path (still report-only).
>
> **RULINGS EXECUTED ("ok to all recs"):** (1) gates + 0.98 cap KEEP -
> recorded; (2) dedup root-cause DONE (above - no code change yet, fix
> awaits its own go); (3) conviction: no sizing on it, clean re-measure
> needs median persistence first; (4) **quote_ts/quote_lag_s SHIPPED**
> (both paths, recording-only, 66 pytest, deployed 2026-08-25T16:40:41Z,
> backup copy_watcher.py.pre-quotets-20260825; NOTE the deploy restart adds
> one more dedup-reset boundary - known artifact, disclosed).
> Pathway-walkthrough fleet (9 auditors + 9 verifiers) still running;
> full hygiene doc on landing.
>
> ## 2026-08-25 (~14:10Z) — STRESS CAMPAIGN: ALL ITEMS vs CANON DATA, BLINDLY
> (operator: "stress test all items with canon data blindly")
>
> **Positive runs (fresh /dev/urandom seeds, 3x sample):** seeds 4762895 +
> 14372946, each 25 records + 25 labels + 15 fees = **130/130 clean**;
> cumulative with the daily-seed run: **152/152 blind checks vs chain
> receipts / CLOB resolutions / venue fees, 0 alarms.**
> **Negative controls (the stick must be able to FAIL):** corrupted-sink copy
> (all 71,222 prices +0.05) -> **10/10 sampled records ALARMED**;
> flipped-labels copy (239,828 resolutions inverted) -> **10/10 sampled
> labels ALARMED**. 100%% detection both axes. canon_verify gained
> --sink/--gamma override flags for exactly such audits.
> **Fuzz:** 2,000 seeded random trials (seed 1599970607) on mb_canon +
> e_value - 0 invariant failures (bounds, ordering, pooling, empty->None,
> merge partition/conflict); e-process boundary hammer clean (all-positive
> edges e=25.2 crosses 20; all-negative 0.28).
> **Restore drills:** bundle untarred - locks/ledger md5-MATCH, sink
> restored 71,221/71,222 lines (append-only gap = expected);
> shadow_fills snapshot pg_restored into a scratch schema: **9,514/9,514
> rows**, schema dropped after. Pulled Windows bundle lists 95/95 entries.
> **Ledger blind spot-check** (seed 1712492766, 5 random claims re-grepped):
> 5/5 held. **Full lane pytest: 105 green.**
> **Honest notes:** (1) band_lock.json does not exist yet - CORRECT
> (created on first crossing); my ad-hoc drill printed a false MATCH for
> missing-vs-missing - drill flaw, not backup flaw, disclosed. (2) cohort5
> grading deliberately NOT re-run in the stress: 0xee00ba33 sat at 23/30 at
> 11:40Z and an unscheduled run risks consuming an original-20 single look.
> (3) /tmp/sf.sql left postgres-owned in /tmp (wiped on reboot).
>
> ## 2026-08-25 (~14:00Z) — OVERHAUL PROGRAM EXECUTED (operator 7-item
> directive; parallel agents authorized; "nothing but excellence")
>
> **THE CANON MEASURING STICK IS LIVE (TOP PRIORITY, item 4; 55b96a5).**
> `scripts/mb_canon.py` = THE estimand (per-market mean per the ratified band
> charter; venue-fee precedence chain; conflict-detecting label merge — the
> silent DB-wins merge is retired for new code). `scripts/canon_verify.py` =
> daily BLIND date-seeded verification of recorded data vs ground truth:
> shadow records re-derived from Polygon receipts, labels re-checked vs CLOB,
> fee map re-checked vs venue. **First live run 2026-08-25T13:49Z seed
> 20260825: records 8/8, labels 8/8, fees 6/6, ALARMS=0.** Wired into the
> 11:40Z cron; any mismatch prints a loud ALARM; all-sources-unsampleable
> exits rc=2 (a blind verifier is never a pass). Rulebook:
> `docs/MEASUREMENT_CANON.md` (definitions, NAMING LAW — bare "edge" =
> canonical only — verification protocol, consumption rule: IMPORT canon,
> never re-implement; change control). 8 canon tests, 55 pytest green.
>
> **REC EXECUTED (item 6; same commit):** C1_UNTESTED re-registered
> ANYTIME-VALID before any look was consumed —
> `docs/COHORT1_UNTESTED_AMENDMENT.md`: e-process (reject e>=20, futility
> 300, econ gate +0.02 at crossing, OK-rate 0.75), CANON venue fees, epoch
> 2026-08-24T17:00Z UNMOVED. Legitimacy: only count-only "0/30" lines had
> ever printed for the 9. Original 20 keep their 07-30 charter (flat 2%,
> single look) + per-run fee-divergence disclosure. End-to-end verified as
> cron user (9 x ACCRUING e=n/a; 1,515 forward records in window).
> **PROPOSED, operator ruling pending: re-register the 15 unconsumed
> original-cohort5 looks the same way.**
>
> **EVIDENCE BACKUP LIVE (item 5; 84c0407).** Nightly VPS bundle 03:30Z
> (`/opt/pa2-backups/mb_evidence/`, tier1 full + shadow sinks gzipped, 14d
> retention, gamma weekly): first bundle 20M / 95 entries. OFF-BOX leg:
> Windows schtasks `MB_Evidence_Pull` daily 05:00 local pulls to
> `C:\lockes-picks\mb-evidence-backup\` — tested, **md5 verified identical**
> (e82df0bf..). Cloud bucket = upgrade path, needs operator creds (no
> aws/rclone on the box).
>
> **EVERY-SESSION SURFACING (item 7):** banners atop
> `MB_DEEP_DIVE_NEXT_PROMPT.md` + `docs/MB_SESSION_STARTUP.md`; memory
> `project_mb_overhaul_program.md` indexed top of MEMORY.md. Read order:
> agenda -> canon -> [canon] line (ALARMS must be 0 before quoting numbers).
>
> **ITEM 1 (disposition ledger):** drafted by agent; finalization + the
> one-word sign-off sheet land next block. NOTHING retired/stopped yet —
> every disposition is operator-gated. Measurement-canon doc (items 2+3) =
> `docs/MEASUREMENT_CANON.md`, finalized from agent draft.
>
> ## 2026-08-24 (~17:15Z) — CHAIN-TRUTH FILL CLASSIFICATION (operator: "it's
> blockchain, isn't there a record?" — YES, and it resolves the feed gap)
>
> The RTDS feed hides who started each trade, but every fill_tx settles on
> Polygon and the receipt names the aggressor: the OrderFilled whose
> counterparty field is the exchange itself is the TAKER's order (validated
> pattern, decode_fill_v2); the taker's side for our token comes from the
> receipt transfer-log rule (side_from_receipt_logs). Retro-classified ALL 65
> epoch-2 fills via raw eth_getTransactionReceipt (tenderly, 30s timeouts):
> **65/65 classified, 0 errors, 0 unknowns** (`scripts/bidsim_classify_fills.py`).
>
> **RESULT (ESTABLISHED, 72 resolved bids at read time):**
> * taker-SELL aggression (would hit a resting bid): **17/65 = 26.2%** of fills
> * **chain-truth strict fill rate: 17/72 = 23.6%** vs the 74% break-even bar
> * charter-rule (any print): 65/72 = 90.3%
> * RTDS's own side field said 8 SELL — the chain found 17: the feed's side
>   field is confirmed unreliable (perspective-dependent), never use it for
>   this again; classify via receipts.
>
> **Honest caveat (microstructure, stated not assumed):** taker-BUY prints at
> <= our bid imply ASKS were resting at/below our bid — in the counterfactual
> where our bid exists, those incoming asks would have matched US first. So
> strict taker-SELL-only likely UNDERCOUNTS the true maker fill rate; the
> truth lies in [23.6%, 90.3%] and prints alone cannot pin it further. The
> bracket is now measured from chain truth instead of an unreliable feed
> field. Fill-side ruling remains OPEN — but the operator now has real
> numbers: strict reading is FAR below 74%, loose reading far above.
>
> ## 2026-08-24 (~16:45Z) — OPERATOR RULINGS ON THE 6 RECS + TWO EXECUTED
>
> **RULINGS (operator, verbatim intent):**
> * **FUNDING RULED: start ~$500 once a test passes; grow to ~$5,000 as it
>   proves out live.** (The long-open "conditional funding number" is now
>   SET. Band pass => $500 pilot; scaling to $5k on live proof.)
> * "Don't fund yet" — RATIFIED.
> * Scout filter fix — GO. **EXECUTED (b8deacf):** discovery — the old floor
>   (>=500 trades/6h = 2,000/day) sat ABOVE the dive's 1,000/day UNCOPYABLE
>   ceiling: the filter could only EVER select machines; sweep #1's 9/9 bots
>   was structural. New band notional>=$25k / markets>=5 / 10<=n<250 per 6h.
>   Dry-run on the real 07-30 capture (26,123 wallets): **82 human-scale
>   candidates, 89 machines dropped.** No sweep launched — cadence still open.
> * The 9 untested cohort1 traders — GO, "if they pass the test."
>   **EXECUTED (3c76580):** cohort1-untested group added to the daily
>   qualification cron; own fresh epoch **2026-08-24T17:00:00Z**, same bars,
>   same locks file, source tag `cohort1_untested single-look`. End-to-end
>   verified as the cron user (9 x ACCRUING 0/30, existing locks untouched).
>   Deployed via mb_readout checkout sync.
> * The 13 INSUFFICIENTs — ruled "positive EV -> add, negative -> remove."
>   **FINDING: their dive verdicts contain NO edge measurement at all** (that
>   is what INSUFFICIENT means; verified across all 13 JSONs — no
>   edge_mean/n_labeled fields). One of the 13 is `0x44886115` = cohort4,
>   ALREADY on the roster — the real set is 12. Forward EV requires WATCHING
>   them: the concrete plan is 12 observation-only probes (roster 31->43,
>   ledger + watcher restart, fresh epoch, single-look bar as above).
>   **AWAITING explicit confirm — roster changes stay operator-gated and the
>   ruling did not name a 12-probe roster expansion.**
> * Fill-side ruling — still OPEN; operator asked for a simpler explanation
>   of the bracket proposal before ruling (given in chat 2026-08-24).
>
> ## 2026-08-24 (~16:05Z) — FIX VERIFIED LIVE + INTERIM chase-vs-post (operator "proceed")
>
> **AMENDMENT 1/1b VERIFIED ON LIVE DATA:** 74 posts on the epoch-2 sink —
> **0 missing trigger_tx, 0 self-fills** (scoreboard alarm 0 across 3 daily
> runs). Median wait moved 0.6s (pre-fix) → 30.3s: the instrument measures a
> different, credible quantity now. Pending-verification item CLOSED.
>
> **INTERIM chase-vs-post at 71 resolved bids (NOT the ~100 proposal; both
> fill-side interpretations, no ruling made):**
> * A (charter rule, all prints fill): fill_rate 64/71 = 0.901; cond edge
>   **−0.2520 on only 14 LABELED fills of 64 (22% coverage — the other 50
>   markets are simply unresolved after ≤3 days)**. HEADLINE = THE BLINDNESS:
>   the 14 fast-resolvers are a horizon-biased subset; the −0.25 must not be
>   quoted as the maker edge.
> * B (SELL-prints only): fill_rate 8/71 = 0.113; **0 of 8 labeled — cond
>   edge unavailable.**
> * Fill sides on record: 55 BUY / 8 SELL prints (the fill-side ruling now has
>   forward data accruing; OPERATOR DECISION still open).
> * **STRUCTURAL NOTE for the tripwire:** "~100 resolved bids" gives the
>   FILL-RATE arm, but the cond-edge arm needs those markets to RESOLVE —
>   which lags days–weeks behind bid resolution. The proposal at ~100 will be
>   fill-rate-firm and cond-edge-thin; expect a two-stage read.
> * Method: labels = daily-supplemented gamma_resolutions.json (refreshed
>   2026-08-24T11:42Z); venue fee = rate·p·(1−p), unmapped flat 2%; read-only.
>
> **Other movement (source: 08-24T11:42Z scoreboard + locks file):** band
> n=31 pooled −0.0685 e=0.417 (trajectory 0.717→0.885→0.683→0.501→0.417 — no
> tripwire, descriptive); **cohort5 lock #5:** `0xc660ae71` DOES NOT QUALIFY
> 08-23T11:43Z (edge −0.0623, P 0.235, n=30) — 5 of 20 graded, 5 failed.
> Watcher 3d uptime, 0 restarts. PRs: #5 (docs sync), #6 (sports lane,
> operator-commanded) — both opened, merge = operator.
>
> ## 2026-08-21 (~14:49Z) — BIDSIM SELF-FILL DEFECT FIXED + SCOUT SWEEP #1 CLOSED
> (operator: "delete duplicate, resolve sim now then get proper data")
>
> **1. SCOUT SWEEP #1 CLOSED — zero admissions.** Finished rc=0
> **2026-08-21T09:23:40Z**, 9/9 verdicts: **8 REJECT + 1 INSUFFICIENT-EVIDENCE,
> 0 ADMIT.** All 8 rejects on the same mechanical gate (UNCOPYABLE; true chain
> rates 4,399 / 7,065 / 8,704 / 11,483 / 11,883 / 17,045 / 22,299 / 26,180
> fills/day vs a 1,000 cut). `0xa16a1302` hit the receipt budget (30,000 of
> 80,549 V2 txs attempted, 0 fetch failures) — a wall, not a finding. The
> tripwire "scout completions -> admission proposals" fired with an EMPTY
> proposal. Selection filter (notional >=$250k + >=100 mkts + >=500 trades over
> 6h) returned nine machines — that is a finding about the FILTER, and the
> scout-cadence decision remains OPEN with the operator. Verdicts backed up at
> `deep_dive_scout.bak-sweep1-20260821` (md5 of `_summary_scout.json`
> `08645d645a963a2ec9c0c098a8241011`, verified identical to live).
>
> **2. DUPLICATE SCOUT RUN KILLED (operator go).** The 08-20T16:41Z relaunch
> sat in `scout_queue2.sh`'s `pgrep` wait-gate (so it never corrupted output —
> the guard worked), then woke 8 min after the first run finished and spent
> ~5.3h re-diving the same 6 already-graded addresses. Killed by exact PID
> (3186231/3186232/3293330 — no `pkill`, per the self-match landmine). Verdicts
> verified intact after the kill.
>
> **3. BIDSIM AMENDMENT 1 — the 93.8% fill rate was an artifact (a1996b9).**
> At 35 posts / 30 fills / 2 expires the headline read 30/32 = **93.8%**, but
> **21 of 30 fills landed within 5s, median wait 0.6s**, print price exactly ==
> bid in 23/30, and all 5 chain-sourced posts filled in <=0.6s. Only 6 of 30
> waited >60s. ROOT CAUSE (code-verified): `on_print` filled any open bid from
> any print at/below the bid with no knowledge of WHICH TRADE the print
> belonged to — a whale order matched against several makers arrives as several
> tape rows, row 1 registered the bid and rows 2..N of the SAME TX filled it.
> The pre-existing ordering guard covered only the literal trigger row.
> **This is NOT the registered queue-optimism bias** — that is "we might sit
> behind others at our level"; this was counting the very order we reacted to
> as our counterparty.
> FIX: `register()` records `trigger_tx`; `on_print()` refuses to fill from
> that tx. Queue optimism deliberately RETAINED (still brackets the 07-19
> 47.6% snapshot floor from above). Fills now carry `fill_tx`/`fill_trader`/
> `fill_side`.
> **NOT changed, and OPEN:** a resting BID is economically fillable only by
> SELL aggression and the rule still ignores side. Measured on the 07-30
> firehose capture (400,000 rows): BUY 346,225 / SELL 53,775; both sides
> present in only 30,839 of 241,869 (token,ts,price) groups — a sell-only rule
> would swap a known bias for an unquantified feed-asymmetry bias. Instrumented
> instead of guessed. **OPERATOR DECISION OPEN.**
> VERIFICATION: 92 pytest green; `trigger_tx` carried through
> `bidsim_rehydrate` (a restart cannot resurrect the self-fill); anti-no-op
> SOURCE test asserts all 3 call sites thread the tx, **mutation-tested**
> (dropping `print_tx` fails the suite; restored passes) — this lane's worst
> failure mode is a fix the reporting path never adopts.
> **EPOCH RESET: 2026-08-21T14:48:56Z.** Old sample PARKED at
> `mirror3_bidsim.jsonl.pre-amend1-20260821` (67 lines = 35+30+2), NOT poolable
> and never to be quoted as a fill rate. New sink started empty; deploy
> confirmed `[bidsim] ENABLED ... rehydrated_open=0`. **The ~100-resolved
> tripwire RESTARTS FROM ZERO.** Backup `copy_watcher.py.pre-amend1-20260821`.
>
> **4. AMENDMENT 1b (~15:59Z) — THE FIRST FIX WAS 75% INERT; CAUGHT ON LIVE
> DATA (456578c).** The RTDS register site read `trigger_tx=sig.get("tx")`, but
> **`rtds_sig()` does not carry tx** (`copy_watcher.py:771-786`) — only the
> chain path builds `sig["tx"]` (`:620`). So the guard was inert for every
> RTDS-sourced post — **15 of 20 posts (75%) in the parked sample**. Found by
> asserting on the first live epoch-1 posts: 2 of 3 carried `trigger_tx`, the
> RTDS one was None. Now reads `row.get("tx")` (rows always carry it —
> measured **0 missing in 20,000** rtds roster records). **The anti-no-op test
> asserted only the call-site SPELLING and so let this through**; it now pins
> both sources AND asserts the premise (`rtds_sig` has no tx) so it cannot
> silently invert. Mutation-tested both ways.
> **EPOCH RESET AGAIN: 2026-08-21T15:59:14Z** (1 of the 3 epoch-1 posts was
> collected under the inert guard). Epoch-1 parked at
> `mirror3_bidsim.jsonl.pre-amend1b-20260821`. LESSON for §7: a source-text
> assertion proves a call site's SPELLING, never that the value it names is
> populated on that path — assert the premise too.
>
> **5. SCOREBOARD SHIPPED (the one allowed build, 456578c).**
> `scripts/mb_scoreboard.py` appends band + bidsim + cohort5 + scout to the
> 11:40Z cron block. Every section reads its own authoritative artifact and
> fails LOUD (explicit `UNAVAILABLE` + a missing-count line, never silence);
> carries a standing **self-fill regression alarm** (`fill_tx == trigger_tx`
> must stay 0). Self-tested on fixtures (healthy / all-4-missing / regression)
> and run end-to-end as the cron user (rc=0). ALSO:
> `deploy/label_and_fee_refresh_cron.sh` had **no repo copy** — it existed only
> on the VPS inside a checkout that hard-resets to this branch daily. Now
> committed (ASCII-sanitized) and the checkout synced to `456578c`.
>
> **STILL PENDING (next session MUST verify — not yet observable):** the first
> post+fill pairs on the new sink. Confirm no sub-second self-fills survive, that EVERY post (chain AND rtds) carries a non-null trigger_tx, and
> that `fill_tx != trigger_tx` on every fill:
> `python3 -c "import json;r=[json.loads(l) for l in open('/opt/pa2-shared/mirror3_bidsim.jsonl') if l.strip()];f=[x for x in r if x['type']=='fill'];print(len(f),[x['wait_s'] for x in f][:20]);assert all(x.get('fill_tx')!=x.get('trigger_tx') for x in f)"`
> Posts are sparse (hours). A still-empty sink after a day is a FAILURE signal,
> not a quiet success.
>
> **UNCHANGED:** band n=14 pooled +0.0414 e=0.885 (08-21T11:41Z; no tripwire);
> cohort5 20 eligible / **4 locked DOES-NOT-QUALIFY** (`0xf705fa04` locked
> 08-20T11:41Z: edge +0.0666 PASS, P 0.890 FAIL vs 0.95, OK-rate 0.81 PASS) /
> 16 accruing; roster 31 (`15+8+6+1+1probe`); RTDS gap re-measured **3.41%**
> (43 chain-only of 1,261 unique, 20.25h tx-join, both sets asserted non-empty).


> ## 2026-07-30 (~16:45Z) — FULL AUDIT + "PROCEED WITH ALL": fee truth, cohort4 re-admission, RTDS A/B LIVE
>
> **Operator directive:** full state audit + path-to-profit; then "proceed with
> all" on (1) benched re-admission, (2) fee + copyability measurements,
> (3) RTDS latency build. All three EXECUTED this session.
>
> **RE-REVIEW COMPLETE (the 07-22 key deliverable): 20/20, FLIPPED: 0** —
> roster-complete via strict `rereview_diff.py` (rc=0). Chain edges survive
> complete labels: +0.0101 → +0.0749 per trader. No roster removals.
>
> **FEE TRUTH (kills the phantom-fee hypothesis):** CLOB per-market
> `taker_base_fee`: **145/148 resolvable shadow markets = 1000, 3 = 0**;
> weighted by our OK first-buy fills: 181 fee-bearing / 6 zero-fee / 877
> unmappable-via-cache (fee-known subset only — disclosed). Live RTDS fee
> field: fee/(price×size) p50 ≈ **0.028** (n=1,826, dominated by crypto
> 5-min up/down + esports — cohort3's exact hunting ground). ⇒ the readout's
> 2% haircut is roughly RIGHT; the measured near-zero copy edges STAND.
> Money path = copyability selection + latency, not a fee correction.
>
> **COPYABILITY (the structural insight):** whale chain edge ≠ our copy edge.
> Diagnostic arms (post-hoc, Bonferroni-flagged): `0x84dbb710` chain +0.0199
> vs copy **−0.0432** (57 mkts); `0x4ad6cade` −0.0305; `0x216509be` −0.0364 —
> vs `0xc660ae71` +0.0450, `0xee00ba33` +0.0826, `0xecb14ac6` +0.0428.
> Snapshot artifact: `deep_dive/copyability_snapshot_20260730.txt`. NEXT: a
> pre-registered copy-edge-selected cohort (criteria to be registered BEFORE
> composition; chain deep-dive stays as the fraud/skill screen).
>
> **COHORT4 RE-ADMISSION EXECUTED (operator go):** benched `0x44886115` met
> his pre-registered bar (fwd-since-bench **+0.1114, P=0.972, 60 resolved**
> vs bar ≥+0.02 / ≥0.90 / ≥20; readout 07-30 12:30Z). Ledger: benched →
> `cohort4`, epoch 2026-07-30T16:22:32Z, **`forward_only: true`**, benched
> emptied w/ history entry, clean=30 unchanged (watcher restart not needed
> for the ledger — detection set identical). Backup
> `chain_audit.json.pre-cohort4-20260730`. **Dry-run caught the trap:**
> without forward_only the "fresh" cohort pooled his pre-bench history (132
> mkts +0.0026 vs true 60 / +0.1114) — `trust_after` is NOT a time filter.
> New ledger-driven `forward_only` flag in `shadow_readout.py` applies the
> real detect_ts cutoff; label prints `FWD-only since 07-30`; cohort1/2/3
> byte-identical (verified same-instant). Epoch = re-admission time, NOT
> bench time — the bar-meeting window was admission EVIDENCE; reusing it as
> the verdict window would select and verify on the same data.
>
> **RTDS A/B LIVE (cd29962):** `rtds_watch()` beside the chain poll —
> match-time detection w/ identity (measured RTDS delivery p50 0.82s/p90
> 1.2s/no tail vs chain p50 1.30s/p90 11.6s). OWN sink
> `mirror3_shadow_rtds.jsonl`, source=rtds, chain stream canonical +
> untouched. Env `MIRROR3_RTDS_AB=1`. Deployed 16:31:55Z, connected
> 16:32:53Z, heartbeat `rtds=AB`, 0 alarms, 56/56 pytest. ⚠ A/B must answer
> BEFORE any swap: (a) coverage — RTDS may emit taker-side only (maker-side
> roster fills chain-only?); (b) fill-parity on the same tx; (c) realized
> tax/lag delta. Judge by tx join once roster hits accrue (~1 day).
>
> **12:30Z readout state (07-30):** cohort1(15,REDUCED) −0.0067 P=0.446 (92
> mkts); cohort2(8) POWERED **+0.0129 P=0.616 NOT DEMONSTRATED** (76);
> cohort3(6) POWERED +0.0028 P=0.527 NOT DEMONSTRATED (34); ALERT fired for
> both (per-trader + LOO run + archived same day).
>
> **17:00Z ADDENDUM — 'proceed with all recs' EXECUTED (fd6e6dd, e177acf):**
> (1) **STOPPING RULE LIVE** — verdict locks: one pre-registered test per
> cohort, consumed at first power-bar crossing; locks BACKFILLED from the
> durable log's own first crossings (cohort1 07-20 P=0.617; cohort2 07-23
> +0.0210 P=0.648; cohort3 07-27 −0.0131 P=0.307 — all NOT DEMONSTRATED);
> later readouts print LOCKED + diagnostic; POWERED re-alert storm gone.
> Locks file: `deep_dive/verdict_locks.json` (immutable; corrupt = loud).
> (2) **PER-MARKET FEE IN THE EQUATION** (operator: 'ding market by market')
> — measured-exemption only: fee_map (508 tokens, 56 zero-fee exempt) built
> from CLOB `taker_base_fee`; zero-fee tokens not dinged, others keep flat 2%
> until a rate calibration (separate gate). Header discloses the mode every
> run. Corrupt map = readout REFUSES (silent equation change is the failure
> mode). (3) **CRON 11:40Z daily**: label supplement + fee-map refresh before
> the 12:30Z readout (`label_and_fee_refresh_cron.sh`). (4) **COHORT5
> PRE-REGISTRATION drafted** (`docs/COHORT5_PREREGISTRATION.md`): copy-edge
> selection (chain screen + fwd copy edge ≥+0.02 on ≥30 + P≥0.95 single-look
> + OK-rate ≥75% + ≤50% conc; qualification records start 07-30T17:00Z) —
> **composition awaits operator sign-off of the criteria.**
>
> **~17:30Z ADDENDUM 2 — CANDIDATE HUNT RESUMED (operator 'proceed'):**
> (1) **INSUFFICIENT re-grade IN FLIGHT** — `/tmp/insuff_regrade.sh` (pid on
> VPS, log `/tmp/insuff_regrade_main.log`, out-dir
> `deep_dive_insuff_regrade/`): the 10 most label-starved INSUFFICIENTs
> (9 with >80% of evidence unlabelable + `0x70d94a` at 65.7%; roster
> `/tmp/insuff_regrade_roster.txt`; `0xfbfd14dd` has no API cache — dive
> fetches). Gates mirror admit_rereview3: RPC-free → CLOB-supplement THEIR
> cids (reuses `/tmp/clob_sup.py` via `/tmp/label_gap_cids.txt`) → coverage
> verify → dive → strict roster-complete diff (`rereview_diff.py`, rc=4 on
> any gap). ~2.2h/trader ⇒ ~22h. **Verdict changes are PROPOSALS ONLY.**
> First launch died on a stale root-owned `/tmp/label_gap_cids.txt`
> (PermissionError) — cleared and relaunched.
> (2) **RTDS SCOUT RECORDER RUNNING** — `/tmp/rtds_scout.py`, 6h capture of
> the FULL firehose (wallet/side/price/size/cid/ts) →
> `rtds_scout/scout_20260730.jsonl`, then auto-ranks top non-roster wallets
> by trades/notional/markets (roster excluded; 0 rows = FAILURE not empty
> ranking). Output = future dive candidates, no roster impact.
>
> **~03:15Z 07-31 ADDENDUM 3 — 'proceed with all you can':**
> (1) **SCOUT DIVE QUEUE LAUNCHED** (`/tmp/scout_queue.sh`, gated behind the
> running re-grade): 9 candidates selected from the finished 6h scout
> (983,755 trades / 26,123 wallets) by pre-stated filter notional ≥ $250k AND
> markets ≥ 100 AND trades ≥ 500. EXCLUDED + disclosed: the 5-wallet
> identical-fingerprint cluster (10 trades / $100,099 each — sybil pattern)
> and single-market whales. No API caches for these wallets → dives run
> chain-only reconstruction with `cache_status=missing` disclosed per
> verdict; a targeted cache-build mode for `find_copyable_traders.py` is a
> noted enhancement. Verdicts = PROPOSALS. Out-dir `deep_dive_scout/`.
> (2) **A/B INTERIM (n=717 joined fills, ~10h)**: coverage — RTDS saw
> **717/804 chain fills (89.2%), chain-only=87, rtds-only=0** (strict
> subset, no phantoms). **[SUPERSEDED 2026-08-01 — see CORRECTION below;
> the maker-side blind-spot reading was WRONG]**. Latency —
> rtds−chain detect p50 **−0.56s** (min −10.9s, max +3.76s). Parity —
> verdict 712/717, fill 702/717. ⇒ interim read: a pure SWAP would lose
> ~11% of fills; the candidate architecture is HYBRID first-seen (RTDS for
> speed + chain for completeness) — decision with the full-data report.
> (3) Re-grade 8/10 done at 02:45Z; diff auto-runs at completion.
>
> ## 2026-08-01 00:30Z — CORRECTION + 4 VERIFIED FINDINGS (workflow, 8 agents,
> every finding independently refutation-tested; all 4 survived)
>
> **⛔ CORRECTION — I WAS WRONG ON THE RECORD.** The 07-31 addendum called the
> RTDS coverage gap STRUCTURAL ("RTDS emits taker-side only; the maker-side
> blind spot is REAL"). **That reading is REFUTED.** The gap is our own
> consumer's DOWNTIME:
> * **360/364 (98.90%)** of chain-only roster fills fall INSIDE a measured
>   RTDS outage window (either clock, ±5s). True unexplained residue = **4
>   fills = 0.13%** of 3,075 in-window chain fills.
> * When RTDS is provably connected: **maker-side capture 98.39%**
>   (2,440/2,480) vs taker-side 99.62% (259/260) — a **1.2pp** spread. Maker
>   fills are NOT invisible. Captured population is **90.4% maker**, mirroring
>   the chain population (90.9%) — the opposite of a taker-only feed.
> * Controls that make it decisive: **base-rate** (captured fills landing
>   inside outages 0.48% vs a 9.21% outage base rate = 19x depletion);
>   **placebo** (time-shifting the outage windows drops the hit rate from
>   88.7% to 3.6–15.7%); **regime capture** 3.88% inside vs **98.50%** outside.
> * Window 2026-07-30T16:32:53Z → 2026-08-01T00:11:33Z (31.64h); outage total
>   10,500s = **9.21%**; observed miss **11.84%** (364/3,075).
> ⇒ **The hybrid-vs-swap question is REOPENED and must be re-measured AFTER
> the keepalive fix.** No architecture decision on the old premise.
> (Earlier 804/717 counts were an ~10.5h snapshot of live-appending sinks;
> the 31.6h re-measure supersedes them. The MISS RATE agrees, 10.8% vs 11.8%.)
>
> **ROOT CAUSE OF THE OUTAGES — a defect in OUR consumer, fix identified.**
> `rtds_watch` never sends the application-level `"PING"` text frame this
> venue requires. **This repo's own reference consumer already documents it:**
> `base_engine/data/rtds_websocket.py:22` `_PING_INTERVAL = 5  # RTDS requires
> keep-alive pings`, `:75-76` `ping_interval=None  # we handle pings manually
> (RTDS protocol)`, `:115` `await self.ws.send("PING")`, `:170` PONG handling.
> `copy_watcher.py:753-755` does the OPPOSITE (`ping_interval=20`, protocol-
> level only; the ONLY `ws.send` in the loop is the one-time subscribe).
> Signature: **83 silent alarms vs 2 ConnectionClosedError in 13.92h** —
> transport alive, data dead (protocol pongs don't wake `ws.recv()`).
> **The "slow consumer blocks the socket" hypothesis is REFUTED**: detect_lag
> p50 0.9s / max 5.7s / **0 of 2,191 over 15s**; busy connections live **8.6x
> LONGER** (med 729s with ≥5 records vs 85s with 0); **17/83** alarm-killed
> connections logged ZERO records (no inline HTTP ran at all).
>
> **ADMISSION FLIP EXPLAINED — and it has a serious objection.**
> `0xfbfd14dd` INSUFFICIENT→ADMIT. The "edge fell while verdict improved"
> paradox is FALSE: the original +0.0160 was the mean of **ONE** market
> (`n_labeled=1`) — never a measurement. The single gate that changed is
> `skill_gradeable` (chain_deep_dive.py:482 soft branch); n_labeled 1 → 1090.
> Every hard gate already passed before. Now mkts=1090, **P=0.96, edge
> +0.0103**. **BUT: `maker_frac = 0.886`** — the edge is earned at MAKER
> prices by an 88.6%-maker account, and we copy as a TAKER crossing the
> spread. Also `incomplete_cache_sweep=TRUE` (both runs), the "100%" backing
> is 99.789%, the copier-forensic leg never ran, and the hidden-activity check
> was DISABLED (`hidden=null` ≠ zero hidden). Edge +0.0103 is also below our
> +0.02 copy floor. NOT-MEASURED (flagged): maker fraction among the 1,090
> GRADED first-buys — if that subset is taker-heavy the objection weakens.
>
> **SCOUT RELAUNCH — root cause found + a material correction.** Failure was
> cwd: launched from `/home/ubuntu` (0750 ubuntu:ubuntu; polymarket in no
> shared group) so pydantic's relative `env_file=".env"`
> (`config/settings.py:1604`) hit EACCES. Verified: same import from
> cwd=`/opt/polymarket-ai-v2` → IMPORT_OK. **CORRECTION to the 07-31 entry:**
> the 9 candidates DO have API caches — but STALE (2026-07-10) and TRUNCATED
> (500 rows, `status="hft"`). The dive would silently reuse them
> (`chain_deep_dive.py:1283-1291` cache-hit short-circuit), producing verdicts
> with the SAME `incomplete_cache_sweep=TRUE` weakness as `0xfbfd14dd`.
> A cache refresh before diving is the difference between a real verdict and
> a repeat of that weakness.
>
> **⚠ VPS REBOOTED 2026-08-01 00:08:51Z — `/tmp` WAS WIPED.** All 5 services
> back active; every persistent artifact survived (`deep_dive_insuff_regrade`
> 10/10, `verdict_locks.json`, both sinks, ledger). Lost: the ad-hoc `/tmp`
> orchestration scripts + rosters (`insuff_regrade.sh`, `scout_queue.sh`,
> `scout_dive_roster.txt`, and the prior session's `clob_sup.py`). **Lesson:
> orchestration scripts belong in the repo, not `/tmp`** (the §7 landmine
> "all copy-trader data is in /tmp — one reboot erases it" just came true for
> the scripts).
>
> ## 2026-08-19 — TWO RESULTS THAT REFRAME THE LANE (sequential-design analysis
> + a verified gate defect). Nothing changed on the box; findings only.
>
> **⛔ DEFECT FOUND — WE SHADOW-BUY AT $1.00.** Verified by direct count over
> the whole sink (`/tmp/_fillchk.py`, 2026-08-19): of **3,257 OK first-buys**,
> **281 (8.6%) have `shadow_fill` >= 0.999** and **365 (11.2%) >= 0.99**.
> A fill at 1.000 has ZERO upside and ~full downside: edge = -fee
> deterministically. Concentration: `0x4ad6cade` **190/199 = 95.5%** of his OK
> first-buys are >=0.99 (**178 at exactly 1.000**, whale_price p50 0.9990) —
> and he is **51% of cohort2's first-buys** (readout 2026-08-18T12:30Z), so
> this defect has been dragging that cohort's number. Also `0x0e5bd767` 62.5%
> (5/8), `0x7c3db723` 15.5% (53/343), `0xf705fa04` 13.4% (36/269),
> `0xecb14ac6` 11.8%, `0xab197165` 10.7%.
> **ROOT CAUSE — a hole in our own gates, not a market fact.** `evaluate_gates`
> checks spread <= max_spread and chase (ask - whale_price) <= max_chase; at
> whale 0.999 / ask 1.000 the chase is 0.001 so the gate says OK. **There is no
> maximum-fill-price gate.** PROPOSED (operator gate, NOT applied): add a max
> fill price; re-measure after. Locked verdicts stay locked — a fix applies
> FORWARD only, under a fresh pre-registration.
>
> **⛔ THE +0.02 FLOOR IS DECORATIVE — IT NEVER BINDS.** Sequential-design
> analysis (agent, 2026-08-19; ESTABLISHED where computed on real data):
> **P>=0.95 at n=30 requires edge >= +0.127** (6.4x the stated +0.02 floor);
> +0.105 at n=44; +0.024 at n=855. Whenever the P bar fires at n=30 the edge
> bar is automatically satisfied — measured: the P-only and full-bar
> false-positive rates are IDENTICAL (5.40%). **Power of the one-shot test at
> realistic edges is 7-8%** against a 5.4% FPR. The seven consumed tests could
> not have passed.
>
> **THE ONE-SHOT RULE WAS CORRECTLY BUILT AND COST NOTHING.** Measured FPR on
> nulls built from our own per-market edge pool (N=3,467 market-instances,
> sd 0.4219; 4,000 simulated subjects): **one-shot at n=30 = 5.40%**
> (correctly calibrated); **naive daily retest = 17.78% at 13 looks, 22.28%
> checked every market** (3.2-4.6x inflation — this is the number that
> justifies the design). And it cost zero: across ~134 powered looks in the
> durable log, **0 of 7 subjects ever reached P>=0.95 at any point**, and 0 do
> now. ⚠ The hazard is real though: cohort2 re-ordered by detect-time instead
> of resolution-time touches **P=0.962 at n=48** — a look-schedule change flips
> "never significant" into "significant once".
>
> **WHAT PROOF COSTS (fixed-n, 80% power; INFERRED, and a LOWER BOUND — the
> iid assumption is violated by same-event correlation):** +0.05 -> 440 mkts;
> +0.02 -> 2,751; +0.01 -> 11,005; +0.005 -> 44,020. At measured accrual
> (cohort1 6.7/d, cohort3 15.0/d, cohort4 46.4/d, single trader ~7/d):
> +0.02 = **59 days at cohort4's rate, 1.1 years at cohort1's**; +0.01 =
> 237 days to 4.5 years; +0.005 = 2.6 to 18 years.
>
> **ANYTIME-VALID DESIGN: valid, but it fixes the wrong problem alone.**
> Betting e-process (mixture over lambda) measured FPR **1.55%** (and 3.48-4.73%
> checked after every one of 20,000 markets) — error controlled under
> continuous monitoring, as theory guarantees. Cost: **~1.8-1.9x** the fixed-n
> sample for equal power. **Backtested on all 7 consumed tests under both
> chronologies: NO subject rejects** (peak e-value 6.44 vs threshold 20). The
> binding constraint is n, not the stopping rule.
>
> **ONE LIVE CANDIDATE:** `0x7c3db723` — edge **+0.0481** with **sd 0.294**
> (vs 0.4219 pooled — unusually low variance is why he is tractable); needs
> ~231 markets fixed-n / ~411 under the mixture e-process; **has 100**,
> accrues ~7/day => **~6 weeks**. His single look is consumed and cannot be
> re-opened; continuing him requires a FRESH forward pre-registration
> (operator gate). NOTE: 15.5% of his OK first-buys are >=0.99 fills, so the
> $1.00 defect touches his number too.
>
> **PROPOSED PRE-REGISTRATION (drafted, NOT adopted — operator gate):**
> statistic = per-market mean edge (canonical unit); procedure = uniform
> mixture betting e-process over lambda in {0.05,0.1,0.2,0.4,0.6,0.8};
> **null H0: edge <= 0** (the +0.02 floor becomes an ECONOMIC gate applied
> AFTER significance — folding it into the null roughly doubles n again);
> alpha 0.05, reject at e-value >= 20; check every readout; pre-registered
> futility N_fut per subject; **forward-only from a new epoch** (retro-fitting
> inherits the selection it exists to prevent). Locks stay locked.
>
> ⚠ CAVEATS ON RECORD: all n* are LOWER BOUNDS (iid violated). Chronology is
> imperfect — gamma `resolved_at` is a nominal end date, precedes the shadow
> fill in 180/184 cases for one trader, and 38 market-instances carry dates
> through 2027-06-30; both orderings were tested and only cohort2's
> counterfactual moves. Reconstructions use TODAY's labels while the durable
> log used the labels of the day — where they differ the durable log is the
> record (it reproduces all 4 locks exactly). No changepoint analysis was run,
> and an anytime-valid test of a constant mean is invalid under a changepoint.
>
> **STILL RUNNING (not yet reported):** the 4-part edge-proof workflow —
> power/variance, winner's-curse split-half persistence, best-estimator
> (pooled / random-effects / price-band stratification), and alternative
> estimands (conviction, latency, chase, taker-vs-maker).
>
> **2026-08-19 ~14:35Z — BOTH DEFECT FIXES DEPLOYED (632ad5b, operator
> 'proceed'):** (1) `PRICE_NO_UPSIDE` gate — optional `max_fill` in
> `evaluate_gates` (default None = byte-identical legacy, test-proven);
> `WatcherConfig.max_fill=0.98` (`MIRROR3_MAX_FILL_C`), the zero-upside bound
> at the flat 2% fee; both watchers pass it. FORWARD-ONLY — history + locks
> untouched. (2) RTDS app-level keepalive — `ping_interval=None` + PING task
> every 5s + PONG skipped as non-data, mirroring `rtds_websocket.py`; PLUS a
> DATA-SILENT alarm (adversarial self-review: PONGs wake recv() and would
> have masked the 60s alarm — data-liveness is now tracked separately, so the
> alarm keeps its meaning). 60/60 pytest incl. 4 new. Fenced restart 14:31:45Z,
> RTDS connected 14:33:11Z, **0 silent alarms since restart** (prior rate
> ~1/10min; a clean multi-hour window is the proof — VERIFY at next check-in:
> `journalctl -u polymarket-mirror3 | grep -c "SILENT ALARM"` should stay ~0
> and the A/B miss rate should collapse toward the 0.13% residue).
> Backup `copy_watcher.py.pre-maxfill-20260819`. The 4-part edge-proof
> workflow was resumed (`wf_8a4651be-90a`, cached prefix replays).
>
> ## 2026-08-19 (~15:00Z) — EDGE-PROOF STUDY COMPLETE (4 analyses, each
> adversarially re-verified; 3 of 4 headlines TRIMMED by their refuters —
> the trimmed versions below are the citable ones)
>
> **1. POWER (ESTABLISHED, survived refutation twice — durable-log lines
> reproduced to the digit):** pooled canonical set = 1,690 resolved markets /
> 1,839 OK first-buys / SD of per-market edge 0.4105. Pooled NET edge
> **+0.00645, P=0.754**. Proof at that edge needs **~27,500 markets ≈ 384
> days** at the measured 71.6 mkts/day (324–639 across rate range). The 7
> consumed tests ran at **8–10% power** (~1/60 of the sample the +0.02 floor
> needs; P≥0.95 at n=30–50 demanded a +0.10 to +0.13 point estimate).
> **STRUCTURAL: the full bar (edge≥+0.02 AND P≥0.95) can NEVER pass for a
> true edge ≤ ~+0.022** — at true edge exactly +0.02 the joint power CAPS at
> ~0.49 for ANY n (the floor arm fails half the time forever). Inversion:
> 90 days proves ≥+0.0133 (P-arm) but the FULL bar asymptotes at ~+0.022.
> ⇒ the bar itself needs an operator redesign decision; no calendar time
> fixes it in the region we are likely in.
>
> **2. GROSS-VS-NET DECOMPOSITION (ESTABLISHED, reproduced to 1e-6):**
> pooled GROSS edge **+0.0160 (P=0.942)**; the flat-2% fee term is
> **0.0096 = 59.8% of gross**. The fee model is now the single biggest
> number in the ledger ⇒ the per-market fee-RATE calibration (proposed
> 07-30, gated) is RAISED to decision-critical. Also reproduced:
> fills ≥0.90 are NET **−0.0233 (P=0.000)** — independent confirmation the
> just-deployed PRICE_NO_UPSIDE gate cuts a genuinely negative stratum.
>
> **3. WINNER'S-CURSE (headline REFUTED as overreach; trimmed claim
> stands):** per-trader split-half persistence r ≈ −0.004 (k=10);
> between-trader dispersion at/below the pure-noise 1/sqrt(n) prediction;
> random-effects tau ≈ 0. CITABLE FORM: **no detectable per-trader signal at
> current n** — NOT "nothing to select on" (the refuter showed one decisive
> number was an arm-count artifact and the design lacks power to prove
> absence). Per-trader selection stays unproven in BOTH directions.
>
> **4. PRICE-BAND 0.65–0.85 (numbers exact, framing REFUTED):** edge
> **+0.0853, P=0.9935, 140 markets** — reproduces to the digit and survives
> LOO/jackknife/split-half. BUT it was found by searching many strata
> (46 bucket tests; Poisson check: P(≥4 hits | null) = 0.201), the "only cut
> that clears both bars" claim is factually false, and this framing has the
> highest search-induced false-positive rate in the dataset. STATUS:
> **HYPOTHESIS for a fresh forward pre-registration, never a result.** At a
> true band edge near the observed, a forward test would need roughly
> 160–450 markets (band accrual ~4/day ⇒ ~6 weeks to 4 months).
>
> **DECISIONS THIS PUTS TO THE OPERATOR (report + ask, nothing changed):**
> (a) the pre-registered bar is structurally unclearable below ~+0.022 —
> redesign? (drafted option: anytime-valid e-process vs H0 edge≤0, econ
> floor as a post-significance gate — see 08-19 earlier block);
> (b) adopt the fill-band 0.65–0.85 forward pre-registration as the next
> registered test?
> (c) fee-rate calibration now decision-critical — authorize?
> All three sit alongside the still-open items below.
>
> **2026-08-19 ~15:50Z — VENUE FEE FORMULA LIVE (6f8a36c) + RE-PRICING.**
> Official formula fee = C·rate·p·(1−p), per-category rates (crypto .07,
> sports/econ/culture/weather/other .05, finance/politics/mentions/tech .04,
> geopolitics 0), VALIDATED pre-wiring vs 3,070 live charged fees (implied
> rate p50 crypto 0.0700 exact, sports 0.0500 exact). Wired as
> `fee_rate_map` (precedence rate→zero-exempt→flat; omitted = byte-identical
> legacy), builder emits `fee_rate_map.json`, readout discloses
> `fee=VENUE FORMULA`, clone refreshed — tomorrow's 12:30Z runs it.
> **RE-PRICING (flat → venue, canonical pooled set 15:45Z):**
> pooled 1,723 mkts **+0.00446/P=0.666 → +0.00518/P=0.685** — small NET gain,
> not the big revaluation hoped: the flat model overcharged high-price fills
> but UNDERCHARGED mid-price crypto. cohort2 **+0.0046→+0.0138**;
> cohort4 (crypto-heavy, 47% of flow) **+0.0028→+0.0015** (down);
> band 0.65–0.85 **+0.0812/P=0.993 → +0.0837/P=0.996** (strengthens);
> fills ≥0.90 **−0.0227/P=0.001 → −0.0092/P=0.216** — NO LONGER
> proven-negative under true fees. ⚠ FLAG (report+ask, gate UNCHANGED): the
> PRICE_NO_UPSIDE 0.98 cap's two prior rationales (zero-upside at flat fee;
> proven-negative stratum) both weaken under the venue formula — fills at
> exactly 1.000 stay guaranteed-nonpositive, but 0.98–0.99 is now merely
> terrible risk/reward, not deterministic loss. Operator decides: keep 0.98 /
> move to 0.99 / other. **RULED 2026-08-19: KEEP AS IS (0.98).** 990/3,646 tokens rated at the conservative 0.07
> unknown-category rate (pooled venue figure is thus slightly UNDERSTATED).
>
> ## 2026-08-20 SESSION CLOSE — READ THIS FIRST (supersedes blocks below for
> orientation; they remain the ledger detail)
>
> **⛔ OPERATOR RULE IN FORCE: FORWARD DATA ONLY** (2026-08-20; memory
> `feedback_forward_data_only.md`): in-sample/backtest numbers carry ZERO
> decision weight — hypothesis-generation only, never evidence, never beside
> a forward number. Decision thresholds recomputed from forward data at
> decision time. Externally-verified calibrations (venue fee formula vs live
> charged fees, CLOB labels, fill mechanics) remain usable as infrastructure.
>
> **THE FOUR FORWARD INSTRUMENTS (all live, all $0, verdicts arrive on the
> market's schedule):**
> 1. **Band 0.65–0.85 test** — `docs/BAND_PREREGISTRATION.md`, epoch
>    2026-08-19T18:00Z, e-process reject at e>=20, futility 600; daily
>    e-value in the 11:40Z cron block. Last read 2026-08-20T11:41Z: **n=11,
>    pooled −0.0216, e=0.717** (early, meaningless n; the tripwire decides).
> 2. **Shadow-bid simulator** — `docs/BIDSIM_DESIGN.md`, sink
>    `mirror3_bidsim.jsonl`. First reads 08-20 16:40Z: **16 posts, 13/13
>    resolved bids FILLED**, 3 open (queue-optimistic by design; brackets
>    truth with the conservative proxy). At ~100 resolved bids → chase-vs-
>    post proposal computed from FORWARD data on both arms.
> 3. **cohort5 qualification** (20 chain-ADMITs, single-look locks; 3
>    consumed DOES-NOT-QUALIFY) + **cohort4** FWD window — daily cron.
> 4. **probe(1) fbfd watch** — observation-only, roster=31.
> **Scout sweep #1:** 3/9 REJECT, 6 relaunched 08-20T16:41Z (pid on VPS,
> `/tmp/scout_queue3_main.log`; script copies in `scripts/vps_jobs/`).
>
> **RTDS: FIXED-ENOUGH** — 15s silent-window correction measured **3.0%
> coverage gap** (28/925 since 08-20T01:14Z) vs 11.8–13.8% before. Dual-
> socket idea PARKED as overcompensation (operator-reviewed) unless flow
> becomes binding. App-PING theory REFUTED on record — venue cycles
> connections ~6/h regardless; the fix is fast detection, not prevention.
>
> **FINAL PLAN (operator-ratified after over-correction review):** let the
> four instruments vote; finish scout sweep #1; ONE build allowed = unified
> daily scoreboard block in the cron log; everything else parked. PULLED as
> overcompensation (on review): weekly scout cadence (decide after sweep #1),
> auto-promote funnel (manual until first yield), dual-socket, fill-price
> logging (already built). **OPERATOR DECISIONS OPEN:** conditional funding
> number ("if a test passes I fund $X"); scout cadence after sweep #1.
> **TRIPWIRES:** band e>=20 or n=600; bidsim ~100 resolved; scout verdicts
> → admission proposals. PRICE cap ruled KEEP AS IS (0.98).
>
> **NEXT SESSION: start from `MB_DEEP_DIVE_NEXT_PROMPT.md` (rewritten
> 2026-08-20, self-contained).**

> ## 2026-08-19 (~17:40Z) — ALL FOUR RECS EXECUTED + OVER-CORRECTION AUDIT
> (operator: proceed with all; speed directive: verdict ASAP without losing
> function; PRICE cap ruled KEEP AS IS)
>
> **BAND TEST ARMED (the clock-starter):** `docs/BAND_PREREGISTRATION.md` +
> `scripts/band_tracker.py` — anytime-valid mixture e-process, H0 edge≤0,
> reject e≥20, econ floor post-gate, futility 600, forward epoch
> **2026-08-19T18:00Z**, venue fees, immutable lock, daily e-value in the
> 11:40Z cron. First run verified (correctly `accruing`, 0 pre-epoch leakage).
> TIMELINE HONESTY: work done in one day; the VERDICT is evidence-bound —
> e-process can fire in days if the edge runs hot, expected 2–9 weeks at
> measured band flow (~4/day), shortened by every flow boost below.
>
> **OVER-CORRECTION AUDIT (operator-ordered):** PRICE_NO_UPSIDE = NOT an
> over-correction (92 blocks/~3h, all sampled = whale 0.999→ask 1.00).
> Locks/forward_only/fee-formula = functioning. **KEEPALIVE THEORY REFUTED
> by live A/B** — 18 plain SILENT alarms in 2.9h post-PING (~unchanged),
> coverage 13.8% vs 11.8%; the venue cycles connections regardless (reference
> recorder also ~4/h). CORRECTED: `RTDS_SILENT_ALARM_S` 60→15 (outage cost
> ~61s→~16s each; expected downtime ~9–13% → ~2–3%), PING kept + task-death
> now logs. Deployed 17:33:37Z; 0 alarms in first minutes (VERIFY over hours).
>
> **ROSTER 30→31:** `0xfbfd14dd` → OBSERVATION-ONLY probe (fresh epoch,
> maker_frac 0.886 objection in ledger note; readout header now
> `15+8+6+1+1probe`; backup `chain_audit.json.pre-probe-fbfd-20260819`).
>
> **SCOUT DIVES RELAUNCHED** (9 candidates, cwd fix, stale-cache disclosed;
> `/tmp/scout_queue2.sh`, repo copy `scripts/vps_jobs/`). **MAKER-SIDE
> EXECUTION STUDY launched** (background agent: fill-rate proxy, adverse
> selection, taker-vs-maker net economics incl. the band).
>
> **2026-08-19 (~18:15Z) — MAKER-SIDE EXECUTION STUDY COMPLETE** (background
> agent, read-only; scripts `/tmp/maker_study.py` + scratchpad copies; data
> 60,011 records, 0 parse failures; fee rates known 217/1,459 comparison
> records, rest assumed 0.07 = maker-flattering per design, 0.04 sensitivity
> shown). Findings:
> * **Fill-rate floor (INFERRED, conservative snapshot proxy): 47.6%**
>   (1,090/2,290 covered OK first-buys; coverage 2,290/3,396 = 67.4%; median
>   time-to-fill 136 min). Proxy UNDERSTATES (invisible between-snapshot
>   taker hits; 4.2h median horizon right-censors).
> * **Adverse selection real overall (−8.3pts filled-vs-not win rate) but
>   ABSENT in the 0.65–0.85 band** (filled 55/66 = 83.3% vs unfilled 81.5%).
>   Severe at extremes (≥0.85: cond edge −0.0455).
> * **Band economics (n=131 labeled+covered):** taker **+0.0756** vs maker
>   unconditional **+0.0515** (0.504 fill × +0.1022 cond). Paired gap
>   **+0.0241 ± 0.0240 SE — statistically unresolved.** Maker break-even
>   needs true fill ≥ ~74% vs the 49% proxy FLOOR; rebates uncounted
>   (pro-maker).
> * **Study verdict:** the snapshot proxy cannot resolve it; a live-style
>   shadow-BID simulator (post at whale price, watch the real tape), BAND
>   ONLY, is the instrument. Registered hypothesis for it: true band fill
>   rate ≥74% ⇒ maker beats taker in 0.65–0.85. Outside the band maker-
>   unconditional measured negative everywhere. **PROPOSED (new build,
>   operator gate): band-only shadow-bid simulator.**
> * Independent corroboration: the band's taker edge shows +0.0756 on this
>   cut too (different denominator than the 143-mkt +0.0837; consistent).
>
> **2026-08-20 (~01:15Z) — SHADOW-BID SIMULATOR LIVE** (operator 'proceed';
> design pre-registered in `docs/BIDSIM_DESIGN.md` BEFORE data). Band-only
> [0.65,0.85) maker-execution measurement: shadow BID at whale price on
> roster first-buys; FILL = any print <= bid (RTDS firehose, all traders) —
> QUEUE-OPTIMISTIC by design, bracketing the snapshot proxy's 47.6% floor;
> EXPIRE 24h; one bid per (trader,token); restart-safe rehydration; SHARED
> registry (chain + rtds paths). Sink `mirror3_bidsim.jsonl`, env
> `MIRROR3_BIDSIM=1`. Registered decision hypothesis: **true band fill rate
> >= 74% => maker beats taker** (taker +0.0756 vs maker cond +0.1022, n=131).
> 65/65 pytest; deployed + ENABLED 2026-08-20T01:14:19Z, 0 errors; backup
> `copy_watcher.py.pre-bidsim-20260819`. Sink populates on the first in-band
> whale buy (sparse — hours, not minutes).
>
> **⛔ RULE (operator, 2026-08-20): FORWARD DATA ONLY.** Old/in-sample data
> carries ZERO decision weight. In-sample edges, P-values and rankings may
> motivate a hypothesis or design an instrument — never appear as evidence,
> never share a headline with forward numbers. All decision thresholds are
> recomputed from FORWARD data at decision time (incl. the bidsim
> chase-vs-post comparison: forward taker edge vs forward maker fill×edge,
> not the in-sample break-even). Externally-verified calibrations (venue fee
> formula vs live charged fees, CLOB labels, fill mechanics) remain usable as
> infrastructure — stated interpretation, operator-correctable. The running
> forward instruments (band tracker, bidsim, cohort4, cohort5) are already
> compliant by construction. Memory: `feedback_forward_data_only.md`.
>
> **STILL OPEN (unchanged, not dropped):** stopping-rule fix (pre-committed
> single evaluation point per cohort — FLAGGED URGENT, cohorts now crossing
> power daily; operator go still needed); backfill poison-batch;
> `end_date_iso` NULLs; 123h force-exit loop; docs-sync PR (pushed, needs
> operator click: `claude/mb-docs-sync-20260723`); label residual 730/1062
> tokens (re-run `shadow_label_supplement.py --write` after resolutions).

> ## 2026-07-23 (~02:30Z) — THE DAILY READOUT WAS STILL FLATTERED. FIXED.
>
> **The 07-22 label fix never reached the thing that reports numbers.** The
> CLOB supplement landed in the gamma cache; `scripts/shadow_readout.py` built
> its token→outcome map from `markets` ONLY (deliberately, per its own 07-15
> anti-stale-cache landmine) and never read that cache. So the 12:30Z readout —
> the artifact that fires the ALERT and carries the pre-registered verdict —
> kept printing the flattered edges. Two commits, both on branch head:
> * `54540e0` — merge the supplement **UNDER** the DB (DB wins, supplement
>   fills holes); every block now prints `labels: DB=n +supp=n, k/N shadow
>   tokens still unlabelled`; a missing/unreadable/zero-label supplement makes
>   the readout **REFUSE** (loud line in the durable log, ALERT untouched,
>   rc=2) rather than silently revert to the flattered source.
> * `28370a0` — `scripts/shadow_label_supplement.py`: the 07-22 supplement was
>   keyed on the TRADERS' markets and covered **21 of 405 shadow tokens**. This
>   labels the SHADOW set directly (unlabelled token → condition_id from
>   `markets` → CLOB, derivation reused verbatim from `resolution_backfill`).
>   Live: 262 targets, **262/262 reachable, 135 newly labelled**, 127 genuinely
>   still open, **0 CONFLICT**; cache 213,623 → 213,758, backup
>   `gamma_resolutions.json.pre-shadow-supplement-20260723`.
>   Pre-write cross-check vs an INDEPENDENT source: 80 shadow markets the DB
>   had already resolved, re-fetched from CLOB → **compared=80 AGREE=80
>   DISAGREE=0** (non-emptiness asserted).
>
> **WHAT THE COMPLETE-LABEL READOUT SAYS** (dry-run 02:29Z, scratch
> `--out`/`--alert`, durable log untouched; same instant, same records — arms
> non-empty and identical, first-buys 107/118/130/45):
>
> | line | DB-only (old) | DB+supplement (now) |
> |---|---|---|
> | cohort1(15) REDUCED | 33 mkts +0.0607 P=0.854 | **42 mkts +0.0335 P=0.739** |
> | cohort2(8) | 25 mkts +0.0331 P=0.707 | **39 mkts +0.0210 P=0.648** |
> | cohort3(6) | 0 resolved | **7 mkts −0.0258 P=0.141** |
> | benched(1) | 4 mkts −0.0176 | **19 mkts +0.0717 P=0.764** |
>
> cohort3 reproduces the prior session's independently-derived corrected figure
> (**−0.0258 on 7**) to the digit, from a different code path — that is the
> cross-check that the pipeline is now reading the true labels.
>
> **⚠ TWO THINGS THE OPERATOR MUST SEE:**
> 1. **cohort2 is now POWERED and FAILS.** 39/30 resolved, edge +0.0210
>    (barely over the +0.02 floor), **P=0.648 ≪ 0.95 ⇒ NOT DEMONSTRATED**,
>    concentration `0xbaa2bcb5…39%`. The ALERT will fire on the next real
>    12:30Z run and asks for the per-trader breakdown + LOO before any verdict.
> 2. **The benched bum is drifting toward his re-admission bar** —
>    forward-since-bench **+0.0717 on 19 resolved, P=0.764**. Bar is edge ≥
>    +0.02 **AND P ≥ 0.90 on ≥ 20**: n is one short and P is well under. **No
>    action, no proposal yet** — noting it so nobody is surprised.
>
> **Residual gap: 235 of 405 shadow tokens still unlabelled** — 127 markets
> genuinely still open, the rest have no condition_id anywhere in `markets`
> (97 tokens as measured 02:25Z) and need a token→market lookup to reach.
> Re-run `shadow_label_supplement.py --write` after new resolutions land; the
> readout needs no further change, it picks up whatever the cache gains.
>
> **ADMIT RE-REVIEW UNAFFECTED BY THAT CACHE WRITE** — `chain_deep_dive.py`
> preloads the gamma cache ONCE per process (`:1259`) and the re-review is a
> single long-lived invocation, so all 20 traders are graded on the same
> 07-22 snapshot (keys=213,623). Internally uniform; verified alive after the
> write (3 JSONs, pid 31257). `scripts/rereview_diff.py` (`16af40b`) is the
> strict completion check: anything short of roster-complete is rc=4 naming
> the uncompared addresses — "FLIPPED: 0" now only prints next to a
> roster-complete compare. Interim (3/20): all ADMIT→ADMIT, edges
> +0.0382→+0.0382, +0.0220→+0.0205, +0.0237→+0.0209.

> ## 2026-07-22 SESSION CLOSE — READ THIS FIRST
>
> **HEADLINE: every edge number this lane has ever reported was FLATTERED.**
> The `markets` resolution labels were incomplete, and the missing slice was
> systematically NEGATIVE in every cohort. Corrected (CLOB-verified):
> cohort1 **+0.0604 → +0.0315**; cohort2 **+0.0567 → +0.0399**; cohort3 was
> reported "no resolved data" but actually had **7 markets at −0.0258**;
> benched bum −0.5049 (n=2). Nothing moved toward passing; every P dropped.
>
> **ROOT CAUSE (shared infra, affects ALL bots):** `polymarket-ingestion`
> crash-looping (334 restarts); its resolution queue hit the 300s statement
> timeout every cycle, so the backfill wrote **nothing for 56h** against a
> 38,696-market backlog. Fixed the query cost with a partial index
> `idx_markets_unresolved_enddate` (matches the queue predicate + end_date
> ordering; additive, CONCURRENTLY). Index alone was NOT enough — it needed
> fresh stats; autoanalyze on `markets` 17:45Z supplied them and the queue
> **succeeded 18:01Z, first time in 56h**.
>
> **LABEL FIX THAT ACTUALLY UNBLOCKED US — CLOB SUPPLEMENT.** The shared
> backfill drains ~3 markets/30min (poison-batch: `end_date ASC` re-chews
> permanently-unresolvable markets) ⇒ ~270 days, unusable. Instead supplemented
> the gamma cache straight from CLOB (source proven: 196/196 verified, 0
> unreachable, 0 mismatches): **+14,791 labels, gamma 198,832 → 213,623, ADMIT
> label gap 32.0% → 1.9%** (residual = 610 genuinely-open + 330 CLOB-
> unreachable, disclosed). Backup `gamma_resolutions.json.pre-clob-supplement`.
>
> **WHY THE TRADER VERDICTS ARE SUSPECT:** the deep-dive grades skill from
> `DB + gamma cache` — the SAME degraded source. **26.5% of all ADMIT skill
> evidence was unlabelable**, worst on three cohort-3 members graded on ~50%
> of their evidence (`0x216509be` 53%, `0x7c3db723` 51%, `0xe542afd3` 50%).
> That plausibly explains cohort-3's live edge landing NEGATIVE.
> ⇒ **ADMIT RE-REVIEW of all 20 ADMITs RUNNING** (`/tmp/admit_rereview3.sh`,
> out-dir `deep_dive_rereview/`, originals preserved for the before/after diff;
> `<== FLIPPED` marks any verdict that fails complete labels). REJECTs are
> rate-based ⇒ unaffected. 5 label-starved INSUFFICIENTs may have been wrongly
> shelved.
>
> **EXECUTED THIS SESSION:** run-4 CLEAN-FINISH (28/28, 6 ADMIT/9 REJECT/13
> INSUFFICIENT) → cohort-3 promotion + bum time-out in ONE fenced restart
> (roster 25→30, verified `roster=30`, 0 alarms) → fill-cache proof gates (i)
> and (ii) BOTH PASS → cohort-1 active-trader vetting (7): **6 ADMIT / 1
> INSUFFICIENT**, and the INSUFFICIENT is the benched bum (edge +0.0031,
> P=0.678 on his CLEANEST sample) — **his time-out is independently vindicated
> by three lines of evidence**. NOTE those 6 ADMITs were ALSO graded on
> incomplete labels ⇒ they are in the re-review (roster grew 14 → 20).
>
> **CROSS-BOT (WB relay):** master release `20260721_232241` (41 commits, a
> month) went live on `polymarket-mirror` 03:27Z. **Verified on MB's own
> terms:** scanning (`elites=300 open_positions=9`, ~2.8s cycles), gates
> blocking, exits + zombie reap working, exposure reconciling, state restored,
> **paper mode confirmed** (`simulation_mode=True`, canary 0). Calibrator
> healthy post-c12: `fitted on 5000 resolved predictions`, fts fitted, 267
> `mirror_calibrated` emissions adjusting BOTH directions. All error signatures
> PRE-EXISTING and mostly improved per-hour (adverse slippage 29→4.5/h).
> **KEEP THE RELEASE — no rollback.** One pre-existing anomaly: a 123h position
> stuck in a force-exit retry loop, blocked by the 10% adverse-slippage guard
> (correct protection, but it loops).
>
> **STILL OPEN / NOT FIXED (flagged, need operator go — shared infra):**
> (a) backfill poison-batch ordering (~3/cycle ⇒ backlog never drains);
> (b) `end_date_iso` NULL on **56%** of markets ⇒ starved by NULLS-LAST AND
> invisible to the health check, so the true backlog exceeds the reported
> 38,696; (c) the stuck force-exit loop; (d) master docs-sync PR for MB_STATE.

> **2026-07-21 20:07Z — BATCH-BOUNDARY LEDGER MUTATION EXECUTED (operator-
> authorized "fold into and proceed" + "proceed"). ROSTER DELTA (protocol-
> logged):** run-4 finished CLEAN 07-20 23:52Z ([28/28], 6 ADMIT / 9 REJECT /
> 13 INSUFFICIENT; final ADMIT set ≡ the queued 6, JSON-verified). ONE ledger
> edit + readout-clone refresh (`21ad7ba`) + ONE fenced watcher restart:
> **(a) COHORT-3 PROMOTED** — 6 ADMITs (0xf705fa04 graduates from probe,
> 0x7c3db723, 0xe542afd3, 0x216509be, 0x2ee04b8b, 0xa6a856a8), own epoch
> 2026-07-21T20:05:30Z, probe emptied; **(b) BUM BENCHED** — 0x44886115 moved
> cohort1_original→`benched` (from_cohort=cohort1, TIME-OUT; reason: chain-
> verified drag edge −0.1051 P=0.107 on 22 resolved, 46% conc; re-admit bar
> pre-registered: forward-since-bench edge≥+0.02 AND P≥0.90 on ≥20 resolved,
> operator go). clean=30 == c1(15)+c2(8)+c3(6)+benched(1); backup
> `chain_audit.json.pre-cohort3-20260721`. Verified: `roster=30` in watcher
> log, 0 alarms, VPS load_cohorts OK. cohort1 now prints REDUCED/NO-VERDICT
> (its pre-registered verdict stays the LOCKED pre-bench line: POWERED at
> 12:30Z 07-20, NOT DEMONSTRATED). **Fill-cache PROOF GATE (i): PASS**
> (bounded A/B, 3 addrs, fill-multisets IDENTICAL, pinned [90439826,
> 90639826]). **Gate (ii) RUNNING** (amended, disclosed: flag-off vs flag-on
> FRESH dives of 0x7744bfd7 — inactive 32d → head-drift-immune; the
> pre-registered compare-vs-07-17-JSON is impossible after 4d label drift);
> verdict marker `/tmp/proof_gate_ii_VERDICT.txt`. **NEXT AUTO-STEP:** on gate
> verdict → launch `/tmp/launch_c1vet.sh` (cohort-1 7-active vetting,
> gate-conditional cache flag; expected ~2A/2R/3I at run-4 base rates). Then
> deepen wave (freeze set: 13 run-4 INSUFFICIENTs ⊇ the 9 confounded) →
> 0x70d94a solo. Watch next 12:30Z: header must read 15+8+6+1benched.

> **➡ NEXT SESSION: start from `MB_DEEP_DIVE_NEXT_PROMPT.md` (fresh, self-
> contained, 2026-07-19) — it has the current state, armed queues, procedures,
> and this session's landmines. The dated blocks below (9–14 newest first) are
> the ledger detail; the blocks before them are prior-session history, kept for
> provenance. Do NOT re-derive from the old blocks — the prompt + blocks 9–14
> are current.**
>
> **2026-07-19 SESSION CLOSE (one-paragraph state):** run-4 (fair-params
> re-adjudication, code `27ee79b`) at **19/28, alive (pid 3269649)**; promotion
> queue = **6 chain-verified ADMITs** (`0xf705fa` graduates from probe +
> `0x7c3db723`/`0xe542afd3`/`0x216509be`/`0x2ee04b8b`/`0xa6a856a8`), executes
> as **cohort3** in ONE fenced watcher restart at the batch boundary (operator
> word). Shadow watcher healthy (roster 25, 0 alarms). Readout generalized to
> `cohort<N>` (blocks 11,13,14) so the promotion works. Fill-cache + multi-
> sweep BUILT behind `--fill-cache-dir` (block 10) with a pre-registered proof
> gate before the deepen wave uses it. **Reviews this session: two adversarial
> workflows + one root-cause audit → ~15 defects found & fixed AT ROOT, every
> one in not-yet-exercised code (the running readout + live run-4 were clean
> throughout).** All work pushed to the branch (`329444e`+). NO live batch-end
> watcher survives session end — next session re-checks run-4 on start.

> **2026-07-17 UPDATE (local steward session; operator-approved "proceed with
> all action items") — RUN-3 KILLED, RUN-4 (merged, fair-params) LAUNCHED
> 12:34:57Z after a 3-round blind adversarial review chain. READ THIS FIRST.**
>
> 1. **RUN-4 IS THE ACTIVE BATCH**: 28 traders (probe `0xf705fa` line 1 →
>    band 8 → 19 not-yet-done), code `/tmp/mbre`@`27ee79b` (new-params
>    defaults: receipt-free >1000, decisions/day ≤25, flat-share <0.60),
>    python `/opt/polymarket-ai-v2/venv/bin/python` (NOT /tmp/mbre/venv —
>    doesn't exist; run-3's cmdline used a RELATIVE venv path), log
>    `/tmp/deep_dive_run4.log`, summary `_summary_run4.json` (spans ALL runs
>    sharing the out-dir — not run4-only). First-minute checks passed
>    (28 traders, SKIPDB=0, PID 3269649 detached). Local watchers: batch-end
>    poller + probe-JSON watcher (steward session scratchpad).
> 2. **WHY the kill (operator-approved)**: run-3 ran pre-rework code
>    (`07e7296`, old cap-200) — its band rejects needed re-testing anyway —
>    AND the blind review found run-3's INSUFFICIENTs confounded (see 3).
>    Run-3 got through 8/27 before the kill (traders 1-8 have JSONs; last
>    3: `0xa58d4f` REJECT 1194/day [stays — >1000 in both regimes],
>    `0xdf2e12` REJECT 390/day [band → in run-4], `0x0c0e27` INSUFFICIENT).
> 3. **NEW LANDMINES (blind-review chain findings, ALL MITIGATED for run-4)**:
>    (a) **status="hft" API cache (~500 rows) makes ADMIT deterministically
>    unreachable** — tok2cond comes ONLY from cached API rows → lifetime
>    first-buys unlabelable → span<60d → skill can't clear; AND unmapped
>    tokens inflate decisions/day (each token = a "market") → FALSE-REJECT
>    through the hard decision gate. ALL 34-HFT-borderline caches were hft.
>    Fix applied: renamed 27 starved caches to `.hft-bak` → run-4 re-fetches
>    full histories (missing-cache path disables the HFT short-circuit).
>    (b) **gamma_resolutions.json was 6 days stale** — suppresses (never
>    corrupts) recent labels; refreshed 12:24Z via
>    `backfill_resolutions_gamma.py` (+699 labels, 198,832 total, 0 errors).
>    ⇒ **run-1/2/3 INSUFFICIENT "un-gradeable/underpowered" verdicts are
>    confounded by (a)+(b) — they are DEEPEN candidates under fresh
>    caches/labels, backlog, operator word.**
>    (c) `ssh 'cmd &'`-style launches: stale /tmp logfile owned by another
>    user = silent no-launch (bit us 12:12Z; use fresh log names + the
>    LAUNCHED/ABORTED marker pattern + alive-check after 10s).
> 4. **ROSTER LEDGER DELTA (deep-dive candidate roster, protocol-logged)**:
>    ADD `0xdf2e12c6a5…` to the band re-test (390/day, run-3 old-cap
>    REJECT). ADD `0xed107a85a4…` (trader 5) to run-4 (its INSUFFICIENT was
>    (a)+(b)-confounded; verdict NOT code-invariant under new gates). No
>    live-cohort changes — cohort-2 (8) + probe (1) unchanged, watcher
>    untouched (canary 0 throughout).
> 5. **Verified this session**: 8 ADMIT JSONs ≡ cohort-2 ledger (set
>    identity); deployed watcher blob ≡ `336f6a4` (hash match); cohort-2
>    OK-rate 69.2%/conc 69% SURVIVES (trader,token)-dedup; 47-list files
>    durably copied into `deep_dive/`; 9 pre-rerun verdicts backed up to
>    `deep_dive/pre_band_rerun_20260717/`.
 > 6. **OPEN**: [operator] cohort-2's 8 live ADMITs never faced the new
>    decision/flow gates — shadow measures copyability empirically (chosen
>    for now); uniform re-dive = backlog. [next] run-4 completion → tally →
>    ADMIT proposals by NAME only; probe cross-check vs 7.6 decisions/day,
>    0.16 flat_share on first JSON. Full procedure + review findings:
>    steward scratchpad `band_rerun_runbook.md` (v3).
> 7. **PROBE `0xf705fa` = ADMIT under fair params (run-4 trader 1, ~13:45Z
>    2026-07-17) — CROSS-CHECK PASSED, PROMOTION QUEUED TO BATCH BOUNDARY
>    (operator "proceed").** Verdict: complete sweep 135,493 fills, 0
>    mismatch, 100% of 28,926 API-BUYs chain-backed, skill +0.036 P=1.00 on
>    1,838 mkts, decisions/day 7.66 (exact match vs pre-registered 7.6),
>    flat_share 0.39 < 0.60 (differs from the ledgered 0.16 — BENIGN: old
>    figure was the recent-window API sample, run-4 computes lifetime chain
>    positions; both far under the bar). Live shadow agrees: 82% OK-rate at
>    0.9s lag since its 00:50Z probe epoch. PROTOCOL: promotion (probe →
>    cohort-3 w/ own epoch) happens in ONE batched watcher restart with any
>    further run-4 ADMITs at batch end — a mid-run restart would reset
>    FirstBuyDedup for zero informational gain (probe already has its own
>    epoch + readout line; collection is identical under either label).
> 8. **STACK-VS-FIRST-BUY TEST (operator-requested 2026-07-17) — PRE-REGISTERED,
>    retrospective arm VOID, forward arm ARMED.** Question: for a stacker, does
>    the edge live in the first entry or the accumulation (would re-buying beat
>    our one-bet-per-market policy)? Retrospective attempt on the probe's API
>    cache FAILED its own cross-check gate twice (could not reproduce the
>    deep-dive's 1,838 mkts @ +0.036) — ROOT CAUSE: the data-api record is a
>    structural SUBSET of chain truth (28,926 API BUYs vs 60,576 chain BUYs for
>    0xf705fa; deep-dive tier-2 verifies the subset is honest, NOT complete).
>    NO VERDICT from API-based retrospectives — landmine: never grade a
>    high-rate trader's entry pattern from /activity alone. UNVERIFIED
>    descriptive residue (both estimand framings agreed): his stack VWAP sits
>    ~1.5c WORSE than his first price (P(better)=0.000) but dollar-weighting
>    his sizing BEATS equal-weight — i.e., size-as-conviction looks real,
>    price-improvement-by-stacking does not. Do not act on this.
>    **FORWARD TEST (pre-registered, runs on shadow data):** when >=30 resolved
>    (trader,token) positions carry >=2 BUY records, compute per position
>    Delta_exec = (outcome - VWAP of recorded asks) - (outcome - first ask),
>    market-clustered bootstrap, seed 7. Delta>0 @ P>=0.95 -> re-buy policy
>    becomes a DESIGN PROPOSAL (touches the one-bet-per-market guard: operator
>    decision); else first-buy-only validated. Every re-buy is already recorded
>    with executable quotes — zero new collection needed.
> 9. **OPERATOR APPROVALS (2026-07-17 ~15:20Z, "proceed") — EXECUTE AT RUN-4
>    BATCH BOUNDARY (the local batch-end watcher fires the sequence):**
>    (a) PROMOTE `0xf705fa` (probe ADMIT) + `0x7c3db723f1d4…` (run-4 fresh
>    ADMIT, 395/day: sweep complete, 0 mismatch, 100%/118,606 API-BUYs
>    backed, skill P=1.0 on 4,280 mkts) — implementation: clean 25→26,
>    extend `probe.addresses` with 0x7c3db7 (readout's multi-address probe
>    group VERIFIED to work; group epoch stays 00:49:56Z — 0x7c3db7 has zero
>    prior shadow records so nothing pools, F1-safe), ONE
>    mirror3_shadow_deploy.sh restart, verify roster=26 + canary + next
>    12:30Z readout runs clean. Any FURTHER run-4 ADMITs join the same
>    single restart. (b) DEEPEN WAVE after run-4 exits: the 9 confounded
>    INSUFFICIENTs (list in steward runbook) — rename non-ok caches, bare
>    roster file, detached launch, --max-receipts 30000. (c) THEN
>    `0x70d94a` solo deepen at --max-receipts 120000 (~4h receipts).
>    Sequence strictly serial (one batch at a time on the shared RPC).
> 10. **SPEEDUPS 1+2 BUILT (operator-approved 07-19; option 3/paid endpoint
>    REJECTED): `scripts/chain_fill_cache.py` (`b67fe20`)** — persistent
>    per-address chain-fill cache (+receipt-side memory, key tx|token_id)
>    + populate_multi ONE-sweep-for-N-addresses; wired into chain_deep_dive
>    behind `--fill-cache-dir` (default OFF = byte-identical old path,
>    differentially proven by adversarial review). 5 review findings fixed
>    (silent coverage hole on non-adjacent merge; reorg margin on the write
>    path; cache-file collision with API caches; gap error-frac denominator;
>    malformed-blob fallback). 65 tests green.
>    **EMPIRICAL PROOF GATE (pre-registered, MUST pass before any batch uses
>    the flag):** at run-4 exit, on the idle RPC: (i) bounded A/B — multi
>    sweep vs per-addr sweeps over the same block range for 2-3 addrs →
>    fill sets must be IDENTICAL; (ii) full re-dive of one completed trader
>    via populate+cache → verdict AND tier-1/2 counts must match its
>    existing JSON exactly. Only then does the deepen wave run with
>    `--fill-cache-dir` (expected ~10-25x cheaper sweeps). Failure of
>    either → deepen wave runs FLAG-OFF (old path), no function lost.
> 11. **COHORT-3 PROMOTION PREREQUISITE BUILT (operator "go" 07-19; `cdf01fb`):**
>    `shadow_readout.load_cohorts` generalized from hardcoded cohort1/cohort2/
>    probe to read any `cohort<N>` key (own epoch, never pooled) — needed
>    because the daily readout couldn't represent a 3rd cohort. Differential-
>    IDENTICAL on the live 16+8+1probe roster (offline AND a VPS dry-run in
>    the real venv). Adversarial workflow (4 lenses × verify) caught a REAL
>    HIGH defect I introduced — an empty `cohort<N>` addresses list slipped
>    every guard → `filter_traders("")` pools the WHOLE roster mislabeled →
>    could fire a false POWERED go/no-go alert (the 2026-07-15 finding-A
>    silent-pooling class). FIXED at root (empty admitted cohort now raises,
>    matching HEAD's `if not c2`); self-test PASS incl the new case; a
>    simulated real promotion (16+8+6, probe emptied) loads clean. Cron
>    auto-adopts at 12:30Z (branch-pinned refresh THEN roster read = new code
>    + new roster always consistent). **BATCH-BOUNDARY PROMOTION (armed):**
>    at run-4 exit, the run-4 ADMITs (6 so far: 0xf705fa graduates from probe
>    + 0x7c3db7/0xe542af/0x216509/0x2ee04b/0xa6a856; +any more before t28)
>    become cohort3 via ONE fenced mirror3_shadow_deploy.sh restart —
>    procedure in steward scratchpad `cohort3_promotion_procedure.md`
>    (chain_audit.json: clean 25→30, add cohort3 key w/ own epoch, empty the
>    probe key; invariant clean==union checked offline before deploy).
> 12. **READOUT (07-19 14:56Z VPS dry-run, fresh labels):** cohort1(16)
>    28/30 resolved edge +0.0440 P(>0)=0.720 conc 0x448861…37%; cohort2(8)
>    14/30 edge +0.0432 P(>0)=0.648 conc 0xbaa2bc…35%; probe 0 resolved.
>    Both UNDERPOWERED, both drifting mildly POSITIVE as markets resolve
>    (cohort1 +0.031→+0.044 within the day). No verdict; no alert.
> 13. **TRIPLE-BLIND REVIEW of all session code (07-19; 3 blind lenses ×
>    adversarial verify): 5 confirmed findings, ALL in NOT-YET-EXERCISED code
>    — the live-critical paths (running readout on the live roster, flag-off
>    run-4) came back CLEAN. All 5 FIXED + committed (`7f5c771`,`d2bca15`,
>    `8b3ce27`):**
>    - [med, stack_vs_firstbuy_forward #2/#4/#5] the forward test read RAW
>      /price best_ask+verdict, bypassing `az.repair_records` (the /book-ladder
>      repair the money-gate readout treats as ground truth) → priced the
>      estimand off a flattered/gate-dodging quote; AND powered the verdict on
>      POSITION count while the bootstrap clusters by TOKEN → cross-trader
>      token overlap could fire a "POWERED" verdict on ~2 markets; no
>      concentration disclosure. FIX: canonical repair pipeline + power on
>      DISTINCT token-clusters + inline concentration. Pre-registration
>      corrected BEFORE any data (tool not yet run). 4 new self-test asserts.
>    - [low, shadow_readout #1] an intra-group DUPLICATE address passed the
>      cross-group set() guards but broke the leave-one-out `rest` →
>      filter_traders("") → whole-roster pooling in the LOO line. FIX: fail
>      loud on any intra-group dup.
>    - [low, chain_fill_cache #3] a superset re-populate summed leaf_ok
>      (double-count) → could understate the lossy-gap rpc_err_frac and mask
>      an incomplete sweep. FIX: replace-on-superset (don't sum).
>    Nothing here changed a currently-live number. Method note: the two
>    workflow reviews this session (readout cohort<N>, then this triple-blind)
>    each caught a real defect the single-pass reviews missed — the money-gate
>    surface warrants the multi-lens adversarial pass.
> 14. **ROOT-CAUSE AUDIT of all fixes (07-19; classify + adversarial challenge
>    per fix): most fixes root-cause+clean; 3 real residuals fixed at ROOT
>    (`2856fe8`):**
>    - **[the important one] the empty-cohort raise + intra-group-dup raise
>      were TWO reactive per-path guards on the SAME footgun** — cohort_readout
>      passing empty members into filter_traders (where ""=all records) → whole
>      roster pooled+mislabeled. Added the CLASS fix: cohort_readout treats
>      empty members as ZERO records at the single chokepoint every group +
>      every LOO flows through. Kept the two fail-loud guards (ledger-integrity
>      value). Any future empty-member path is now safe, not just the two we
>      enumerated. Defense-in-depth self-test added.
>    - fill_cache merge_ranges: superset-replace only covered the reachable
>      shape; a PARTIAL overlap still summed leaf_ok → now refuses anything but
>      strict-adjacent-or-superset (general double-count closed).
>    - forward test: `cluster_bootstrap_p` was copy-pasted → now calls the
>      canonical `az.cluster_bootstrap_p` (a duplicated verdict statistic can
>      drift). Removed the copy + unused `random` import.
>    - SKIPPED (documented, not a band-aid): element-wise dict validation in
>      chain_fill_cache.load() — a contrived corruption the write path never
>      produces, degrades gracefully (one trader INSUFFICIENT, self-heals), not
>      worth an O(n) scan on every load. All self-tests + 65 pytest green.

> **2026-07-14 PM UPDATE (local steward session; VPS-direct SSH, operator-
> approved per-command) — CHAIN DEEP-DIVE GATE BUILT, REVIEWED, VALIDATED,
> AND THE 47-BATCH IS RUNNING.**
>
> 1. **`scripts/chain_deep_dive.py` — the roster-admission gate — is DONE**
>    (§5 TO-DO item 1). Four tiers: T1 lifetime dual-era fill reconstruction
>    (V1 OrderFilled maker+taker both exchanges + V2 fill topic owner-filtered;
>    V1 direction implicit in the USDC leg, V2 direction from tx receipts,
>    capped); T2 API↔chain reconciliation BOTH directions (tx-exact matcher,
>    BUY-only candidates); T3 skill re-grade on chain data vs the SAME
>    walk-forward hire bar; T4 forensics (counterparty/wash, maker-taker +
>    TRUE lifetime rate = the fair HFT test, sampled copier probe, pUSD funder).
>    Reuses the audited siblings as-is; read-only; no shared-module edits.
> 2. **Pre-registered verdict (locked in the docstring): REJECT only on an
>    AFFIRMATIVE contradiction (mismatch / fabrication / adequately-powered
>    NEGATIVE chain edge) or a MEASURED infeasibility (true rate > cap);
>    every evidence gap or unverified forensic suspicion (short-span/underpowered
>    skill, thin backing, too-few API BUYs, ts-uncomputable, receipt-cap, wash,
>    copier) → INSUFFICIENT-EVIDENCE. ADMIT is a PROPOSAL to the operator for a
>    cohort — never auto-add, never pooled with cohort-1.** Chain wins; a gap is
>    never an accusation (binding operator rule 2026-07-14).
> 3. **Validation:** 61-agent-style adversarial review (5 lenses → adversarial
>    verify) surfaced **16 confirmed findings; all fixed** (top: T2A folded
>    SELLs/unknown into BUY candidates → could mask a lie / false-REJECT — the
>    core chain-wins bug). A bounded integration smoke on `0xd1acd3925d` then
>    caught **2 more** (API-buy windowing → false 99% FABRICATION; receipt-cap
>    → false not_found) — both fixed. Re-smoke CLEAN: tier-2 backing 1.00,
>    direction fully resolved, correct INSUFFICIENT (recent markets unresolved).
>    `--self-test` (16-case verdict table) + 23 pytest green LOCAL + VPS venv
>    (web3 7.5.0); siblings unregressed (53). Commits on this branch through
>    `0231f2c`.
> 4. **[SUPERSEDED by the ROSTER LEDGER's EXECUTION UPDATE below — run-1 was
>    killed at 12/47 and relaunched as run-2 (rps 8, receipt short-circuit);
>    the summary is now `deep_dive/_summary_run2.json`. The summary CODE since
>    `5eae137` rebuilds from the on-disk JSONs after every trader (crash-
>    durable, spans all runs sharing the out_dir) — but the IN-FLIGHT run-2
>    loaded pre-fix `d6276f7`, so its file covers only its own 35 at run end;
>    the 47-wide view = the per-trader tally one-liner below, or any later
>    run under `5eae137`+.]**
>    ~~THE 47-BATCH IS RUNNING~~ (originally launched 2026-07-14 19:31 UTC, detached
>    setsid+nohup, reparented to PID 1, rps=4, max_receipts=30000): 9 cohort-2
>    (`readjudicate.json` VINDICATED) + 38 (`deep_dive_extra_38.txt` = grey-4 +
>    the 34 HFT-borderline incl `0xa6a856a8c8…`) = 47. Log `/tmp/deep_dive_batch.log`;
>    per-trader JSONs + `_summary.json` land in the polymarket-owned
>    `/opt/pa2-shared/mb_copyable_data/deep_dive/` (mb_copyable_data itself is
>    root-owned — see landmine). Est. ~17-25h. **NEXT SESSION: collect
>    `deep_dive/_summary.json`, review the ADMIT/REJECT/INSUFFICIENT split;
>    admissions to any cohort need the OPERATOR'S WORD (own start date, separate
>    readout, never pooled with cohort-1). INSUFFICIENT = deepen (raise
>    --max-receipts / widen window / --refresh cache), NEVER accuse.**
>    Monitor cmd: `wc -l /tmp/deep_dive_batch.log; ls
>    /opt/pa2-shared/mb_copyable_data/deep_dive/0x*.json | wc -l;
>    journalctl -u polymarket-mirror3 --since '1 hour ago' | grep -cE 'CANARY ALARM|QUOTE SANITY'`
>    (the batch shares the tenderly endpoint with the LIVE mirror3 watcher —
>    watch that canary count stays 0).
>
> **ROSTER LEDGER + add/subtract protocol (operator directive 2026-07-14):**
> the steward MAY add/subtract candidate traders from the deep-dive roster
> autonomously, but EVERY add/subtract MUST be logged here + in
> `MB_DEEP_DIVE_NEXT_PROMPT.md` with the delta + reason (nothing enters/leaves
> the roster invisibly). Admissions still need the operator's WORD (this
> authority is over the CANDIDATE roster, not who joins a live cohort).
>   - **WAVE-1 = 47** (roster UNCHANGED): 9 cohort-2 (`readjudicate.json`
>     VINDICATED) + 4 grey (`readjudicate_grey2.json` VINDICATED) + 34
>     HFT-borderline (`deep_dive_extra_38.txt` = grey-4 ∪ the 34, dedup).
>     **EXECUTION UPDATE 2026-07-15:** run-1 (rps 4, code `0231f2c`) was KILLED
>     at 12/47 done (results preserved) and RELAUNCHED as **run-2** on the
>     remaining 35 (`/tmp/deep_dive_remaining.txt`) at **rps 8** with the
>     receipt short-circuit (`d6276f7`). Run-1 tally: **8 ADMIT / 3
>     INSUFFICIENT / 1 REJECT-uncopyable** (all 12 JSONs parse-verified).
>     Run-2 writes `deep_dive/_summary_run2.json` (its 35 ONLY) — **NO durable
>     artifact aggregates all 47**; tally from the per-trader JSONs:
>     `python3 -c "import json,glob,collections; print(collections.Counter(json.load(open(p))['verdict'] for p in glob.glob('/opt/pa2-shared/mb_copyable_data/deep_dive/0x*.json')))"`
>     The 3 INSUFFICIENT (skill under the P bar on full data) are DEEPEN
>     candidates, not re-run-blindly: their gap is resolved-market count, so
>     re-dive them only after more of their markets resolve.
>   - **CANDIDATE-ADD MENU for WAVE-2 (identified 2026-07-14 PM, NOT yet run —
>     deferred so a 2nd batch doesn't double RPC load on the shared tenderly
>     endpoint while wave-1 + the live mirror3 watcher run):**
>     `0x6bab41a0dc40d6dd4c1a915b8c01969479fd1292` (run-1 strong: 264 P1 mkts
>     +0.067 → 109 P2 +0.089 P=0.99) and
>     `0x4dfd481c16d9995b809780fd8a9808e8689f6e4a` (run-1 politics: 17 mkts
>     +0.410 P=1.00) — both cached, both confirmed NOT in wave-1; plus the
>     ~38 ALL-universe scope-outs (§0 item 5, less concrete). Run wave-2 AFTER
>     wave-1 completes; log the exact added addresses here when you do.
>   - **COHORT-2 ADMISSIONS — LIVE (2026-07-15, operator go "batch all 8"):**
>     the first 8 deep-dive ADMITs (all VERIFIED: 100% API-BUY backing, 0
>     mismatch, chain skill P 0.925-1.000 on 175-8386 mkts, rate 38-170/day
>     tailable, no wash/copier) were ADMITTED to the live mirror3 shadow
>     watcher. Roster `chain_audit.json` `clean` 16→24 (backup
>     `chain_audit.json.pre-cohort2-20260715`; cohort split kept in its
>     `cohort1_original`/`cohort2` keys). Restarted via
>     `mirror3_shadow_deploy.sh` (→ /opt/mirror3 d6276f7; mirror_v3 BYTE-
>     IDENTICAL to the prior 25b54d4, verified — roster-only in effect).
>     Watcher healthy: roster=24, first canary 1765 fills, 0 quote/canary
>     alarms. **COHORT-2 START EPOCH = 1784143245 (2026-07-15 19:20:45 UTC).**
>     The 8: `0x0e5bd767…`, `0x4ad6cade…`, `0x7744bfd7…`, `0xa2f1fecf…`,
>     `0xbaa2bcb5…`, `0xc660ae71…`, `0xd1acd392…`, `0xe25b9180…`.
>     **SEPARATE READOUT (never pool w/ cohort-1):** `analyze_shadow.py
>     --trust-quotes-after 1784143245` FILTERED to the 8 cohort-2 addresses.
>     **Cohort-2 pool (13 = the nine + grey-4) accounting, corrected
>     2026-07-15 PM (session-close review finding #16):** 8 ADMITTED (6 of the
>     nine: 0x0e5bd7/0x7744bf/0xa2f1fe/0xbaa2bc/0xd1acd3/0xe25b91 + 2 grey:
>     0x4ad6ca/0xc660ae) · 3 INSUFFICIENT (0x481858, 0x92672c of the nine;
>     0xea8ee3 grey — skill under the P bar on FULL data; deepen = wait for
>     more of their markets to resolve, then re-dive) · 1 REJECT-uncopyable
>     (0xf705fa, 461/day — honest, skilled, un-tailable) · 1 PENDING
>     (0xfbf3d5 grey, run-2 trader 1). Add later ADMITs (run-2 remainder +
>     wave-2) the SAME way — batch, one restart, extend the cohort2 ledger key
>     (shadow_readout REFUSES a readout if clean != cohort1+cohort2, so an
>     admission without the ledger update now fails loud).
>   - **ROSTER DELTA 2026-07-17 00:49:56Z (operator-agreed "fix not bend"):**
>     `0xf705fa…` added as a **PROBE** (observation-only, ledger key `probe`,
>     own epoch/readout line, never pooled; roster clean 24→25, watcher
>     restarted 00:50:21Z, backup `chain_audit.json.pre-probe-20260716`).
>     Why: the 461-fills/day REJECT measured the wrong unit — he is a STACKER
>     (7.6 decisions/day, 16% net-flat, 57% hold; round-trip component wins
>     only 38%) with +0.0368 P=1.000 on 1,835 mkts and 100% chain backing.
>   - **COPYABILITY PARAMS REWORKED (`27ee79b`, pre-registered BEFORE any
>     re-run):** receipt-free REJECT band now >1,000 fills/day; the 200-1,000
>     band is judged post-receipts on `--max-decisions-per-day 25` (chain
>     first-buys/day) + `--max-flat-share 0.60` (flow_shape net-flat share,
>     >=20 positions) — the DIRECT market-maker test. `--hft-max-rate` is
>     reporting-only now. Adversarial review: no confirmed defects; overlap
>     rejection + strict probe epoch added.
>   - **QUEUED [next session or run-3 completion]: BAND RE-RUN** — re-run all
>     rate-rejects with true_rate in (200,1000] under `27ee79b`+ (they were
>     rejected under the old 200 cap): currently 938, 749, 461(=probe, gets a
>     formal verdict), 395, 371, 288(0xfbfd14dd, had a ts issue) + any run-3
>     additions. Whoever passes -> PROPOSED as probes (operator word each).
>
> *(The 2026-07-14 ~02:45 block below is prior state from earlier the same day;
> its deep-dive-gate TO-DO is DONE per the above.)*

> **2026-07-14 UPDATE (local steward session; VPS-direct SSH, operator-
> approved per-command).** Five instrument bugs found & root-fixed in one
> day; ZERO trader lies ever confirmed (~800 receipt-level checks). State:
>
> 1. **QUOTE-SWAP (deployed watcher read /price sides REVERSED).** side=BUY
>    returns the best BID, side=SELL the best ASK — the watcher had it
>    backwards, so every pre-fix record's bid/ask were swapped and
>    shadow_fill quoted the BID (median +1.5c flattery vs the +0.02 floor;
>    counterfactual: 5/31 "OK" were really PRICE_RAN_AWAY; spread gate
>    could never fire). Fixed `2686e5c` + crossed-book runtime alarm
>    `5ce37ba` + live verify method `scripts/verify_clob_price_sides.py`
>    `875e389` (ran 5/5 AGREES) + readout repair `25b54d4`
>    (analyze_shadow re-derives every ladder-armed record; ladderless
>    pre-fix records EXCLUDED unless `--trust-quotes-after 1783985376` —
>    THE FIX-DEPLOY EPOCH, memorize it). Deployed 2026-07-13 23:29:27 UTC
>    (`/opt/mirror3` = `25b54d4`+); first post-fix record verified
>    (ask>=bid, fill=ask, ladder MATCH). Records are trustworthy from the
>    epoch; pre-fix records are ladder-repairable (all 83 had ladders).
> 2. **DUAL-ERA RE-ADJUDICATION — ALL 29 AUDITED TRADERS CLEAR.** The
>    audit toolchain searched V1 exchanges only (predates the 07-12 V2
>    discovery) → every post-migration fill was a structural not_found.
>    Fixed in `readjudicate_discrepant.py` (`fa21111`: V1 OrderFilled +
>    V2 fill topic, V2 candidates receipt-confirmed per (tx,token)).
>    Results: original 12 DISCREPANT → 9 VINDICATED (0 mismatch); grey-4
>    re-run dual-era n=60 ±3600s → 4/4 VINDICATED, 239/240 verified,
>    **0 not_found**; CLEAN-16 symmetric check → 16/16, 320/320 verified.
>    Artifacts: `mb_copyable_data/readjudicate{,_grey2,_clean2}.{json,log}`.
> 3. **OPERATOR RULES (2026-07-14, binding):** (a) the not-found quota is
>    REMOVED — not_found is an evidence gap, NEVER an accusation; the
>    response is a deeper search (window, samples, second RPC, dual-era),
>    never a threshold change; a lie exists only when the chain SAYS so
>    (size-matched tx at a different price) or when silence survives an
>    EXHAUSTIVE search of a complete record. (b) **CHAIN DEEP-DIVE GATE:
>    nobody joins any roster until they pass a full chain-native deep
>    dive** (see §5 TO-DO). API data is demoted to candidate-finding only.
> 4. **CRYPTO: UNRESOLVED, out of scope BY DEFAULT (never "killed").**
>    Kill-test ran (db.init bug fixed `23200e9`): INCONCLUSIVE by
>    construction — 0/2,720 crypto signals had ANY orderbook_snapshots
>    coverage at any lag. Retrospective crypto measurement is CLOSED;
>    only the forward shadow (correct books post-fix) can answer it.
> 5. **FUNNEL TRIPLE-CHECK:** hire→audit census perfect (29=29, 0 fetch
>    drops). BUT the HFT/bot filter judged on a 1-2.5 day burst page —
>    **34 borderline dismissals (203-469/day page rate) incl
>    `0xa6a856a8c8a7…` (run-1 named strong candidate, +2.5pts/593 mkts)**
>    never got the lifetime-rate test. They join the deep-dive batch.
>    Also: 38 traders rostered in the ALL universe never got a primary
>    shot (truncation scope); 496-leaderboard universe misses small
>    traders (documented scope limits).
> 6. **Shadow probes S1-S5 ran** (pre-registered 2026-07-13, descriptive):
>    83 records → **9 distinct (trader,token) firsts** (flow is heavily
>    concentrated — power = distinct positions, not detections). Capacity
>    CLEAR at paper size ($300 slip med +0.19c p90 +0.96c; >=$5k at <=1c
>    median). First-buy spread med 1c. Copy tax med +1c p90 +2c (n=7).
>    S1/S2 0/9 resolved — rerun when markets close. Artifacts:
>    `mb_copyable_data/shadow_probes_20260713.{py,out}`.
> 7. **Cohort-2 pool = 13** (9 + grey-4), NONE admitted — all gated on the
>    deep dive. Boundary-pass caveats retired (were the V1 blind spot).
> 8. Local-session logistics: work from the dedicated worktree
>    `C:/lockes-picks/mb-steward` (operator-directed exception to the
>    parent-dir fence — the Claude app yanks the main checkout between
>    branches); `git pull --ff-only` before EVERY commit (two writers).
>
> *(The 2026-07-12 block below is prior state; its "NEEDS REDEPLOY" and
> deploy-version questions are RESOLVED by the above.)*

> **2026-07-12 UPDATE (shadow-steward session, `claude/repo-setup-docs-fq9bhn`):**
> the `irq7r5` session ("king") is FROZEN; this session stewards the shadow
> (operator-authorized, all four items). Everything king pushed survives
> (head `1c08793`, incl. A+D sizing `4d6c3da`); its UNPUSHED ladder-capture
> patch is presumed lost with the container and was REBUILT (below). New
> since 2026-07-11, all on this branch, none deployed yet:
> 1. **Ladder capture in the shadow watcher** — `mirror_v3/copy_watcher.py`
>    now records `book_asks`/`book_bids` (top 20 CLOB `/book` levels, shaped
>    for `fill_models.precise_fill`) per record; gates still quote `/price`
>    unchanged, `/book` failure = null ladders, never a verdict change.
>    17 unit tests. NEEDS A REDEPLOY to take effect.
> 2. **DEPLOY-VERSION QUESTION (UNVERIFIED):** §0 says the service runs
>    commit `eac8a92`, but A+D sizing landed AFTER it (`4d6c3da`) — if no
>    redeploy happened, live records lack `conviction_r`/`size_multiplier`.
>    Operator check + the ladder redeploy resolve this together (see §5).
> 3. **Tx-exact re-adjudication of the 9 DISCREPANT** —
>    `scripts/readjudicate_discrepant.py` (pre-registered VINDICATED /
>    STILL_DISCREPANT rule in its docstring; vindicated traders only ever
>    join as a SECOND cohort, operator-gated). Awaiting operator VPS run.
> 4. **Crypto kill-test runner** — `scripts/crypto_kill_test.py`
>    (pre-registered KILLED/SURVIVES/INCONCLUSIVE at lag 10s vs +0.02
>    floor). Awaiting operator VPS run, after (3).
> A daily 13:00 UTC steward check-in Routine now fires into this session
> (king's wake-ups are dead with it).
>
> **2026-07-12 LATE UPDATE — THE SHADOW WAS BLIND FROM BIRTH; FIXED, NEEDS
> REDEPLOY.** The watcher's first ~33h produced ZERO records while the
> data-api showed **179 roster BUYs in 40h** (probe `roster_activity_check.py`,
> 16 traders, 0 fetch errors). Root cause chain, each step receipt-verified:
> (1) Polymarket moved trading to the **V2 exchanges** (Exchange V2
> `0xE111180000d2663C0091e4f400237545B87B996B`, NegRisk V2
> `0xe2222d279d744050d28e00520010520000310F59`) — WI-24 verified this
> 2026-06-11, BEFORE the watcher ever deployed; (2) V1 `OrderFilled` never
> fires for current flow (all probes zero across 4 RPCs — the RPC was
> innocent); (3) V2 fills emit an UNNAMED event, topic0
> `0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee`,
> layout reverse-engineered from known trades and validated to 4 decimals
> (`scripts/decode_v2_fill.py`): topics[2]=order owner (server-side
> filterable), data=[?, token_id, usdc*1e6, tokens*1e6, 0,0,0]; (4)
> **BUY/SELL is NOT in the V2 event** — direction read from the tx
> receipt's ERC-1155/pUSD transfers on roster hits (`side_from_receipt_logs`).
> Watcher reworked accordingly (`c77e2dd`), plus a blind-RPC canary
> (10-min unfiltered fill count, alarms after 2 zero cycles, first result
> always logged) so silent blindness is structurally impossible now.
> Diagnostic toolchain kept: `diagnose_watcher_detection.py`,
> `rpc_logs_probe.py`, `trace_real_fill.py`, `decode_v2_fill.py`.
> **The shadow readout clock starts at the post-fix redeploy, not at
> 2026-07-11 12:46.** The walk-forward/audit are NOT invalidated (their
> fills genuinely lived on V1-era history).

**The operator runs all VPS commands** via single-line SSH one-liners from
Windows PowerShell (he cannot paste after connecting; never give multi-line).
Template: `ssh -t -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 "..."`.
PowerShell EATS `$` and `"` inside the quoted command — never put either in a
one-liner (the 2026-07-10 probe false-negative was PS mangling `\"` JSON).

**RESULTS CHAIN (2026-07-10/11 overnight, all artifacts snapshotted to
`/opt/pa2-shared/mb_copyable_data/`):**
1. **Walk-forward PASS** (full-coverage run, header-gated: labeled
   first-buys=100,180, gamma merged=69,977 market keys, coverage 97%):
   PRIMARY edge **+0.0237, P(edge>0)=1.000, upper95 +0.0284**, 24,919 bets /
   19,281 mkts / 28 traders; robustness +0.0224/+0.0203; econ floor +0.02
   cleared, thinly. `/tmp/walkforward3.{log,json}` + snapshot.
2. **Chain audit (fixed × 3, see §7 landmines): 16/29 CLEAN** — 580 samples:
   505 verified / 20 mismatch / 55 not-found / 0 rpc-err.
   DISCREPANT (9) EXCLUDED (chain wins; some may be ±30min window-blend
   artifacts — a tx-hash-exact matcher would settle it, later). THIN (1)
   excluded pending wider window. `/tmp/chain_audit.{log,json}` + snapshot.
3. **Fill gate: NO VERDICT — GENUINE coverage gap, probe-CONFIRMED
   2026-07-11:** only 164/34,507 roster tokens (0.5%) exist in
   orderbook_snapshots at all (token shapes match — no key bug, no window
   bug). Retrospective fill measurement is CLOSED for this roster; the
   forward shadow is the only instrument. NOT fill-killed.
4. **Operator decision (2026-07-11): build the forward shadow instrument.**
   BUILT: `mirror_v3/copy_watcher.py` — on-chain OrderFilled polling of the
   CLEAN roster (~2-4s detection vs ~10s REST), pre-trade gates
   (NO_BOOK / SPREAD_TOO_WIDE / PRICE_RAN_AWAY / OK), shadow fill = real CLOB
   ask, JSONL sink + detect-lag metrics. NO orders, NO DB writes. Wired into
   `mirror_v3/run.py` behind `MIRROR3_COPY_WATCHER=true` (default OFF,
   fail-loud). Env template: `deploy/env.mirror3.example`.

**SHADOW IS DEPLOYED AND RUNNING (2026-07-11 12:46 UTC, commit `eac8a92`):**
`polymarket-mirror3.service` on the VPS — env-guarded paper silo, watcher
polling both exchanges at 2s for the 16 CLEAN traders, retry-don't-skip
cursor (Tenderly head-race absorbed; only `SKIPPING (dropped window)` log
lines mean lost samples), sink `/opt/pa2-shared/mirror3_shadow.jsonl`
(world-readable), code at `/opt/mirror3`, redeploy = rerun
`deploy/mirror3_shadow_deploy.sh` (idempotent; never touches an existing
`.env.mirror3`).

**NEXT ACTIONS:**
1. [operator, ~daily glance] `systemctl is-active polymarket-mirror3;
   wc -l /opt/pa2-shared/mirror3_shadow.jsonl` — count should grow as the
   roster trades (humans: hours of silence normal).
2. [analysis, ~2-4 weeks of records] `scripts/analyze_shadow.py --log
   /opt/pa2-shared/mirror3_shadow.jsonl --gamma-cache
   /tmp/copyable_cache/gamma_resolutions.json` — the PRE-REGISTERED readout:
   OK-rate ≥50% on first-buys; pooled shadow edge net of fee ≥ +0.02 with
   P(>0) ≥0.95 on ≥30 resolved mkts (it refuses a verdict when
   underpowered). SURVIVES → operator decision on paper trading with real
   order flow. Never live from a backtest.
3. [later, optional] WSS subscription upgrade (Alchemy free tier) to cut
   detection to ~2-3s; tx-hash-exact audit matcher to re-adjudicate the 9
   DISCREPANT traders.

---

## 1a. CURRENT STATE (2026-07-10 — read this first, §1b is background)

**The investigation PIVOTED (operator-directed, 2026-07-09/10): stop testing on
rejected-signals leftovers; pull COMPLETE per-bet trader histories from the
public Polymarket APIs and find COPYABLE traders directly.** Chain of results:

1. **Run 1 of `scripts/find_copyable_traders.py`** (496 leaderboard addrs, full
   histories, qualify-on-P1/judge-on-P2, chain-verified 20/20): first broadly
   positive signal of the whole investigation — P1-qualified traders held
   **+2.3pts P2 edge, P=0.962, across 1,065 markets** (descriptive; PNL-collider
   -tainted, politics cell post-hoc — NOT promotable). Primary was UNDERPOWERED:
   329/496 histories TRUNCATED at the 20k cap and the VOL list is by definition
   the deepest whales → primary cell starved to 1 trader. Named strong-both-half
   candidates emerged (`0x6bab41a0dc` 109 P2 mkts +0.089 P=0.99; `0xd1acd3925d`;
   `0xa6a856a8c8` 593 P2 mkts +0.025 P=0.89).
2. **Grading redesigned with the operator into the WALK-FORWARD rule**
   (`scripts/walkforward_copy_traders.py`) — the deployable strategy tested as
   it would run: HIRE on lifetime record at monthly reviews (>=25 mkts, >=60d
   span, P(edge>0)>=0.90), FIRE only on statistically convincing RECENT decay
   (trailing 90d, >=10 mkts, P(edge<0)>=0.90 — past glory can't shield bleed,
   a GOAT's cold month can't convict), GRADE only post-hire bets. Improvers
   caught, goats not churned, has-beens cut. Locked review grid (anchor
   2025-01-01) + ±15d robustness grids; knowledge gated by resolved_at.
   Primary = VOL-sourced non-truncated universe. Self-test proves all four
   personas (goat/bleeder/improver/streaker).
3. **Pre-run tinker (operator: "tinker before spending 7 hours")**: (a) bot/
   market-maker exclusion `--hft-max-rate 200` bets/day — bots were ~all the
   truncation and most of the download cost, and are mechanically uncopyable;
   (b) **gamma resolution backfill** (`scripts/backfill_resolutions_gamma.py`)
   — DB label coverage was 24%; backfill lifts to ~80-95% via a local JSON the
   graders merge under the DB map (DB wins); (c) truncated caches re-deepen
   when --max-bets rises (`--deepen vol`).
4. **A 3-stage detached pipeline RAN on the VPS** (gamma backfill → deepen
   humans-only → walk-forward). The deepen took ~13h (01:15→~14:30 UTC,
   0 partial-failed); its walk-forward stage never fired (stale-log ownership
   collision) and was re-run standalone at 15:02.
5. **2026-07-10 second session (branch `irq7r5`) — run-2 verdict + two root
   causes found and fixed.** The 15:02 walk-forward printed **PASS on the
   pre-registered primary** (VOL-sourced non-truncated: edge +0.0145,
   P(edge>0)=0.985, 5,338 bets / 3,947 mkts / 19 traders, robustness splits
   +0.0137/+0.0130, `/tmp/walkforward2.log`) — **PROVISIONAL**: it graded on
   DB-only labels (~24% coverage; header `gamma-backfilled labels merged=0`),
   and the point estimate sits under the +0.02 econ floor (upper95 +0.0250
   above it). DECLARED BEFORE THE RERUN: the full-coverage rerun REPLACES
   this verdict whichever way it goes. Root causes of merged=0, both fixed:
   (a) `merge_gamma_cache` refused labels for DB-known-but-unresolved rows
   (`aa6bbc1`); (b) the gamma backfill NEVER labeled anything — gamma's
   `/markets?condition_ids=` filter is silently ignored (probe-verified live:
   CLOB echoes the exact market, gamma returns `[]`); ported production
   `resolution_backfill.py`'s per-key CLOB endpoint (`b609c14`).
6. **THE FULL-COVERAGE PIPELINE IS RUNNING** (launched ~16:59 UTC from
   `/tmp/mbpc2` = `b609c14`): CLOB label backfill (`/tmp/gamma2.log`; at
   20:15 UTC: chunk 5,760/8,189, 139,357 labeled, errors=2; ETA backfill
   ~21:40 UTC) `&&` walk-forward → `/tmp/walkforward3.{log,json}`. See §0
   IMMEDIATE RESUME for the exact next command + accept criteria + PASS
   sequence. Durable 3.4GB snapshot already taken (`/opt/pa2-shared/
   mb_copyable_data`); re-copy after finish for the authoritative version.
   Also NEW this session: mandatory per-fill chain audit
   (`scripts/audit_roster_chain.py`, `28a447d`) + atomic JSON writes across
   all 4 scripts (`0bdd4de`) + this handoff.

Fill-quality gate (`scripts/backtest_copyable_fills.py`, audited coarse model,
real ask crossed) is built and waiting for whatever roster passes. Per-fill
OrderFilled chain audit is MANDATORY before any money decision on a named
trader. Everything committed/pushed on this branch; 29+ script unit tests green.

## 1b. Background (2026-07-09 and earlier)

MirrorBot's old whale-copy strategy is confirmed dead (no measured edge). The old bot is **paused to paper** (real money off, 2026-07-05) but still collecting signal data. A **clean-silo rebuild** (`mirror_v3/`) is scaffolded, tested, and ready to deploy — safety spine only, strategy slot deliberately empty behind an acceptance gate. **The v3 whale trader-ranking engine (`bots/mirror_scoring/`) reportedly FAILED its Stage-1 gate** (prior-session handoff citing `172d72a`: 2 cutoffs FAIL, placebos 0/20 and 1/20). A 61-agent adversarial review (2026-07-09, this session) established two things about that evidence: (a) **commit `172d72a` is NOT in this clone — the FAIL is currently hearsay** until the commit/branch is recovered and reviewed; (b) **the in-repo validation harness is confirmed CIRCULAR** (admission is selected on post-cutoff outcomes, then "validated" on the same post-cutoff signals — a false-PASS machine), which mechanically explains the earlier miscalibrated "PASS" and means **a PASS from `bots/mirror_scoring/validation.py` must never clear anything**. The circularity biases toward PASS, so the reported FAIL — if the recovered code checks out — is if anything *stronger*. **The lead instrument now is the TAIL BACKTEST** — `scripts/backtest_tail_leaderboard.py` (copy-everyone at the operator-measured ~10s lag, per category, market-clustered bootstrap, pre-registered primary cell, pre-spread screen semantics), hardened against all 26 confirmed review findings; `scripts/check_trader_persistence.py` is the SECONDARY corroboration (its shuffle null is anti-conservative under shared-market overlap — labeled as such). Both await an operator VPS run. The other strategy direction is a **sharp-line reference** (compare whale entries to an efficient outside price); its vendor-independent core is built and tested, waiting on an OddsPapi paid tier for sports data. Everything is on GitHub; nothing is deployed except the pause.

## 2. Current system state (verified)

- **Old MB:** paper mode. `SIMULATION_MODE=true, CANARY_STAGE=0, CANARY_AUTO_ADVANCE=false` appended to `/opt/pa2-shared/.env.mirror` and service restarted. Still writes `mirror_rejected_signals` (the rebuild's data).
- **VPS is LIVE-config by default otherwise** — before the pause it was `SIMULATION_MODE=false, CANARY_STAGE=4`, auto-advance armed by code default. The env-drift-to-live risk is real; `mirror_v3` env_guard exists specifically to end it.
- **Data tier (measured 2026-07-05, `docs/m0_db_results_2026-07-02.md` + this session):** ~5.06M labeled whale signals — **crypto 73%, sports 17%, esports 5%**, rest <5%. orderbook_snapshots 37.7M (aggregated buckets, NOT full L2). mirror_rejected_signals 17.5M. gate-labeled 286k. precise-fill ladder rows (shadow_fills) 12,713.
- **Tests:** 423+ green on the merged tree; each new module ships its own suite.

## 3. Key decisions (all in MB_REBUILD_PLAN.md, do not re-litigate)

1. **Acceptance gate:** no strategy ships without passing the fill-replay backtest (precise model) + edge check. "Algo proposes, backtest disposes."
2. **Clean silo** (`mirror_v3/`), new identity `MirrorBotV3`, own systemd unit, allowlist env, same VPS.
3. **Paper-first, real sizing** (code defaults, not the old flat-$1).
4. **Strategy = sharp-line reference, sports-first.** Crypto (73% of volume) is a **latency** edge → un-tailable on our 60s delay → expected to FAIL the gate (use the harness's latency model as the kill test). Sports/esports are **knowledge** edges → tailable. Pinnacle for sports, OddsPapi for esports.
5. **clob_adapter fill-price fix** landed (S250, `d3d2369`) — defensive, paper-unchanged.

## 4. What's built (all pushed)

| Area | Location | State |
|---|---|---|
| Clean silo | `mirror_v3/{env_guard,guards,state_restore,run}.py` | scaffold + 22 tests; boots, restores fail-closed, strategy idle |
| Silo deploy | `deploy/polymarket-mirror3.service`, `deploy/env.mirror3.example` | ready; needs real `DATABASE_URL` on first install |
| Acceptance gate | `bots/mirror_backtest/{fill_models,replay,gate,data_access}.py` | dual-model harness + 19 tests; DB-execution gated on M0-DB |
| Sharp-line core | `bots/mirror_backtest/sharp_reference.py` | no-vig, point-in-time, gate rule + 19 tests; OddsPapi seam env-key-only |
| Scoring engine | `bots/mirror_scoring/` (from `mb-formula-review`) | 45 tests; runner unblocked (`8ea683d`); validate run pending |
| M0-DB verify | `scripts/verify_salvage_data.py` | read-only; cascade bug fixed |
| Tail backtest (PRIMARY) | `scripts/backtest_tail_leaderboard.py` | read-only; hardened vs 2026-07-09 review (strictly-after-print fills, per-slice coverage gate, fee-scaled pts + ret/$ units, paired tax, pre-registered primary cell `cat:sports@10s` + 30s lag-agreement, LIMIT sentinel, `--sample`/progress, stage/side mix printed, PASS* = pre-spread screen only); self-test + 14 unit tests green; **awaiting operator VPS run** |
| Copyable-trader search | `scripts/find_copyable_traders.py` | full-history P1/P2 grader; leaderboard universe, time-windowed pagination, bot exclusion, gamma-label merge, deepen-aware cache; run-1 results in §1a |
| Walk-forward grader (LEAD) | `scripts/walkforward_copy_traders.py` | hire-on-lifetime / fire-on-recent-decay / grade-post-hire; locked+shifted review grids; resolved_at knowledge gating; primary=VOL non-truncated |
| Label backfill (CLOB) | `scripts/backfill_resolutions_gamma.py` | REWORKED `b609c14`: per-key CLOB `/markets/{cid}` (gamma batch filter is a no-op — §7); prices-first resolution, winner-flag fallback; resumable, atomic checkpoints; live run ~96% label rate, 0 errors |
| Chain audit (mandatory pre-money) | `scripts/audit_roster_chain.py` | NEW `28a447d`: per-fill OrderFilled audit of the walk-forward roster, BOTH exchanges (main + NegRisk), CLEAN/DISCREPANT/THIN/ERROR + AUDIT-INCONCLUSIVE tripwire; self-test + 11 unit tests |
| Fill-quality gate | `scripts/backtest_copyable_fills.py` | audited coarse model at real ask for a NAMED roster; SURVIVES/FILL-KILLED/NO-BOOK; runs after a walk-forward PASS |
| Persistence check (secondary) | `scripts/check_trader_persistence.py` | read-only; reworked verdicts (SIGNIFICANT-BUT-SMALL; NULL can't discard underpowered-significant cutoffs), UNION-ALL planner-safe SQL, estimand-faithful first-entry selection, `--since/--until`, LIMIT sentinel; anti-conservative-null caveat printed; self-test + 11 unit tests green; **awaiting operator VPS run** |
| Operator runbooks | `docs/VPS_RUNBOOK_2026-07-02.md`, `deploy/mb_vps_oneshot.sh` | one-paste checks; mktemp-safe |
| Shadow ladder capture | `mirror_v3/copy_watcher.py` (`trim_book`/`fetch_book`, `book_asks`/`book_bids` fields) | additive, gates untouched; 17 unit tests; **needs redeploy to take effect** |
| Tx-exact re-adjudication | `scripts/readjudicate_discrepant.py` | per-tx/per-event size+price matcher (kills the ±window blend artifact); pre-registered VINDICATED rule; self-test + 8 unit tests; **awaiting operator VPS run** |
| Crypto kill-test runner | `scripts/crypto_kill_test.py` | RAN 2026-07-13 (after db.init fix `23200e9`): INCONCLUSIVE by construction (0/2,720 orderbook coverage) → crypto UNRESOLVED, out of scope by default |
| Tx-exact re-adjudication v2 | `scripts/readjudicate_discrepant.py` | DUAL-ERA (`fa21111`): V1+V2 events, V2 receipt-confirmed; 10 unit tests; all 29 traders cleared (see §0.2) |
| /price semantics pin | `scripts/verify_clob_price_sides.py` | live PASS/FAIL vs /book (`875e389`); ran 5/5 AGREES pre-deploy; run before any watcher deploy or on QUOTE SANITY alarm |
| Readout repair | `scripts/analyze_shadow.py` | ladder re-derivation default-ON (`25b54d4`); `--trust-quotes-after 1783985376` for post-fix ladderless records |
| Quote sanity alarm | `mirror_v3/copy_watcher.py` `quote_sanity_msg` | crossed-book LOUD alarm (`5ce37ba`); 27 watcher tests total |
| Shadow probe battery | `mb_copyable_data/shadow_probes_20260713.{py,out}` | S1-S5 pre-registered descriptive; capacity/spread/tax measured; S1/S2 await resolutions |
| Stress suite | `tests/unit/test_mirror3_stress.py` | 9 tests/10 invariants (cloud session `d7fa2bf`); full mirror_v3 surface 93+ green |
| **Chain deep-dive gate (roster admission)** | `scripts/chain_deep_dive.py` + `tests/unit/test_chain_deep_dive.py` | NEW 2026-07-14 (`0231f2c`): 4-tier chain-native gate (lifetime dual-era reconstruction → API↔chain reconcile both ways → chain skill re-grade → forensics/fair-HFT); adversarially reviewed (16 findings fixed) + smoke-validated; `--self-test` 16-case verdict table + 23 pytest green (local+VPS venv); read-only, reuses siblings as-is. **47-batch RUNNING** (see §0). |
| **Shadow readout (fresh-label, per-cohort)** | `scripts/shadow_readout.py` + `scripts/analyze_shadow.py --traders` | NEW 2026-07-15: rebuilds token→outcome FRESH from `markets` each run (default gamma cache is stale — §7 landmine), splits cohort-1 / cohort-2 (never pooled), writes an ALERT on power-bar / negative-firming. Both `--self-test` green. Runs daily on the VPS (durable clone `/opt/pa2-shared/mb_readout`); log `shadow_readout_log.txt`, alert `shadow_readout_ALERT.txt`. |

## 5. Open threads / what's next

### TO-DO (2026-07-14 plan — next session starts HERE)

> **STATUS 2026-07-14 PM:** item 1 **DONE** (`chain_deep_dive.py` built,
> reviewed, smoke-validated, `0231f2c`); item 2 **RUNNING** (47-batch launched
> 19:31 UTC — see §0 for collect/monitor + admission-gate instructions); item 3
> (operator admission word) pending the batch results.

1. **[build, DONE `0231f2c`] `scripts/chain_deep_dive.py` — the roster-admission
   gate (operator-mandated: no trader joins any roster without it).**
   - Tier 1: lifetime fill reconstruction from chain, BOTH eras (V1
     OrderFilled + V2 fill topic, server-side owner-topic filter; reuse
     `mirror_v3/copy_watcher` decoders + `readjudicate_discrepant.py`
     dual-era pattern). ~40-60 min/trader at 6 rps on tenderly.
   - Tier 2: API↔chain reconciliation BOTH directions (API claim absent
     on-chain after exhaustive sweep = fabricated; chain fill absent from
     API = hidden activity).
   - Tier 3: skill re-grade on the chain-reconstructed record (same
     walk-forward hire bar; resolutions from the CLOB label cache).
   - Tier 4 forensics: counterparty concentration (wash), copier-latency
     (are THEY copying someone — double-lag alpha), funding lineage
     (sybils), maker/taker + rate profile (true lifetime bets/day — the
     fair HFT test the burst-page filter never ran).
   - Admission = zero contradictions + chain-graded skill clears the bar
     + no forensic flag. Evidence gaps → deeper search, never quotas.
2. **[run] Deep-dive batch (~47):** 13 cohort-2 candidates (9 VINDICATED
   + grey-4) **+ 34 HFT-borderline** (incl `0xa6a856a8c8a7…`). Optional
   wave 2: the 38 ALL-universe scope-outs. Overnight batch, read-only.
3. **[operator] Cohort-2 admission word** AFTER deep-dive results — own
   start date, separate readout, never pooled with cohort-1.
4. **[analysis, when markets resolve] Rerun probes S1/S2**
   (`shadow_probes_20260713.py`) — gate-optionality (OK vs RAN_AWAY
   win-rate) + conviction-signal cells; both pre-registered 2026-07-13.
5. **[readout, ~2-4wk from 2026-07-13 23:29 UTC] `analyze_shadow.py
   --trust-quotes-after 1783985376`** — the pre-registered verdict.
   Pre-fix records auto-repair from ladders; criteria unchanged.
6. **[flag-flip, proposed] Record roster SELLs** in the watcher (record-
   only, no strategy) — starts the exit-follow dataset clock.
7. **[build, before any real order flow] Per-event exposure caps in
   sizing** (neg-risk sibling correlation — the one guard gap; belongs in
   sizing, NEVER market gating per CLAUDE.md Bug-14 ban).
8. **[monitor, ~daily] `systemctl is-active polymarket-mirror3; wc -l
   /opt/pa2-shared/mirror3_shadow.jsonl`** + journal grep for
   `QUOTE SANITY` (must be absent) and `CANARY ALARM`.

- **[superseded — resolved threads]** The walkforward3 decision tree ran
  to PASS; audit + re-adjudication + symmetric check complete (all 29
  clear); deploy-version question resolved (`25b54d4` live); crypto
  kill-test ran → UNRESOLVED (out of scope by default, never "killed").

- **[NOW — decision tree for /tmp/walkforward3.log]** (the OLD
  `/tmp/walkforward.log` is a stale pre-pipeline artifact — ignore it; use
  `tail -n 4 /tmp/gamma2.log` for backfill progress, table lands in
  `/tmp/walkforward3.log`). First check the header: `gamma-backfilled labels
  merged=` must be ≫0 and `labeled first-buys` ≫ 29,635, else it's not the
  full-coverage run. On **PASS**: (1) re-copy the durable snapshot (below),
  (2) re-clone `/tmp/mbpc2` (the running clone predates the audit script),
  (3) `scripts/audit_roster_chain.py --from-json /tmp/walkforward3.json`
  (mandatory chain audit; ~15-20 min), (4) fill-quality gate
  `backtest_copyable_fills.py --traders <CLEAN roster> --lags 5,10,30`
  (verdict at the measured 10s; 5s is a sensitivity lead only), (5) operator
  decision on a v3 forward PAPER deploy. Everything else per the original
  tree below:
  - **PASS** → the rostered addresses are the deliverable. Next: per-fill
    OrderFilled chain audit on them, then `scripts/backtest_copyable_fills.py
    --from-json` fill-quality gate, then operator decision on a v3 forward
    paper deploy. NOT a live deploy.
  - **FAIL-TERMINAL** → copy thesis closed retrospectively on the best data
    that will ever exist; only a forward shadow test could revive it. Say so.
  - **UNDERPOWERED / INCONCLUSIVE / NO-DATA** → check in order: label coverage
    actually achieved (gamma report), slug-key residue (add slug lookup pass if
    big), primary-universe size after bot exclusion (widen --universe if thin),
    THEN rerun. Do not loosen thresholds — widen data.
  - Interrupted pipeline? Every stage is resumable — rerun the same chained
    command; caches make completed work free.
- **[rules that BIND the next session]** No rework-then-retest until an
  instrument passes; primary verdicts only from the pre-registered cells
  (walk-forward primary = VOL-sourced non-truncated pooled edge); descriptive
  cells (incl. politics' +5pts from run 1) are leads, not results; numbers get
  cited with coverage/sample qualifiers.
- **[superseded]** The tail backtest + persistence check below remain valid
  instruments but are SECONDARY to the walk-forward since the 2026-07-10 pivot
  to full-history data.
- **[operator, GATING — PRIMARY] Run the tail backtest** — the direct "can we tail them
  reasonably?" test (operator's framing, 2026-07-09), hardened per the 61-agent review:
  ```
  # bounded first run (quiet window; the resolution filter is not index-backed):
  cd /opt/polymarket-ai-v2 && sudo -u polymarket env PYTHONPATH=/opt/polymarket-ai-v2 \
    venv/bin/python scripts/backtest_tail_leaderboard.py --by-category \
    --since 2026-06-01 --sample 20000 | tee /tmp/tail_backtest.log
  # full run after the bounded one behaves:
  ... scripts/backtest_tail_leaderboard.py --by-category | tee /tmp/tail_backtest.log
  ```
  Discipline is built in: PRIMARY CELL is pre-registered (`cat:sports` @ 10s, 30s
  lag-agreement required); every other cell is descriptive (multiplicity). PASS* is a
  **pre-spread screen** — a FAIL is final for a slice; a PASS* only licenses the
  PRECISE fill-model gate (`bots/mirror_backtest/gate.py`, shadow_fills ladders) per
  MB_REBUILD_PLAN §2. Read per-slice `cov` and `nr%` before believing any row.
- **[operator, secondary] Run the persistence check** — corroboration only (its shuffle
  null is anti-conservative under shared-market overlap; the output says so):
  ```
  ... scripts/check_trader_persistence.py --by-category --since 2026-03-01 | tee /tmp/persistence.log
  ```
  **Hard rule stands:** do NOT rework-then-retest the ranking until an instrument passes
  (p-hacking); any real verdict needs multi-cutoff agreement, and NULL/MIXED semantics
  are now strict (an underpowered-but-significant cutoff blocks NULL).
- **[operator, evidence] Recover `172d72a`** (the calibrated-permutation FAIL run — likely on
  the `mb-formula-review` lane): push the branch/commit to origin so the Stage-1 FAIL becomes
  auditable instead of hearsay, and grab the 3rd cutoff (05-10) result from `/tmp/val_all.log`
  and record it here. Until then the FAIL is provisional (direction likely correct — see §7
  circularity note: the in-repo harness biases toward PASS, and it still failed).
- **[standing, DO NOT] Re-run `--stage validate` (`deploy/mb_vps_oneshot.sh` / 
  `scripts/mirror_scoring_run.py`) for any decision** — the in-repo kill criterion is
  confirmed circular (§7). A PASS from it means nothing; running it wastes a 300s scan.
- **[gated rework backlog — only if an instrument passes]** catalogued by the 2026-07-09
  review, NOT applied (hard rule above): (1) three-way split fit/select/validate in
  `mirror_scoring` (kills the circularity); (2) per-category stratified scoring + BH
  (MIN_EVENTS=12 pooled starves sparse sports/esports traders); (3) cluster by event, not
  condition_id (neg-risk siblings inflate confidence; schema has no event id — needs one);
  (4) two-group contrast via cluster regression, not the spread/2 recentering
  (anti-conservative); (5) `DELTA_SECONDS` from measured `feed_lag_p95_s` (~10s), not 60.
- **[operator] OddsPapi paid tier** — confirm sports coverage + that `ODDSPAPI_API_KEY` is set in the VPS env (presence only). Then the sharp-line engine wires to live data.
- **[build, blocked on above] Sports sharp-line pipeline:** live OddsPapi fetch, sports team-name → Polymarket condition_id matcher (esports matcher exists in EB, sports is net-new), offline backfill of `sharp_prob` onto signals, then run through the gate.
- **[operator, FIRST — one paste] Deploy-version check + ladder redeploy:**
  `ls /opt/mirror3/mirror_v3` — if `sizing.py` is absent the box predates
  A+D sizing AND the ladder capture; either way one rerun of
  `deploy/mirror3_shadow_deploy.sh` from this branch picks up both
  (idempotent; never touches `.env.mirror3`). Until then, live shadow
  records carry no `conviction_r` (UNVERIFIED which commit runs) and no
  ladders (VERIFIED — capture merged 2026-07-12, post-deploy).
- **[operator] Run the tx-exact re-adjudication** (`readjudicate_discrepant.py`,
  from a /tmp clone of this branch; roster = the audit json's 9 DISCREPANT).
  VINDICATED traders are PROPOSED for a SECOND shadow cohort with its own
  start date — operator decision, never automatic. Ceiling is 16→25; the
  matcher may equally confirm real discrepancies.
- **[build DONE 2026-07-12 → operator run] Crypto kill-test:**
  `scripts/crypto_kill_test.py`, pre-registered verdict at lag 10s vs the
  +0.02 econ floor; INCONCLUSIVE can never kill. Run after the
  re-adjudication (lower priority; it buys focus, not money).
- **[build, unblocked] v3 rejection logging + RTDS plumbing** so the silo collects its own signal stream (then old MB can be fully stopped, not just paused).
- **[decision] Merge/PR hygiene:** master is current; direct master pushes are operator-gated by the sandbox.

## 6. Cross-session coordination

- **EB (esports)** owns the OddsPapi vendor integration (esports). Odds-capability report is in this session's history; EB has a team-alias matcher MB can reuse. Registry publish (`EB_ODDS_CAPABILITY.json`) offered, not yet committed.
- **mb-formula-review** branch owns the *statistical* scoring lane; MB owns execution/guards/gate. Three statistical findings (condition-vs-event clustering, validation statistic, EB-shrink pool) handed over as recommendations, not applied.
- **MB has priority** on shared resources (CLAUDE.md). Old poisoned project lives at parent `C:/lockes-picks/` — OUT OF SCOPE.

- **[operator, after pipeline finishes] Re-run the durable snapshot copy** —
  ALL investigation data lives in `/tmp` (reboot-ephemeral). A parallel
  snapshot was taken mid-run 2026-07-10 (`/opt/pa2-shared/mb_copyable_data`)
  but may hold a torn gamma checkpoint (running code predates the atomic-
  write fix `0bdd4de`); the post-finish re-copy is the authoritative one:
  `sudo cp -a /tmp/copyable_cache /tmp/walkforward3.json /tmp/walkforward3.log /tmp/gamma2.log /opt/pa2-shared/mb_copyable_data/`

## 7. Landmines (do not trip)

### Added 2026-07-23 (readout label-source session)

- **A DATA FIX IS NOT DONE UNTIL THE REPORTING PATH READS IT.** The 07-22 CLOB
  supplement was correct and verified, and the daily readout kept printing
  flattered edges for a full day because it built labels from `markets` only
  and never opened the cache. When you fix a source, grep every consumer that
  produces a NUMBER and prove each one moved — a fixed source with an unmoved
  consumer looks exactly like a fix that worked.
- **Two supplements, two different key sets.** `gamma_resolutions.json` was
  built around the ADMIT deep-dive's evidence (the TRADERS' markets); it
  covered 21 of 405 SHADOW tokens. "The cache is supplemented" never implies
  it is supplemented for YOUR token set — measure the intersection.
- **`chain_deep_dive.py` preloads the gamma cache ONCE per process** (`:1259`),
  so a mid-run cache write does NOT contaminate a running dive — and equally,
  a running dive will NOT pick up a label you just added. Restart to adopt.
- **`shadow_readout.py` now REFUSES to run** (rc=2, loud line in the durable
  log, ALERT untouched) if the supplement is missing/unreadable/labels nothing.
  That is deliberate: a missing daily line is loud, a flattered one is not. If
  the readout goes quiet, look for the FATAL line in `shadow_readout_log.txt`
  before assuming cron died.
- **`markets` knows the condition_id of markets it has NOT resolved.** That is
  what makes a targeted CLOB pass cheap (203 of 300 unlabelled shadow tokens
  were reachable that way). Only the residue needs a token→market lookup.

### Added 2026-07-22 (label-integrity + infra session)

- **EMPTY-SET FALSE PASS — the highest-value lesson of the session. Tripped
  TWICE in different clothes.** (1) An A/B differential harness whose output
  files silently failed to write: `diff` compared two EMPTY streams and printed
  "IDENTICAL". (2) The ADMIT re-review's `mkdir` failed, the dive produced zero
  JSONs, and the before/after diff globbed an empty dir and printed
  **"FLIPPED: 0"** — indistinguishable from "all 20 ADMITs survived". ANY
  comparison/verification MUST assert its inputs are NON-EMPTY and fail loud
  otherwise. A zero-row result is never evidence of agreement.
- **`/opt/pa2-shared/mb_copyable_data` is ROOT-owned — this bit again.** Any
  polymarket-run job needing a NEW output dir must have it pre-created via
  `sudo mkdir + sudo chown polymarket:polymarket`. `chain_deep_dive` dies at
  `os.makedirs` with PermissionError and exits rc=0-looking within seconds.
- **Non-ASCII in a script uploaded over SSH breaks bash** (`syntax error:
  unexpected end of file`). Em-dashes / `§` / arrows mangle in transit. Sanitize
  uploaded scripts to pure ASCII.
- **`trust_after` is NOT a time filter.** `analyze_shadow.repair_record` KEEPS
  every LADDER-ARMED record regardless of `detect_ts` — the epoch is consulted
  ONLY for ladderless records. 80% of live records are ladder-armed, so passing
  an epoch as `trust` does NOT create a forward-only window. A real forward cut
  needs an explicit `detect_ts >= epoch` filter (see `run()`'s benched branch).
- **The resolution backfill only sees markets the BOT TRADED** (queues driven by
  `trades`/`paper_trades`/`traded_markets`/`positions`). The shadow lane writes
  none of those, so shadow-copied markets are refreshed only by accident. Do not
  assume `markets.resolved` is current for shadow markets — verify against CLOB.
- **Backfill poison-batch:** the queue orders `end_date ASC NULLS LAST` and keeps
  re-picking the OLDEST markets, which are the permanently-unresolvable ones ⇒
  ~3 resolutions per 30-min cycle. A "backlog < N" gate is therefore UNREACHABLE
  and must never be used as a precondition.
- **A stale-stats plan can defeat a correct index.** The partial index alone did
  not fix the resolution-queue timeout; it only took effect once `markets` was
  analyzed. After adding an index to a big table, ANALYZE before concluding the
  fix failed.
- **CLOB is the trustworthy resolution source** (`resolution_backfill.
  _fetch_market_by_condition_id` + `_clob_to_market_format`, resolution derived
  from token PRICES which reflect UMA settlement). Verified 196/196 with 0
  mismatches and 0 unreachable. Gamma cache format is
  `{condition_id: {resolution, resolved_at, yes_token_id, no_token_id, category}}`
  and the deep-dive merges it under the DB (DB wins, gamma fills holes).
- **API cache rows key the market as `marketId`** (NOT `conditionId`) —
  `copyable_cache/<addr>.json` → `{"status":..., "trades":[{marketId, tokenId,
  side, size, price, timestamp}]}`. Guessing the field name silently yields 0.

- **The V1 exchanges are DEAD for live flow (2026-07-12).** Any forward-
  looking on-chain detection MUST use the V2 exchanges + topic0
  `0xd543adfd…` (constants in `mirror_v3/copy_watcher.py`); V1
  `OrderFilled` via `blockchain_client` constants is history-only (audits
  of pre-migration fills). A watcher/canary pointed at V1 reads as
  "running, zero events" — silently. Also: the V2 fill event does NOT
  carry BUY/SELL; direction needs the receipt's transfer logs.
- **A frozen session's unpushed work is GONE (2026-07-12).** Remote session
  containers are ephemeral; when the `irq7r5` session froze, its built-but-
  unpushed ladder-capture patch died with it and had to be rebuilt from
  scratch. Push after every completed unit of work, before idling or waiting
  on the operator — "built, tested" means nothing until it is on origin.
- **The chain audit's ±window matcher BLENDS same-token trades** — two real
  trades at different prices inside the window produce a chain price that
  matches neither API row → false DISCREPANT. Any future audit verdict
  should use (or cross-check with) the tx-exact matcher
  (`scripts/readjudicate_discrepant.py`); the audit's own mismatches are an
  upper bound on real discrepancies, not a count of them.
- **web3 v7 renamed `get_logs` kwargs to `from_block`/`to_block`** — the
  camelCase spelling TypeErrors on EVERY call and a bare `except` can launder
  that into "rpc_error" (2026-07-10: 580/580 dead samples). Use
  `get_logs_compat`; the real-library binding test in
  `test_audit_roster_chain.py` guards the regression. Same latent bug still
  in shared `base_engine/data/{blockchain_client,uma_proposal_monitor,
  oracle_monitor}.py` — NOT fixed (shared-module protocol).
- **`get_block_number_from_timestamp` (shared client) is a one-shot linear
  estimate at 2.0s/block — off by ~1-2M blocks a year back.** Never use it
  to window a chain search. Use `locate_block_by_ts` (Newton on real block
  timestamps, audit script) instead.
- **publicnode 403s archive eth_getLogs; polygon-rpc.com is key-gated;
  blastapi discontinued.** Probe-verified working free archive-logs endpoint:
  `https://polygon.gateway.tenderly.co`. Probe with a curl BODY built
  server-side (sed placeholder trick) — PowerShell mangles `\"` JSON.
- **PowerShell one-liners: never include `$` or `"`** inside the SSH command
  string ($var interpolates to empty; `\"` arrives as literal backslashes).
- **Two detached runs writing the same log/JSON corrupt both** — always kill
  the previous run (exact PID, not pattern) before relaunching a pipeline
  that writes the same output paths.
- **gamma `/markets?condition_ids=` is a silent no-op** — HTTP 200, `[]`,
  zero errors; it burned two full backfill runs before being caught
  (probe-verified 2026-07-10). Per-key CLOB `/markets/{condition_id}` is the
  production-proven path (`resolution_backfill.py:17`). Never batch-filter
  gamma by condition id.
- **`pkill -f <pattern>` where the pattern appears in your own SSH command
  string kills your own session** before the rest of the command runs (bit
  us 2026-07-10: the kill+re-clone one-liner died at the clone). Use a
  bracket pattern (`backfill_resol[u]tions`) in operator one-liners.
- **All copy-trader investigation data is in `/tmp`** — one VPS reboot erases
  ~14h of API downloads + labels. Durable copy: §5 last item.
- **`/tmp/walkforward.log` + `/tmp/walkforward2.log` are superseded
  artifacts** (stale pre-pipeline run; provisional DB-only-label PASS).
  The citable run is `/tmp/walkforward3.*` only.

- **`bots/mirror_scoring/validation.py` is a false-PASS machine** (confirmed 2026-07-09, adversarially verified): `_UNIVERSE_SQL` has no time bound, BH admission keys on the post-cutoff test half, and the "out-of-sample" rejected-signals set shares the same post-cutoff (trader, market, outcome) randomness. Any PASS it emits is selection noise — never clear the UNVERIFIED label with it, never cite it in a go decision. (FAILs are directionally credible: the bias runs the other way.)
- **order_gateway neg-risk block** no-ops for MB by accident (CLAUDE.md "DORMANT LANDMINE"). "Repairing" the index re-creates Bug 14 (election blackout). Leave it.
- **Do NOT add a `neg_risk=True` filter** anywhere (CLAUDE.md).
- **`mirrored_trades` is bookkeeping, not a guard** — the real same-side dedup is the `_open_positions` scan.
- **CANARY_AUTO_ADVANCE unset → true** by code default. Any live-capable path must set it false explicitly.
- **orderbook_snapshots is aggregated buckets, not L2** — precise replay needs `shadow_fills.book_snapshot`.

### Added 2026-07-14 (local steward session)

- **CLOB `/price` `side` names the BOOK SIDE read: BUY=best bid, SELL=best
  ask.** The watcher shipped with it REVERSED (every record's bid/ask
  swapped, fills at the bid). Pin it any time with
  `scripts/verify_clob_price_sides.py`; the watcher now alarms LOUDLY on a
  crossed book. Fix-deploy epoch `1783985376` — analyze_shadow needs
  `--trust-quotes-after` that value for ladderless post-fix records.
- **`scripts/audit_roster_chain.py` is V1-ONLY — its not_found column is
  structurally inflated for post-migration fills.** Superseded by the
  dual-era `readjudicate_discrepant.py` (`fa21111`). Never adjudicate
  anyone on the old audit's not_found numbers.
- **not_found is an evidence gap, NEVER an accusation (operator rule
  2026-07-14).** The quota rule is gone; escalate the search (window,
  samples, second RPC, dual-era) until silence survives an EXHAUSTIVE
  sweep — only then is it fabrication evidence.
- **The HFT/bot filter (`looks_like_market_maker`) judges a 1-2.5 day
  burst page, not lifetime rate** — it can dismiss bursty humans (34
  borderline, incl a run-1 strong candidate). The fair lifetime test only
  exists for FETCHED histories; chain deep-dive Tier 4 replaces it.
- **The local Claude app yanks the MAIN checkout between session branches**
  (mis-branched a commit 2026-07-13). MB local work happens in the
  dedicated worktree `C:/lockes-picks/mb-steward` (operator-directed
  exception to the parent-dir fence; the checked-out branch is thereby
  locked). TWO WRITERS share the branch — `git pull --ff-only` before
  every commit.
- **`pgrep -f` waiters self-match their own command line** — bracket the
  pattern (`discrepan[t]`) in the WAITER too, or wait on output files
  (bit us 2026-07-13: a waiter hung 3+ hours on itself).
- **Shared `.env` line 367 is malformed** — python-dotenv aborts there;
  scripts needing DATABASE_URL must `set -a; . /opt/pa2-shared/.env` in
  the shell (runbook pattern) AND call `await db.init()` (crypto_kill
  shipped without it — any new DB runner needs an integration smoke-run
  `--days 1` before handover, not just unit tests).
- **`detect_lag_s` can be legitimately negative** (~-1s; producer-set
  block timestamps) — clamp to 0 in ANALYSIS, never "fix" the recorder.
  `block_ts` falls back to detect-time on fetch error (lag reads 0).

### Added 2026-07-14 PM (chain deep-dive build + batch launch)

- **`/opt/pa2-shared/mb_copyable_data` is ROOT-owned** (`drwxr-xr-x root
  root`) — `polymarket` (which runs the batch via `sudo -u polymarket`)
  CANNOT create files/dirs there. The deep-dive batch writes into a
  pre-created `polymarket`-owned subdir `.../deep_dive/` (both `--out-dir`
  AND `--out` live inside it). Any new polymarket-run job writing durable
  output needs a `sudo mkdir + sudo chown polymarket:polymarket` subdir first
  (first batch launch crashed at `os.makedirs` PermissionError).
- **`pgrep` without `-f` does NOT match python-script jobs** — the process
  `comm` is `python`/`python3`, not the script name, so `pgrep 'chain_deep_
  dive[.]py'` returns 0 (false "it died!"). ALWAYS use `pgrep -f` (or
  `pgrep -fc`) for these; the batch was falsely reported dead once this way.
  A `setsid`-launched job is reparented to PID 1 — verify with `ps -ef`.
- **`sudo env DATABASE_URL=...` exposes the DB password in the process
  table** (`ps -ef`). It is a localhost pgbouncer credential and this matches
  the deployed-service pattern (sudo scrubs env, so the value must be passed
  as an argv assignment) — acceptable on the single-tenant VPS, but do NOT
  echo `ps`/pgrep output containing it into chat/logs. Extract it at runtime
  (`grep '^DATABASE_URL=' /opt/pa2-shared/.env`) so it never lands in a file.
- **chain_deep_dive reconciliation must PRESERVE reconstructed direction** —
  reconcile_api_to_chain only takes BUY-side chain fills as candidates; folding
  SELLs/direction-unknown V2 fills into BUY-shaped candidates would let an API
  BUY 'verify' against a SELL (mask a lie) or false-mismatch (false REJECT).
  Same discipline: `direction_complete` (v2_receipts >= v2_txs) gates the
  direction-dependent tiers — a receipt-capped sweep reads real BUYs as unknown
  and would manufacture false not_found → INSUFFICIENT (raise --max-receipts),
  never REJECT. And reconcile ONLY API BUYs inside the SWEPT block window
  (`window_api_buys`) — a bounded/narrow sweep vs full-history API BUYs invents
  false FABRICATION (both bugs were smoke-caught 2026-07-14, now unit-tested).
- **UNCOPYABLE (fill-rate) is checked BEFORE the direction-complete gate** —
  it needs no receipts, so a genuinely un-tailable HFT account rejects fast
  without paying for full receipts; and it uses FRACTIONAL span-days (integer
  floor inflated the rate for the 1-2.5-day-history borderline cohort).

### Added 2026-07-15 PM (shadow readout — stale-label trap)

- **`analyze_shadow.py --gamma-cache` SILENTLY GOES STALE → false "0
  resolved / UNDERPOWERED" that MASKS the real edge.** The gamma resolution
  cache (`copyable_cache/gamma_resolutions.json`) is from 2026-07-10 and
  covers ZERO of the shadow markets (07-13+), so the readout reported 0/30
  resolved when the live `markets` table already knew ~10 resolved — AND the
  early edge on those was NEGATIVE (the stale cache hid a real signal).
  Operator caught it ("0% chance 0 are closed after 3 days"). **NEVER trust
  the default gamma cache for a readout.** Use `scripts/shadow_readout.py`
  (rebuilds token→outcome FRESH from `markets` every run; per-cohort split via
  `analyze_shadow --traders`; writes an ALERT on power-bar / negative-firming).
  This is the Forbidden-Pattern-9 discipline: an impossible number (0 resolved)
  means the QUERY is wrong — fix the source, don't explain it away.
- **EARLY FORWARD SIGNAL (2026-07-15, DESCRIPTIVE, n=10 UNDERPOWERED):**
  cohort-1's shadow edge on the ~10 resolved-so-far is **NEGATIVE**
  (edge ≈ -0.048, P(edge>0) ≈ 0.37) net of the ~1c copy tax. NOT a verdict
  (need ≥30), but it leans the WRONG way — the retrospective +edge may not
  survive our spread/latency. Watch as resolved climbs; the same tax applies
  to the 8 cohort-2 admits, so their forward shadow is the real test.
- **Shadow token→outcome join:** `markets` rows key outcomes by
  `yes_token_id`/`no_token_id` (resolution YES ⇒ yes-token won). The shadow
  records carry only `token_id` (no condition_id), so resolve via those two
  columns, not condition_id.
- **Watcher-fidelity audit 2026-07-15 PM (operator-challenged, MEASURED
  CLEAN):** operator challenged the low shadow wager counts ("0% chance
  elites trade this little"). Head-to-head vs the independent data-api per
  trader over each cohort's own window: cohort-1 matches near-exactly
  (197=197, 1171=1171, 82=82; the 9 zero-record C1 traders show 0 API buys
  too — genuinely idle 2 days), cohort-2 windowed at its 19:20:45Z start
  matches exactly (2=2, 1=1, rest truly 0 fills). 0 dropped-window markers, 0
  side-unknown skips. VERDICT: instrument faithful; the confusion was
  cohort-mixing (the 38-170/day rates are COHORT-2 machines; cohort-1 are
  slow/idle humans) + units (records ≈ re-buys; graded unit = first-buys).
  CAVEAT THIS SURFACED (precise form): cohort-1 flow is EXTREMELY
  concentrated, on BOTH axes — RECORDS are 72% one trader (0x84dbb7,
  1,171/1,627 — mostly re-buys), while the EDGE ESTIMAND (first-buys) is
  led by a different one (0x448861, ~20/51 ≈ 39%). Either way a pooled
  cohort number can be one trader's story. STANDING OPERATOR RULE
  (2026-07-15): every readout DISCLOSES concentration inline and auto-
  prints a leave-one-out line when the top trader ≥ 50% of first-buys
  (shadow_readout `concentration()`/`--conc-threshold`); every ALERT
  carries it; NO aggregate is presented without its composition checked
  first. Even the C2 machines are bursty (0x0e5bd7: 0 fills in its first
  5.5h despite a ~111/day lifetime rate).
- **The daily readout cron is BRANCH-PINNED + leaves a root gitconfig
  mutation (session-close review #17/#30):** `deploy/shadow_readout_cron.sh`
  hard-resets `/opt/pa2-shared/mb_readout` to `claude/repo-setup-docs-fq9bhn`
  daily — if the MB lane moves branches, UPDATE the BR pin or the readout
  runs frozen code forever (a refresh failure now writes a WARN line into
  `shadow_readout_log.txt`). Cohort membership is read from
  `chain_audit.json` at runtime (not code), so admissions don't need a code
  change — but they DO need the cohort ledger keys extended or the readout
  refuses to run. Setup also left a `safe.directory /opt/pa2-shared/mb_readout`
  entry in ROOT's global gitconfig (harmless, recorded here).
- **An RPC await with no read-timeout can park a batch FOREVER — and
  process-liveness monitoring cannot see it (2026-07-16):** run-2 hung ~13h
  on ONE `get_transaction_receipt`/`get_logs` await (zero CPU, ZERO open
  sockets) while the event loop stayed alive — `db_pool_health` heartbeats
  kept printing, so `ps`/pgrep checks looked healthy. Fixed `07e7296`:
  `rpc_call()` wraps EVERY chain RPC in `asyncio.timeout(90)` (hang → counted
  retryable error). Monitoring rule: watch LOG GROWTH (the code heartbeats
  through every phase), never just process existence. Timeout-guard every
  network await in any new long-running chain runner. **The LIVE WATCHER
  shared this class (6 unguarded web3 awaits, no systemd watchdog) — fixed
  `336f6a4` (rpc_call wrapper, 43 tests green), **DEPLOYED 2026-07-16 16:40:53
  UTC (operator go): /opt/mirror3 = `5c91261`, watcher blob verified
  byte-identical to tested, roster=24 reloaded, canary 1216, 0 alarms.
  Restart boundary note: FirstBuyDedup reset at 16:40:53Z.** Sibling one-shot
  scripts (audit_roster_chain, readjudicate) share the class but are
  operator-attended — a hang is visible, not silent (documented, not churned).
  Restart side-note: each watcher restart resets FirstBuyDedup, so a token
  seen before restart can record first_buy=True again — token-clustered
  analysis absorbs it, but don't be surprised by duplicate firsts at restart
  boundaries.
- **pkill self-match, VARIANT 2 (bit TWICE 2026-07-15):** bracketing the
  pkill pattern (`chain_deep_di[v]e`) is NOT enough when ANY OTHER clause of
  the same SSH command contains the literal name — a `pgrep -fc
  'chain_deep_dive[.]py'` check, or even a file path (`git hash-object
  scripts/chain_deep_dive.py`). pkill -f matches the whole remote shell's
  command line, which includes those literals → kills your own session
  mid-command (exit 255; the rest of the command never runs — our /tmp/mbre
  refresh silently didn't happen). RULE: a kill command contains the pkill
  and NOTHING ELSE that names the target; verify/refresh in a SEPARATE
  ssh command afterwards (plain `ps -ef | grep` there is safe — no pkill in
  that shell).
