# R0a + R0b RESULTS — money ladder rungs 0a/0b (2026-08-13, READ-ONLY studies)

Both rungs of the ratified ladder (roadmap §3) ran 2026-08-13 ~14:5x–15:0xZ. GET-only +
on-box tape reads; nothing placed, cancelled, or modified. Scripts archived in session
scratchpad; frozen outputs on box `/tmp/R0A_PAPER_BACKTEST.json` (md5 `2d3bcdbe`) and
`/tmp/R0B_VENUE_CENSUS.json` (md5 `6b0d96b8`) — copy to a durable path before box reboot.

## R0a — PAPER BACKTEST (filters applied retroactively to our own tape)

**Reads (UTC 2026-08-13 ~14:56Z):** 1,630 fills (full tape, cursor-exhausted), 204
settlements, 63 credit rows; market meta for all 227 tickers (0 missing). Method =
D1 canon (position-aware `replay_fills`, event = `rsplit('-',1)[0]`, credits event-level,
$15 referral excluded).

**Sanity anchor (ESTABLISHED):** full-tape lifetime net **−$390.73** = D1's −$391.67
(read 01:01Z) + the +$0.94 gas/rain settlement landed 13:05:44Z. Exact reconciliation.

**Filters as implemented** (mechanism proxies, stated limits):
- `close8d`: event's first fill ≤ 8d before market close.
- `announcement`: title contains a discrete-actor announcement verb (keyword proxy).
- `ladder_near_strike`: market has a strike AND contract-weighted mean fill price in
  (0.10, 0.90).
- `resolution_proximity`: >50% of contracts filled <1h to close.
- Eviction (2×-with-paid-ratio): simulable ONLY where the estimates tape exists
  (2026-08-06+); min/target SIZING is NOT retroactively simulable — stated, not faked.

**Result (ESTABLISHED, full account history 07-15→08-13):**

| Subset | Events | Net | Paid | Realized | Fills |
|---|---:|---:|---:|---:|---:|
| Full tape | 115 | −390.73 | +189.06 | −579.79 | 1,630 |
| **KEPT (passes all filters)** | **19** | **−79.59** | **+29.58** | **−109.17** | **231** |
| Dropped | 96 | −311.14 | +159.48 | −470.62 | 1,399 |

Dropped-by (overlapping buckets): ladder_near_strike 86 events / −$329.14;
resolution_proximity 35 / −$24.63 (note: paid +$81.16 — the weather/daily winners live
here too, incl. KXTEMPNYCH); announcement 7 / −$29.74; close8d_fail 3 / −$1.66.

**KEPT subset by ERA (first-fill date; ESTABLISHED):**

| Era | Events | Net | Paid | Realized |
|---|---:|---:|---:|---:|
| 07-25→07-31 (scaling/defect era) | 6 | −51.23 | 18.41 | −69.64 |
| 08-01→08-09 (governors era) | 7 | −34.21 | 3.66 | −37.87 |
| **08-10+ (fixed build)** | **1** | **−1.66** | 0.00 | −1.66 (still-open TOPMODEL-26AUG17, incl. open-inventory basis) |
| zero-fill credit events | 5 | **+7.51** | +7.51 | 0.00 |

**Classifier-fidelity caveat (IMPORTANT):** the keyword announcement proxy MISSED
KXTRUMPTIME (−$39.91 across 2 kept events; title "Will Trump post on Truth Social…" has
no announce-verb) and KXCHINAAI (−$6.11) — both D3-measured toxic (≤ −10 c/ct). With
D3's MEASURED toxicity classes applied instead of keywords, kept-set net ≈ −$33.6.
Either way the direction is unchanged.

**R0a verdict (per-rung question: "is the filtered subset clearly credit-negative?"):**
The filtered subset was historically credit-negative ($29.58 paid vs −$109.17 realized)
— BUT the losses concentrate in the defect eras (Rule 7 attribution applies: the same
eras carry the known agent-defect classes), and the fixed-build sample is 1 filled
event + 5 zero-fill credits (UNPOWERED). The tape contains ZERO examples of the R2
shape (full-target presence, ~0 fills) other than the 5 zero-fill credits — which were
pure-positive. **The tape does NOT answer the mandate; it does not refute the presence
thesis; it re-confirms fills-lose/presence-pays. R1/R2 remain the live test.**
Eviction sim (INFERRED, denominator = 22 tape-covered events): 2 evictions triggered,
~$5.97 net avoided — small because coverage starts 08-06.

## R0b — FILTERED VENUE CENSUS (all active liquidity programs)

**Reads (UTC 2026-08-13T14:59:54Z→~15:07Z):** 3,722 active liquidity programs (single
page limit=10000, cursor exhausted, no alarm); meta for all tickers (batched, 0
missing); orderbooks for all 232 survivors (0 errors; 0.6s spacing). Venue total pool
**$266,543.33/day** (was $250,003.33 at D2's 01:01Z read — the universe moves daily).

**Funnel:** 3,722 → not-open −20 → close>8d −3,020 → toxic (announcement 43,
ladder-near-strike 222, resolution-proximity-24h 243) → **232 survivors**.

**Share model:** replica of the quoter's `_qualifying_score` R4 walk (ref = best bid;
score = DF^N·size; book that can't reach Target pays NOBODY; share at ref =
size/(rival_q + size); R3 both sides must qualify; $/day = pool·(s_yes+s_no)/2). Bot
was DOWN with 0 resting (verified 14:49:59Z) so fetched books are rivals-only.

**Headline (ESTABLISHED as a MODEL output — M7 canon: the capture model over-predicts
2–6× as an absolute, treat as RELATIVE ranking + upper bound):**
- Addressable at TARGET size, all 232: **$10,825/day** (upper bound, see M7).
- Addressable at TARGET size, **top-12 (concurrency cap): $1,415/day**.
- Addressable at MIN size (10 ct), all 232: $306.52/day — and that number assumes the
  book already qualifies on rival depth; **sub-target books model $0 (W10 floor — R1's
  question)**. 195 of 232 survivors have at least one sub-target side.
- **vs the ~$500/day kill threshold: PASSES at target size even under a 3× M7 haircut
  on the top-12 ($1,415/3 ≈ $472 ≈ threshold; the full-232 bound gives wide margin —
  but capital, not concurrency, then binds).** The arithmetic-kill branch does NOT fire.

**Candidate quality flags (carry into R1/R2 selection):**
- KXTRUMPTIME strikes rank high in the census but are D3-MEASURED toxic (−10.0 c/ct,
  announcement mechanism) — the census keyword proxy missed "post on Truth Social".
  Selection must overlay D3 measured classes on top of census keywords.
- KXTOPMODEL-26AUG17-CLAUM is a RESIDUAL wind-down position (EXIT-ONLY pinned) — not
  a new-entry candidate; sibling strikes share its event.
- Best floor-probe shapes (sub-target quiet books, no measured-toxic history, ≤4d to
  close, $145–200/day pools): KXOPENSHARE / KXTOPUSAGEAI / KXDEEPSHARE weekly
  share-trackers — NOTE mechanism kinship with KXMLABELSHARE (D3 toxic −11.3 c/ct,
  data-release class): min-size probe fills there are themselves informative tape.
  Final selection happens at `plan` time on fresh books.

## What gates R1
R0a: no kill. R0b: no kill; addressable clears threshold at target size. R1 (floor
probe, ~$5–20, 48h) is GO-gated: standalone script built + reviewed this session
(`kalshi_live/r1_floor_probe.py`); FIRST live order is relight-class — operator
one-word GO required in-session before `place`.
