# Scoring-Algo Review Findings — 2026-07-06 (pre-validation gate)

**Scope:** full read of `bots/mirror_scoring/` (9 modules, 1,313 lines incl. runner)
plus its interaction with the new v3 signal collector. **Review only — nothing
implemented.** Findings are recommendations; statistical-lane fixes belong to the
`mb-formula-review` lane per the handoff protocol, findings 2–3 are MB-lane.

**Headline:** do NOT treat a validation PASS as trustworthy until F1 is fixed,
and do NOT stop old MB until F2 is resolved. A validation FAIL is also muddied
(F6 biases toward FAIL while F1 biases toward PASS — the two don't cancel, they
just make either outcome ambiguous). Fix F1 (+cheap F4) first; then the run
means something.

---

## F1 — HIGH · Validation set is not disjoint from the admission test half (circularity → false-PASS risk)

`q_score.py` admits traders using `p_holdout` computed on the **test half**:
their own trades in markets **resolved after the cutoff**. `validation.py` then
"validates out-of-sample" on rejected signals with `event_time > cutoff` — but a
rejected signal IS the whale's own trade print (MB saw it and declined to copy);
the same print/market routinely exists in the `trades` table too. So the same
(trader, market, outcome) can sit on **both** sides: it helps admit the trader,
then re-scores them in "validation." Traders selected for winning post-cutoff
markets are re-measured on those same markets → admitted-vs-others spread is
inflated **by construction** → the kill criterion can PASS without any true
out-of-sample information. This is the dangerous direction for a kill gate.

**Recommendation:** in `validate_ranking`, exclude any (trader, condition_id)
pair present in that trader's scoring entries (or at minimum in their test half)
before computing edges; report the overlap count in the ValidationReport so the
degree of contamination is visible. ~15-line change, no schema impact.

## F2 — HIGH · Stopping old MB may stop resolution-labeling of v3 rows (MB lane — my code/runbook)

`mirror_rejected_signals.resolution` is backfilled only by a running
`base_engine` instance (resolution listener `base_engine.py:1796`, scheduler
`ingestion_scheduler.py:391`, sweep `resolution_backfill.py:552`). The v3 silo
(`mirror_v3/run.py`) does **not** run any of these. If `polymarket-mirror.service`
was the only engine writing that backfill, then following my deploy runbook's
"stop old MB" step ends labeling — v3 rows keep accumulating with
`resolution=NULL` forever, and both the acceptance gate and `validate_ranking`
starve (labels are their input).

**Recommendation:** before stopping old MB, verify on the VPS that another
engine unit (e.g. the main fleet service) is live and emitting the
"Resolution event: backfilled …" log line. If not, wire a periodic
`backfill_mirror_rejected_signals_resolution()` call into the v3 heartbeat
(safe: it's the shared idempotent UPDATE with the temporal guard) BEFORE the
stop step. Runbook `docs/VPS_DEPLOY_v3_collector.md` must gain this check either
way.

## F3 — MEDIUM-HIGH · v3 collector stream mixes into validation unfiltered (MB lane)

`_REJECTED_SQL` has no filter on `rejection_reason`/`metadata->>'source'`. Once
the v3 collector deploys, post-cutoff rows are a mix of two differently-shaped
populations: old-MB gate rejections (whale-floored, strategy-filtered) and the
v3 raw stream (no floor, includes signals old MB would have copied). Not a
hindsight bug, but an uncontrolled population change mid-window: runs become
non-comparable and `DISTINCT ON` may pick either stream's row per (trader,
market).

**Recommendation:** either sequence the first validation run BEFORE deploying
the v3 collector, or add an explicit stream filter to `_REJECTED_SQL`
(`WHERE COALESCE(metadata->>'source','') <> 'mirror_v3'` for the legacy
population, or `= 'mirror_v3'` once v3 is the sole writer). One line; decide
per-run and record which stream was used in the report.

## F4 — MEDIUM · Case-sensitive address joins; silent zero-row failure mode

