export const meta = {
  name: 'kalshi-pivot-select-build',
  description: 'Never gate down to fewer markets — backfill the footprint with EARNING markets (pivot past gated ones, reach deeper into high-density series to near-money strikes), behind an off-by-default flag, tested + adversarially verified',
  phases: [
    { title: 'Design', detail: 'read select_footprint + the quote-gen gates; design the minimal backfill/pivot change that fills FOOTPRINT_TOP with QUALIFYING markets, density-weighted' },
    { title: 'Implement', detail: 'code it behind KALSHI_PIVOT_SELECT (default 0 = provable no-op) + pin tests + run pytest, commit' },
    { title: 'Verify', detail: 'adversarial: flag-off no-op, never quotes a non-earning market, does not thrash/over-read, reducing/unwind untouched' },
    { title: 'Deliver', detail: 'ship checklist, md5, deploy+rollback' },
  ],
}

const WT = 'C:/Users/samwa/AppData/Local/Temp/claude/C--lockes-picks-polymarket-ai-v2/02f270fe-27ab-42e6-8906-2ebc25f6df3b/scratchpad/kalshi-wt'

const RULES = `
KALSHI MAKER LANE. Worktree: ${WT} (branch claude/maker-kalshi-live). cd ${WT}; bash cwd drifts, use absolute paths.

=== THE PROBLEM (measured live 2026-07-24 ~12:10Z, DEFINITIVE) ===
The bot quotes only ~9 of a FOOTPRINT_TOP=40 book, and is in 0 of 17 eligible gas-daily strikes (our BEST market, cap/mkt 150/day). Root cause is SELECTION, not the gates:
1. select_footprint (maker_kalshi_quoter.py:282-343) sorts eligible programs by usd_day desc, then ROUND-ROBINS 1 market/series/round across all active series until FOOTPRINT_TOP. With 8 active series that gives EACH series exactly 5 slots — so gas-daily (usd_day 150.2) gets the SAME 5 slots as KXH100MON (usd_day 2.6). Egalitarian dilution of our best market.
2. Within a series, rows are tie-broken by TICKER ascending (:320 rows.sort by (-usd_day, ticker)), so gas-daily's 5 slots go to the 5 LOWEST strikes 4.065-4.085 — deep-ITM, best_yes~0.98 — which then FAIL the price-bound gate at quote-gen (:482-484, best bid must be in (0.04, 0.96]). The NEAR-MONEY strikes 4.100-4.135 (best bids ~0.15-0.85, symmetric, in-bounds) that WOULD quote and earn are never selected (they're 6th-16th by ticker, beyond the 5-slot round-robin).
3. Result: gas-daily contributes ~1 quotable strike; 31 footprint markets gate out at quote-gen (gated_out = len(footprint)-len(desired), :1357); the bot just quotes FEWER (9) instead of backfilling.

VERIFIED gate outcomes on the 17 live gas-daily strikes (best_y/best_n, gate): 4.065-4.080 price-bound-DROP (best_y 0.88-0.98), 4.085-4.135 **WOULD QUOTE** (11 strikes, symmetric+in-bounds), 4.140-4.145 price-bound-DROP. So 11/17 gas-daily strikes are quotable but only ~5 low ones are ever selected and 4 of those are gated.

=== OPERATOR DIRECTIVE (verbatim intent, to hardcode) ===
"We NEVER gate any markets. If the rewards aren't there we PIVOT to another trade. Hardcode this."
Interpretation: the footprint must not shrink to fewer markets because some got gated. When a candidate market fails a gate (can't earn: price-bound / lopsided / unqualifiable / crossed), the bot must PIVOT — pull the NEXT eligible market into the slot — and keep going until FOOTPRINT_TOP markets are actually QUOTED (or the eligible pool is exhausted). And density matters: our best series (gas) should not be capped to an egalitarian 5 while H100 also gets 5. Fill all slots with EARNING markets, weighted toward reward density.
The GATES STAY (they correctly identify non-earning markets — quoting a $0-reward or lopsided book loses money). The change is BACKFILL/PIVOT, never "remove the gate and quote garbage".

=== THE FIX (design constraint — keep it MINIMAL + SAFE, behind a flag) ===
New flag KALSHI_PIVOT_SELECT (_envi, DEFAULT 0 = OFF, provable no-op = today's exact behavior).
When ON:
- OVER-SELECT: build the ordered eligible candidate pool larger than FOOTPRINT_TOP (e.g. all eligible, still in density-then-proximity order), so the quote loop can reach past gated markets.
- FILL BY QUALIFICATION: iterate the ordered pool; run the SAME quote-gen (desired quotes for a market); if it returns [] (gated), SKIP and pull the next candidate; if it returns quotes, keep it; stop once FOOTPRINT_TOP markets have real quotes OR the pool is exhausted. This is the "pivot instead of gate" behavior.
- WITHIN-SERIES ORDERING: prefer NEAR-MONEY strikes (best bids well inside (0.04,0.96), i.e. closest to the balanced/tradeable region) over extreme-ticker deep-ITM/OTM strikes, so we pick the strikes that actually qualify. (Sorting strikes by proximity of the mid to 0.50, or by whether both best-bids are in-bounds, achieves this without a book read if the market object carries a price; if a book read is needed, note the added reads.)
- DENSITY WEIGHTING: do not cap the top series at an egalitarian 5. Let a high-density series (gas) take more slots (bounded by PER_SERIES_CAP and by leaving SOME room for diversification if the operator's config wants it). Simplest: after ensuring at least 1-2 slots per active series for coverage, fill the REMAINDER by pure density (gas-first). Or make the round-robin fill by density-weighted rounds. State the exact rule.
BOUND THE COST: over-selecting + qualifying more candidates means MORE orderbook reads per cycle. Cap the candidate pool (e.g. 2-3x FOOTPRINT_TOP) so reads stay bounded; log reads. Do NOT introduce an unbounded loop.
DO NOT touch: the gates themselves (:480-547), reducing/unwind quoting, the risk breakers, the funding gate, the loss meter. Only the SELECTION + fill logic changes, behind the flag.

=== HARD CONSTRAINTS ===
1. Edit ONLY maker_kalshi_quoter.py (selection/fill) + a test file. No deploy, no live.env, no ssh-write, no systemctl. The worktree edit does NOT affect the running VPS bot.
2. FLAG DEFAULT 0 must be a PROVABLE no-op (byte-for-byte today's select_footprint + quote loop). The code must ship deployable with zero live change.
3. Preserve every function signature; reducing/unwind + all guards unchanged. Kalshi venue only.
4. COMMIT to the branch when green: git -C ${WT} add kalshi_live/maker_kalshi_quoter.py kalshi_live/test_pivot_select.py && git -C ${WT} commit -m "feat(kalshi): pivot-select — backfill footprint with earning markets behind KALSHI_PIVOT_SELECT (default off) [NOT DEPLOYED]". Return sha + full diff + pytest output.
5. Kalshi authed reads for measurement work locally (cd ${WT}/kalshi_live && python3, module L). Orderbook: L.get(L.P+f'/markets/{t}/orderbook')['orderbook_fp'] with keys yes_dollars/no_dollars = [[price_str,size_str]] (NOT 'orderbook'/'yes'/'no' — that returns empty).

=== TESTS (kalshi_live/test_pivot_select.py) ===
1. FLAG-OFF NO-OP: with KALSHI_PIVOT_SELECT unset/0, select+quote produces the SAME footprint/quotes as before the change on a fixture where some markets gate. (Assert against the legacy path.)
2. THE FIX PIN: a fixture like today's gas book — 5 selected strikes where 4 gate (price-bound) and near-money strikes qualify but sort later by ticker. Flag OFF: only ~1 gas strike quoted (legacy). Flag ON: the footprint BACKFILLS to the near-money qualifying strikes → many more gas strikes quoted, footprint filled with earners. FAILS on legacy, PASSES on fix.
3. NEVER-QUOTE-A-NON-EARNER: flag ON must still return [] (not quote) a genuinely unqualifiable/$0-reward/lopsided market — pivot means quote a DIFFERENT earner, never quote the bad one. Assert a lopsided/price-bound market is still skipped.
4. BOUNDED READS/POOL: flag ON caps the candidate pool (no unbounded loop); assert it stops at the cap and does not read more than the cap's worth of books.
Run the FULL suite: cd ${WT}/kalshi_live && python -m pytest test_*.py -q. ALL must pass (164 + new).
`

