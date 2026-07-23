# KALSHI DOCTRINE → POLYMARKET MAKER — FULL PORT + PLATFORM VERIFICATION

Operator directive 2026-07-22: *"mirror the kalshi plan in full now … verify plan
works on the platform as well."* This document is that mirror. Every element of
the Kalshi lane's hardened plan is enumerated and given a verdict against OUR
platform and OUR engine, with the evidence for each.

**"In full" cannot mean literal copy, and the reason is structural.** Kalshi's
plan is organised around *selling to unwind*: their bot must exit inventory, a
maker exit means resting an opposite order, and when that fails they cross the
spread as a taker. Two taker fire-sales are what cost them real money. Our lane
has a **hard NO-TAKER rule** (operator directive, `6f9352f` removed the last
taker code). So the port keeps their **doctrine** — which is venue-independent
and correct — and discards their **taker machinery**, which is a Kalshi-shaped
answer to a problem Polymarket does not pose in the same form.

---

## §0 THE PLATFORM DIFFERENCE THAT DRIVES EVERYTHING

On Polymarket, our quote structure is **BUY YES + BUY NO** — both legs are BUYs
funded from pUSD (you cannot post a YES ask without holding YES;
`maker_live_engine.py` module docstring). Consequences, verified:

1. **Delta reduction is a post-only BUY.** To reduce a long-YES position you buy
   NO. That is an ordinary maker order — no spread crossed, no taker, no fee.
   Kalshi's hardest problem (unwinding costs spread + fee, and the last resort is
   a fire-sale) **is structurally absent here.**
2. **The hedge converts risk into a $1 certainty.** `min(y, n)` pairs merge to
   exactly $1 (`merge_pairs`, `maker_live_engine.py:1158-1169`). Buying the
   complement does not just shrink net exposure — it turns directional risk into
   a redeemable pair.
3. **Resolution is the native exit.** We hold to resolution by design; there is
   no equivalent of Kalshi's "must be flat before settlement or eat the taker."
4. **Therefore: our correct posture is hold-to-resolution with net exposure
   capped, and the ONLY unwind primitive we need is a maker buy of the
   complement.** Kalshi's §2.5 (taker on settlement), §2.7 (STOP taker
   escalation), fix G (taker IOC guard) are **N/A by platform** — importing them
   would violate the no-taker rule and solve a problem we don't have.

---

## §1 THEIR BINDING TRADING MODEL (operator-taught, hard-won)

| # | Kalshi principle | Polymarket verdict | Evidence |
|---|---|---|---|
| 1 | Rewards pay for QUOTES RESTING, not inventory held | **HOLDS — identical.** Confirmed independently on our venue | official-docs audit, `MAKER_MASTER_PLAN.md` §7c; 5B handoff §5 |
| 2 | POSITION and QUOTES are two things, managed separately | **HOLDS.** Our engine separates inventory (`y`/`n` tokens) from standing quotes (`ob`/`oa`) | engine state model |
| 3 | Flat = zero net delta, not zero footprint; offset NEVER larger than the overhang | **HOLDS as doctrine; NOT separately enforced.** Net exposure = `y − n` and is capped at `3×msz`; there is no active offset primitive to overshoot with (see §3) | `:652` |
| 4 | Maker-first always; taker = genuine last resort | **STRENGTHENED to maker-ONLY.** We have no taker path at all | `6f9352f` |
| 5 | Expect 1–2¢ bleed per pair; income = rewards − bleed; don't panic on it | **HOLDS, same economics.** Our equivalent is the labelled `tradeDrag`, kept out of headlines by rule | `MAKER_NUMBERS_LEDGER.md` |
| 6 | NEVER taker fire-sale on flatten/STOP | **CANNOT OCCUR.** No taker path exists to fire-sale with | structural |

## §2 THEIR BOT CYCLE

