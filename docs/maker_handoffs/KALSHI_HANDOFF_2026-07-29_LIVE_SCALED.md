# KALSHI MAKER — HANDOFF 2026-07-29 (LIVE and scaled; capital-aware ranking is task #1)

**BOT IS LIVE.** Went live 2026-07-29T01:18:31Z at $50, operator-stepped to $280 total /
$60 per market through the day. As of ~15:33Z: ~$261 resting across an 18-market footprint,
100% of scored placement at the reference price, book near-flat, zero unresolved errors.
Halt: drawdown arm $40 (MARK basis) + ratchet arm $120 (COST basis — churn-immune by
construction after the root fix). Panic stop: `sudo touch /opt/pa2-maker-kalshi-live/STOP`.

**THE OPERATOR RATIFIED THIS SESSION'S WORKING STYLE AS THE NORM** ("the work on this
session was perfect... carry that over as the norm"). The norms, binding:
1. VERIFY EVERYTHING INDEPENDENTLY before acting — state, numbers, prior sessions' claims.
   Provenance + ESTABLISHED/INFERRED labels on every figure (RULES ZERO/SIX).
2. SURGICAL FIXES: tests + mutation checks BEFORE every deploy; deploy byte-exact (md5 of the
   box file == the commit blob — always `git show` the LF file, never scp the CRLF worktree).
3. THE OPERATOR DECIDES: present finding -> options -> recommendation, execute only NAMED
   decisions; additive is free, removals/demotions need an ask (RULE NINE); stop means halt
   (RULE ELEVEN); never headline a blind gauge (RULE TWELVE).
4. ROOT-FIX OVER BAND-AID: when a guard fires falsely, fix what it MEASURES, not just its
   threshold (see the cost-basis ratchet split, `fe563b6`).
5. OWN YOUR REGRESSIONS: every operator-ordered change gets its own emergent defects found
   and fixed the same session (JOIN_SIZE=0 -> F2/F10 same-day).
6. Monitors are SIGNAL-ONLY (fill bursts / errors / >=$1 mark moves / hourly heartbeat);
   quiet ticks get no prose. Receipts > models: label every projection until the credit lands.

---

## TASK #1 (operator-ordered): CAPITAL-AWARE MARKET RANKING
When eligible markets exceed capital, the current ranking is pool-$ desc -> SCORE_RANK
(measured capture, `kalshi_market_scores.rank`) -> per-series round-robin. It is BLIND to:
- **Capital efficiency**: a $0.90-priced book consumes ~9x the capital per resting contract
  of a $0.10 book for a comparable reward share. Nothing ranks $reward/day per $committed.
- **Per-market fill cost**: adverse-selection losses differ wildly by book (the excluded
  index family bled ~2c/ct; quiet political books cost ~0). The fills tape now carries
  per-market realized cost — usable as a rank penalty.
- **Correlation**: sibling strikes/family concentration (D3, still tabled by operator).
Design sketch: score = (expected $reward/day from the R4 share model x measured-capture
calibration) / (dollars consumed at our quote sizes) − (per-market fill-cost rate from the
tape). Feed: kalshi_market_scores + /portfolio/fills + Thursday's receipt calibration.
Ship behind telemetry first (log the would-be ranking vs actual), flip on operator approval.

## STATE (verify it yourself; trust nothing including this doc)
- Branch `claude/maker-kalshi-live`; deployed = HEAD (verify md5 vs `git show HEAD:...`).
  Session commits: 228bedd fixes3+4 · c8c0357 unconditional risk rule + MTM halt + market-clock
  cap + slippage bound + strand 15s · de5e6d3 SERIES_DENY · 7a42c0d JOIN_SIZE=0 · 04982a1
  F2/F10 · 98cafa1 audit set (F1/F3/F6/F8/F9/F12/F13) · fe563b6 ratchet root fix · funnel
  prefilter + budget cap (last two commits).
- live.env: TOTAL=280, MARKET=60, JOIN_SIZE=0 (dollar-governed, INV_HARD 80ct ceiling),
  STRAND_CROSS_S=15, FLATTEN_MAX_SLIP=0.10, LOSS_HALT=40(dd/mark), DOWN_HALT=120(cost),
  HELD_MAX=100, GROWTH=40, band 0.04/0.96, ACTIVATE=40(+clamp), DENY=KXDXY,KXNDQ,KXINX,KXDJI,
  MAX_DAYS_TO_CLOSE=8, THROTTLE_SMART=0, WS_HOT=1, WS_BOOK_COLD=1, TAKER_FLATTEN=1.
- Suite: 631 passed / 2 xfailed / 0 failed. Mutation passes: 15/15 (fix set) + basis-revert +
  deny + F2/F10 mutants all killed.
- Day P&L 07-29 (mark): start $295.78 -> ~$277 (~-$19); ~$10 of it = the pre-exclusion index
  family (venue tape 01:44Z + episode decomposition), remainder = clean-fleet fill costs at
  2-4x scale + spread marks. NO reward credit landed yet.

## LIVE-VALIDATED THIS SESSION (cite freely)
Strand cross: first firing 01:25:20Z, 21 ct flat in 50s at a marked gain. Takers verified
position-reducing on the full tape (one 1-ct through-flat = the irreducible ~300ms read->IOC
race; no reduce-only order type exists). Stage B fired 3x live (~254-296ms reactions).
False-stop F5 fired exactly as predicted (ratchet $41.89 vs dd $18.44, 14:25:25Z) ->
root-fixed same hour. Funnel fix measured: footprint 5 -> 18, committed $93 -> $261.

## WATCHLIST / OPEN ITEMS (RULE NINE: none of these are demoted; order = operator's asks)
1. **Thursday 07-31: first reward receipt** (ballot-market window closes) — compare vs the
   $26.63/day model line for that market; start the receipt-vs-model calibration table.
2. FOOTPRINT_TOP=40 becomes the breadth lever when more capital arrives (operator: "we can
   raise caps later and will with more capital added").
3. Index family (KXDXY/KXNDQ/KXINX/KXDJI): TABLED NOT DEAD (operator: "toxc markets can be
   the most profitable... just dont rule out money in the future"). Re-entry needs a NEW
   design; velocity-conditioning was measured NEGATIVE 07-24 — do not re-warm it.
4. Two historical ctx_build_errors (budget starvation, fixed) remain in the daemon log
   counters — the signal-only monitor treats >2 as new.
5. F4 price-band: 0.04/0.96 now explicit; the old 0.01-0.97 default era is closed.
6. Rewards attribution M2b: per-event split still needs the manual UI/CSV pull.
7. `_offset_size` + `KALSHI_PAIR_BOTH_SIDES` remain orphaned pending an explicit removal ask.

## TOOLING THAT EXISTS (reuse, do not rebuild)
On the VPS /tmp: resting.py (portfolio read), session_econ.py + full_review.py (fills/P&L
attribution), episode_analysis.py (flat-to-flat round trips), est_rewards.py (R4 model),
funnel_audit.py (selection waterfall), live_summary.py (signal-only monitor feed),
verify_takers.py (exit-purity check). Local Monitor loop: 15-min signal-only summary.