phase('Design')

const design = await agent(`${RULES}

TASK — DESIGN THE MINIMAL PIVOT-SELECT FIX.
1. Read maker_kalshi_quoter.py:282-343 (select_footprint), :460-560 (quote-gen gates incl the price-bound :482 and unqualifiable :504 and selection :513 gates), and the quote loop where 'desired' is built + gated_out computed (:1245-1360). Understand exactly how footprint -> desired -> created flows, and where to insert backfill.
2. Decide the EXACT mechanism: (a) how to over-select the candidate pool (ordered how — density then near-money proximity), (b) where the pivot/backfill loop lives (in the quote loop, pulling next candidate when quote-gen returns []), (c) the density-weighting rule for slots, (d) the read-cost bound.
3. Show the flag-gated pseudo-code, the exact insertion points (line refs), and why flag-off is byte-for-byte the legacy path.
4. Specify the 4 pin tests concretely (fixtures).
RETURN: the design (mechanism + pseudo-code + insertion points + flag-off no-op proof + test specs).`,
  { label: 'design', phase: 'Design', effort: 'high' })

phase('Implement')

const impl = await agent(`${RULES}

DESIGN TO IMPLEMENT:
${String(design).slice(0, 12000)}

TASK — IMPLEMENT the pivot-select fix + the 4 pin tests exactly per the design and RULES. Flag KALSHI_PIVOT_SELECT default 0 = provable no-op. Bound the candidate pool + reads. Do NOT touch the gates, reducing/unwind, guards, funding gate, or loss meter. Add the 4 tests. Run the FULL pytest suite; fix until green (or flag a pre-existing unrelated failure with evidence). Commit to the branch.
RETURN: commit sha, the COMPLETE unified diff of maker_kalshi_quoter.py + the test file, the exact pytest summary, and a 4-line plain-English description of the change + why flag-off is a no-op.`,
  { label: 'implement', phase: 'Implement', effort: 'high' })