| # | Kalshi behavior | Verdict | Evidence |
|---|---|---|---|
| 1 | Read state fail-closed; after 2 blind cycles cancel last-known ids | **HAVE, STRONGER.** Stale books deny new orders; 10 consecutive feed failures trigger a full kill (cancel-all + halt); 5 unmatched own-address fills trigger a schema-drift kill | `:633-635`, `:2019-2028` |
| 2 | Quote both sides at reference; both live below HARD | **HAVE** — two-sided quoting is the engine's core | quote construction |
| 3 | Skew control: per-ticker + per-EVENT aggregate; throttle accumulating side, grow reducing side | **PARTIAL — see §4 GAP-1.** We cap net per market (`3×msz`) and carry a per-EVENT one-winner floor, but we do not *actively shape* quotes toward flat | `:652`, `_event_cap_ok` |
| 4 | Settlement ramp; full pull at wind-down; **held position keeps its reducing-side quote** | **PARTIAL — see §4 GAP-2.** We have the ramp and the pull (`ramp_h`, `last_hours`) but the pull is total: a net position is abandoned into resolution with no reducing quote left | `gate()` `:586-592` |
| 5 | Taker fires on material position settling within 30min | **N/A by platform** (§0.3–0.4) | — |
| 6 | Capital: accumulating creates stop at the cap; **`unwind` creates NEVER blocked**; held value must be REAL | **GAP — CONFIRMED REAL, FIX ATTEMPTED AND REVERTED.** Net cap is delta-aware; the two GROSS caps are not. Held value was never faked here. See §3 — this is the session's main finding and its main failure | `:652`, `:656`, `:668` |
| 7 | STOP = maker-first with bounded escalation to taker | **N/A by platform.** Our kill is cancel-all-then-halt; positions ride to resolution, which is the intended exit | `kill_sequence` |

## §3 THE CAPITAL DEADLOCK — **FIXED** (a660aa1 -> 87b7c30 -> 44998b1)

**STATUS 2026-07-22: closed.** Three commits, three adversarial review rounds,
13 mutants. The history below is kept verbatim because the two failed attempts
are the reusable part; the working fix is summarised in 3e.

### 3a. The defect (stands — verified in code, independent of the failed fix)

`merge_pairs` nets `spent` down *after* a fill (`:1158-1169`), but the gross-cap
check runs *before* the order (`:656-658`). **A flat gross cap therefore denies
the very order whose fill would relieve it.** That is Kalshi's stuck-bot in our
shape — and worse than theirs, because both our legs cost money, so hitting
`market_gross_cap` stops *both* legs: quoting stops and the unhedged net rides to
resolution with no way to hedge it.

### 3b. The attempted fix and why it was REVERTED (independent adversarial review, DO-NOT-SHIP)

Attempt: exempt any order reducing `|net exposure|` from `market_gross_cap` and
`sector_gross_cap`. Written, tested 130/130 — and **wrong on both counts**:

1. **INEFFECTIVE.** The sole caller (`:1900-1919`) checks legs in fixed order
   (`yes` then `no`) and is **two-sided-or-nothing**. In the long-YES deadlock the
   accumulating YES leg is checked FIRST and denied, so `approved` never reaches
   2 and the exempt NO leg is discarded. The deadlock is **not relieved at all**
   for long-YES, and for long-NO only when the random size jitter happens to draw
   `sz_b > sz_a`. With `MAKER_SIZE_JITTER=0` (a supported config) it is a strict
   no-op. A money-path guard whose verdict depends on an RNG draw is also not
   defensible or reproducible.
2. **UNSAFE.** `reducing` was computed once per CALL, so on leg 2 a single
   "reducing" verdict skipped the gross checks for the **sibling accumulating leg
   too** — and the legs then rest and fill INDEPENDENTLY. Reviewer traced
   `spent` to **1.65× `market_gross_cap`** with the gross caps never consulted,
   plus a sector-cap breach by the same mechanism. My comment claiming it was
   "bounded by construction" was false.
3. **THE TESTS DID NOT DISCRIMINATE.** Mutation test (`reducing = True`, i.e.
   fully broken) still passed 4 of my 6 new tests — including the headline
   deadlock regression. The safety they appeared to demonstrate was actually
   being provided by `market_net_cap`, not by the new code.

**Reverted in full.** Suite back to the committed 126.

### 3c. The platform fact that constrains ANY future fix

The caller's own comment states it: **a single-legged quote scores ZERO rewards**
(two-sided MIN scoring, confirmed in the official-docs audit,
`MAKER_MASTER_PLAN.md` §7c). So placing only the reducing leg would **not**
restore the income the deadlock kills — that leg earns nothing. A lone hedge leg
buys risk reduction ONLY. This means the deadlock's two harms must be separated:

- **"Rewards stop"** — NOT fixable by a one-sided reducing quote. Only a
  genuinely two-sided quote earns, so relief requires cap HEADROOM (i.e. the
  GAP-4 sizing question), not a delta exemption.
- **"Cannot hedge"** — fixable in principle by a one-sided reducing placement,
  but that requires deliberately breaking the two-sided invariant for a
  risk-only purpose. That is an operator design decision, not a bug fix.

### 3e. THE FIX THAT WORKED

1. **Merge-aware capital accounting** (`a660aa1`). The cap was MERGE-blind, not
   delta-blind. `eff_cost = cost - min(sz, held_on_opposite_side)`. No
   exemption, no boolean, no sign rule — and pairs against a PENDING sibling
   are deliberately not counted, because legs fill independently, so ordinary
   two-sided quoting stays capped exactly as before.