`validate_ranking` matches `r.trader_address = ANY(:traders)` and
`d["trader_address"] in admitted` — both case-sensitive. `trades.user_address`
(scoring ids) and `mirror_rejected_signals.trader_address` (RTDS `proxyWallet`
via `_log_rejection`) are each stored **as received**; no write site lowercases
(verified by grep). If casings ever differ between the two tables, traders
silently match zero rejected rows → spurious "insufficient post-cutoff resolved
signals" FAIL, or a skewed admitted/other split — with no error.

**Recommendation:** `LOWER()` both sides of the SQL match and lowercase the
Python set membership. Also worth a one-off VPS check:
`SELECT count(*) FROM mirror_rejected_signals WHERE trader_address <> lower(trader_address)`.

## F5 — MEDIUM · EB shrinkage pool is admitted-only → winner's curse under-corrected → inflated Kelly weights

`select_and_size` calls `empirical_bayes_shrink` on **admitted traders only**.
Shrinking toward the mean of the selected winners shrinks toward a
selection-inflated target — precisely the ~2.16× inflation the shrinkage exists
to counter (per `stats.py`'s own docstring). Downstream, `edge_shrunk` feeds
`kelly_event_weight`, so shadow sizing weights are systematically optimistic.
Harmless today (shadow-only) but it's the number that would size real bets
post-gate.

**Recommendation:** estimate the grand mean and tau² on the FULL scored pool
(all `TraderScore`s with finite se), then apply the shrinkage factor to admitted
traders' edges. Few-line change in `select_and_size`.

## F6 — MEDIUM · Kill-criterion test statistic is a nonstandard signed-mixture; variance inflated by group offsets (conservative → possible false-FAIL)

`validation.py:129-132` builds `diffs` (+edge admitted, −edge others), recenters
to `spread/2`, and runs the one-sample wild cluster bootstrap. The residuals of
that mixed series carry the between-group mean offsets (admitted rows center at
`mean_a − mean(diffs)`, others at `−mean_o − mean(diffs)`), which inflates the
bootstrap variance relative to a proper two-sample comparison — biasing toward
FAIL. Safe direction for a kill gate, but a genuinely predictive ranking could
be killed by construction noise; and combined with F1's opposite bias, neither
verdict is cleanly interpretable until both are addressed.

**Recommendation:** replace with a standard cluster-bootstrap two-sample test:
compute per-market (cluster) mean edge for admitted and for others, difference
them within shared clusters / aggregate across clusters, bootstrap the spread
directly. Alternatively regress edge on an admitted-dummy with cluster-robust
wild bootstrap on the coefficient — same machinery, clean estimand.

## F7 — LOW / informational

- `stats.bh_fdr`: if the best p-value were exactly 0.0 it would admit nobody
  (`thresh > 0` guard). Unreachable today — bootstrap p ≥ 1/(1+N_BOOT) — but a
  latent trap if a non-bootstrap p ever feeds the pool.
- `validate_ranking` drops tzinfo from an aware cutoff without converting to UTC
  (`cutoff.replace(tzinfo=None)`); unreachable via the runner (which normalizes
  to UTC-naive first), bites only on direct calls.
- `_UNIVERSE_SQL`'s OR-join (`m.condition_id = t.market_id OR CAST(m.id AS TEXT)
  = t.market_id`) likely defeats index use; already acknowledged via the 300s
  `SET LOCAL statement_timeout`, but expect a slow first run on the VPS.
- Wild bootstrap loop is pure-Python per replicate (dict remap per boot);
  at ~50k validation rows × 999 boots expect minutes of CPU, not seconds.

---

## Suggested sequencing (nothing implemented yet — operator to approve)

1. Fix **F1 + F4** (validation correctness; small diffs) — statistical lane, or
   MB lane with formula-review signoff since validation.py gates MB capital.
2. Resolve **F3** by decision: run validation on the legacy stream with the
   marker filter added.
3. THEN run the validation one-paste; the verdict is now interpretable.
4. **F2** before any "stop old MB" step — runbook check or v3 heartbeat backfill.
5. **F5, F6** next lane pass; both matter before any sizing goes live, neither
   blocks a ranking-only validation read (F6 noted as a conservative bias when
   reading a FAIL).