phase('Verify')

const verds = await parallel([
  'flag-off-is-a-noop', 'never-quotes-a-non-earner', 'no-thrash-or-unbounded-reads', 'gas-actually-fills-now',
].map(lens => () => agent(`${RULES}

TASK — ADVERSARIALLY VERIFY the pivot-select change. Lens: **${lens}**. DEFAULT TO REFUTED IF UNCERTAIN. Live-money selection change.
Read the committed diff READ-ONLY: git -C ${WT} show HEAD and git -C ${WT} diff HEAD~1 HEAD -- kalshi_live/maker_kalshi_quoter.py. Do NOT edit.
- flag-off-is-a-noop: prove KALSHI_PIVOT_SELECT unset/0 gives byte-identical footprint/desired/plan to the legacy path. Any divergence when off = REFUTED.
- never-quotes-a-non-earner: prove flag-ON still returns [] for a genuinely unqualifiable / lopsided / price-bound / crossed market. Pivot must NEVER mean 'quote the bad market'. Find any path where a $0-reward or fill-risky market gets quoted under the flag.
- no-thrash-or-unbounded-reads: prove the candidate pool + orderbook reads are BOUNDED (no unbounded loop, no per-cycle read explosion). Check cancel/create churn doesn't thrash when the qualifying set shifts cycle-to-cycle.
- gas-actually-fills-now: prove the fix WOULD put us into the near-money gas strikes (the whole point). Run the fixture / logic: does flag-ON select+quote the qualifying gas strikes (4.085-4.135) that flag-off misses? If it doesn't actually fix gas, REFUTE.
RETURN: refuted true/false, severity, the specific state, and the check that settles it.`,
    { label: `verify:${lens}`, phase: 'Verify', schema: {
      type: 'object',
      required: ['lens','refuted','severity','finding'],
      properties: {
        lens: { type: 'string' }, refuted: { type: 'boolean' },
        severity: { type: 'string', enum: ['CRITICAL','HIGH','MEDIUM','LOW','NONE'] },
        finding: { type: 'string' }, failing_state: { type: 'string' }, settling_check: { type: 'string' },
      },
    } })))

phase('Deliver')

const final = await agent(`${RULES}

IMPLEMENTATION: ${String(impl).slice(0, 8000)}
VERIFIER VERDICTS: ${JSON.stringify(verds.filter(Boolean), null, 1)}

TASK — write docs/maker_handoffs/KALSHI_PIVOT_SELECT_BUILD_2026-07-24.md and return it: (1) what changed in plain English + flag-off no-op proof; (2) every verifier verdict, lead with any CRITICAL/HIGH; (3) if green: pytest counts, the new-file md5 (git -C ${WT} show HEAD:kalshi_live/maker_kalshi_quoter.py | md5sum), the per-file md5-gated deploy step, rollback (flag unset = no-op, or git revert); (4) the expected LIVE effect when flag flipped on (gas fills to N near-money strikes, footprint fills to ~40 earners); (5) Tier-3 change — operator sign-off + md5-gated deploy before live. Return the doc.`,
  { label: 'deliver', phase: 'Deliver', effort: 'high' })

return { final, verds: verds.filter(Boolean), implPreview: String(impl).slice(0, 1500) }