2. **One-sided de-risk placement** (`87b7c30`, hardened `44998b1`). Necessary
   because the caller is two-sided-or-nothing. Scores ZERO rewards, so it buys
   risk reduction ONLY. Held by `onesided_hold()` on price stability and
   released by a `q_inv` inventory snapshot on ANY fill.
3. **`match_fills_paper` un-blinded** so an ask-only quote can fill at all,
   with one-sided rows bounded to flat (two-sided family semantics untouched).

### 3d. Minimum bar for any retry (from the review, recorded as requirements)

1. Classify reducing **per leg against that leg's own delta contribution**, never
   once per pair — the legs fill independently.
2. Treat the caller's fixed leg ordering and two-sided-or-nothing rule as part of
   the design, not context.
3. Every new test must be shown to FAIL against both `reducing = True` and
   `reducing = False` (mutation-tested), or it pins nothing.
4. No RNG-dependent guard verdicts.
5. Decide 3c explicitly: is the goal rewards (needs cap sizing) or risk (needs a
   one-sided exception)? They are different fixes.

## §4 GAPS FOUND — PROPOSE-ONLY, NOT IMPLEMENTED

**GAP-1 — no active delta shaping.** Kalshi throttles the accumulating side and
grows the reducing side as inventory builds. We only *cap* net; between zero and
the cap the quote is unshaped. Their measured failure (one-sided flow quietly
accumulating a directional position) applies to us on any one-sided market.
*Why not done:* this changes quoting behavior in the money path and interacts
with the gate-policy lock, which is calendar-gated on ≥3 clean post-cliff days.
Needs its own review cycle and a paired lab arm, not a same-session edit.

**GAP-2 — wind-down abandons a net position into resolution.** Their fix F keeps
the reducing side resting; our gate pulls everything. A resting complement bid
during wind-down would be free optionality (it converts risk to a $1 pair and is
still a rewarded maker order). *Why not done:* the wind-down gate exists because
near-settlement fills are adverse-selected (36–44% adverse in the final 16h), and
whether the reducing side escapes that is an empirical question the v5 lab should
answer, not an assumption.

**GAP-3 — their "unknown"-sector settlement hole is OUR hole too.** Already on
our parked list (in-play gate keys on `sector in ('sports','esports')`;
mis-labelled props in "unknown" quote through settlement). This session's
`MAKER_SECTOR_ALLOWLIST` **de-fangs it for the pilot** (a non-empty allowlist
fail-closes "unknown" out entirely) but does not fix it for full-universe runs.

**GAP-4 — cap sizing** (parked, unchanged): flat `$150` gross leaves 30/140
markets structurally unquotable. Distinct from §3 — that fixed the cap's
*delta-blindness*, not its *sizing*. Sizing needs an operator capital decision.

## §5 WHERE WE WERE ALREADY AHEAD

Recorded so the port isn't read as one-directional:
- **No taker anywhere** — their two most expensive lessons are unreachable here.
- **Blind-feed response is stronger** (kill + halt vs cancel-ids; §2.1).
- **Scoped kill primitive** — our cancel can never touch a co-tenant's orders
  (`0063c61`, tested). They run a dedicated account instead.
- **Held value was never faked.** Their fix C corrected `|pos|×$1`; our ledger has
  always tracked real spend with merge netting.
- **Decoding traps pre-solved:** their `position_fp` / `side`-vs-`action` class of
  bug is what our scoring-stage read-back probes are designed to surface.

## §6 STANDING

**No code changed in this port.** The one code change attempted (§3b) was
reverted on an independent DO-NOT-SHIP verdict; the engine is byte-identical to
the committed build, suite 126/126. The VPS still runs the older `3531d83`
engine in paper, halted. Everything in §4 is propose-only.

## §7 THE PROCESS LESSON (worth more than the port)

The guard change had tests, a written rationale, a self-caught flaw, and 130/130
green — and was still **both ineffective and unsafe**. What caught it was not
more testing but a reviewer who read the **caller**. Two transferable rules:

1. **A guard cannot be reviewed in isolation from its call site.** The exemption
   was designed against a single-leg call shape production never makes. Every
   finding in §3b flows from that one omission — mine, not the reviewer's.
2. **Mutation-test any test that claims to pin a safety property.** Four of six
   of my tests passed against a fully-broken implementation; they were being
   satisfied by a *different* guard. Green tests measured nothing. This is now a
   standing requirement (§3d.3).

Kalshi's arc says every round finds a bug, including bugs introduced by the
previous round's fixes. This round the bug was introduced by *this* round's fix,
and it was caught before it reached money. That is the process working.
