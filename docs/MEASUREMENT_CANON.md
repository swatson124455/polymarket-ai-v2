# MB MEASUREMENT CANON — DRAFT (for operator ratification)

**Status:** DRAFT v1.0, 2026-08-25. Becomes canon only on operator signature
(§5). Implemented by `scripts/mb_canon.py` (definitions) and
`scripts/canon_verify.py` (daily blind verification). This document is the
methodology; the code is its executable form. Where they disagree, the signed
document governs and the code is a defect.

**Why this exists (verified 2026-08-25, `docs/mb_overhaul_review_findings.json`,
areas pipeline/economics):** three scripts compute three different "edge"
estimands under one name (analyze_shadow per-fill at analyze_shadow.py:236-239;
band_tracker per-market at band_tracker.py:75-78; cohort5 per-fill via
cohort5_qualification.py:137); fee models diverge (flat 2% at
cohort5_qualification.py:129-130 vs venue formula at analyze_shadow.py:176-187);
the DB-wins label merge has no conflict detection (shadow_readout.py:230-236);
and the shadow fill is a size-blind top-of-book ask quoted an unrecorded delay
after detection (copy_watcher.py:241-265, :389-405, :624-680). One name, one
estimand, verified daily against the chain — that is the operator's order.

---

## 1. DEFINITIONS

### 1.1 The canonical edge (the only number allowed to be called "edge")

The canonical estimand is the operator-ratified frozen estimand of
`docs/BAND_PREREGISTRATION.md` ("Estimand (canonical, frozen)", lines 17-23):
**the per-market mean edge of OK first-buys**, computed as:

1. **Edge atom** (one OK first-buy fill on one token):
   `edge = outcome − fill − fee(token, fill)`
   where `outcome ∈ {0, 1}` from the canonical label source (§1.4),
   `fill` = the shadow fill (§1.2), `fee` = the canonical fee function (§1.3).
   Implemented at analyze_shadow.py:235 and band_tracker.py:75; canonized in
   `mb_canon.edge_atom`.
2. **Per-market mean**: atoms grouped by `token_id`, mean within each token.
   "Market" in the frozen estimand is implemented as the **token cluster**
   (band_tracker.py:64-78 keys `per_tok` by `token_id`; the bootstrap cluster
   unit is likewise the token, analyze_shadow.py:148-160). This is a
   disclosed implementation fact: the two legs of one condition_id would
   count as two markets if both were first-bought. Canon keeps token_id as
   the unit — changing it is a §5 amendment.
3. **Pooled statistic**: the mean of per-market means — **each market weighs
   equally** (band_tracker.py:106-108). A heavily-copied token contributes
   exactly one observation.
4. **Ordering** (for sequential tests): markets ordered by the market's first
   `detect_ts` (band_tracker.py:76-78; pre-registered at
   BAND_PREREGISTRATION.md:26-28).

**Population qualifiers** (all four required; `mb_canon.per_market_edges`):
- `first_buy == true` as recorded per (trader, token);
- verdict `OK` **after ladder repair** — every ladder-armed record is
  re-derived from its /book ladders before analysis (real ask = lowest ask
  level; gates re-evaluated), analyze_shadow.py:24-33, :77-110; ladderless
  records are EXCLUDED unless written after a trusted watcher epoch
  (analyze_shadow.py:107-110);
- forward window by **explicit** `detect_ts >= epoch` filter
  (band_tracker.py:55; cohort5_qualification.py:85-87). `trust_after` is NOT
  a time filter — it keeps every ladder-armed record regardless of detect_ts
  (MB_STATE.md §7, lines 1700-1704). Any instrument that windows on
  trust_after alone is defective;
- resolved outcome present in the canonical label map (§1.4). Unresolved
  tokens are **counted, never guessed** (analyze_shadow.py:21-22).

### 1.2 What "fill" means today, and its known biases (disclosed, not hidden)

`shadow_fill` = **the top-of-book best ask** at quote time, full copy size
assumed to execute there. Source: `evaluate_gates` returns best_ask, taking no
size input (copy_watcher.py:241-265); the repair path takes the min over
ladder ask **prices** only — ladder SIZE fields are recorded but never read
(analyze_shadow.py:88-89). Known biases, which every consumer of the canonical
edge inherits:

- **B1 — size-blind.** No depth model anywhere in the verdict pipeline
  (review finding, area pipeline: "Shadow fill is top-of-book ask with depth
  ignored"). The canonical edge is an **edge-at-touch**; it certifies nothing
  about edge at size. Any depth-weighted variant must be named
  "edge-at-size" (§2).
- **B2 — unrecorded quote delay.** `detect_ts` is stamped before the receipt
  fetch (copy_watcher.py:624); the ask is quoted after receipt/block/median
  RPC work (:672), each call under a 90s timeout (:302, :305-317), and the
  record has **no quote_ts field** (:389-405). The staleness of the quote
  relative to detection is unmeasurable from the record.
- **B3 — zero queue/latency assumption** between quoting and our hypothetical
  order landing.
- **B4 — NO_BOOK conflation.** `quote_book` swallows every exception and
  returns None (copy_watcher.py:472-484), mapped to NO_BOOK (:257-258) — a
  CLOB transport failure is indistinguishable from a genuinely unquotable
  market, with no rate alarm; outage-correlated exclusions can select the OK
  population toward calm markets.
- **B5 — one-sided-book spread bypass.** The spread gate self-disables when
  best_bid is None (copy_watcher.py:261).

These biases are properties of the recorded data and are NOT correctable by
this canon; they are disclosed here so no verdict overstates what the
canonical edge proves. Fixing B2/B4 (add quote_ts; distinguish transport
failure) is watcher work, out of canon scope.

### 1.3 The canonical fee function

`fee(token, p)` per share, precedence chain (analyze_shadow.py:176-187,
implementation :225-234; canonized in `mb_canon.canon_fee`):

1. **`fee_rate_map[token]` present → venue formula: `rate · p · (1 − p)`.**
   Polymarket's published formula, validated against **3,070 live charged
   fees 2026-08-19** (crypto implied rate p50 0.0700 vs official 0.07,
   sports 0.0500 vs 0.05 — analyze_shadow.py:179-181).
2. **`fee_map[token] == 0` → fee = 0.** Measured zero-fee exemption only
   (2026-07-30, operator-approved; analyze_shadow.py:167-175, :230-232).
3. **Otherwise → flat 2% of notional: `0.02 · p`** (analyze_shadow.py:234).
   This is a FALLBACK and must be **disclosed in every output that used it**
   (count of fallback-priced fills, as analyze_shadow already reports via
   `fee_rate_priced_fills` / `fee_exempt_fills`, :248-251). The flat model
   OVERCHARGES high-priced fills (at p=0.9: 0.018 flat vs true 0.0063) and
   undercharges mid prices (analyze_shadow.py:183-186).

**Artifact integrity rules** (already law in shadow_readout, now law
everywhere): a **corrupt or empty** fee map or fee-rate map is FATAL — refuse
to read out rather than silently change the equation (shadow_readout.py:507-537);
a **missing** map file falls back to the prior behavior but the fee_note
disclosure line must say so (shadow_readout.py:512, :535-537). The
band_tracker pattern — bare `os.path.exists` fallback to `{}` with zero
disclosure (band_tracker.py:89-92) and the flat rate hardcoded inline
(band_tracker.py:74) — is non-canonical and is retired for new code.

### 1.4 The canonical label source and merge

Two independent sources, merged per run:

- **DB**: `fresh_outcomes` rebuilt from the `markets` table **fresh every
  run** — never a cached snapshot (shadow_readout.py module docstring :4-13;
  the 2026-07-15 stale-cache landmine).
- **Supplement**: the CLOB-verified `gamma_resolutions.json` cache,
  restricted to the shadow token set, both legs labelled
  (shadow_readout.py:196-227). CLOB is the trustworthy resolution source —
  resolution derived from token prices reflecting UMA settlement, verified
  196/196 with 0 mismatches (shadow_readout.py:25-26; MB_STATE.md:1717-1722).
  The DB alone is structurally PARTIAL for shadow markets: the resolution
  backfill only queues markets the bot traded, and the missing slice was
  measured systematically NEGATIVE — DB-only labels flattered every edge the
  lane ever reported (shadow_readout.py:15-26; MB_STATE.md:1705-1708).

**Merge precedence WITH conflict handling** (canon 2026-08-25,
`mb_canon` label merge; supersedes the silent `merged.update(db)` of
shadow_readout.py:230-236 for all new code):

1. Token in DB only → DB label.
2. Token in supplement only → supplement label.
3. Token in BOTH, labels agree → the agreed label.
4. Token in BOTH, labels **disagree → CONFLICT**: the token is **EXCLUDED
   from the merged map** (neither label used), counted, and reported loudly
   in every consuming output. Conflicts are never auto-resolved — they are
   left for a human, matching the CONFLICT semantics the supplement writer
   already has (shadow_label_supplement.py:86-98). A wrong `markets`
   resolution row must never silently override a CLOB-verified label, and
   vice versa.

**Guard set (mandatory for every consumer, not just shadow_readout):**
- **Zero-row supplement refusal**: if the supplement labels 0 shadow tokens,
  REFUSE the readout — a zero-row result is never evidence of agreement
  (shadow_readout.py:485-504; empty-set false-pass landmine
  MB_STATE.md:1685-1692). band_tracker.py:87 and
  cohort5_qualification.py:124 currently bypass this guard; that bypass is
  non-canonical.
- **Provenance line** in every output: DB / supplement / conflicted /
  unlabelled counts (extends shadow_readout.py:239-247 with the conflict
  count).

### 1.5 One implementation

`scripts/mb_canon.py` is the single home of: the fee function, the edge atom,
the per-market aggregation with canonical filters, and the conflict-detecting
label merge. The by-hand duplication era — repair logic "mirrors
evaluate_gates exactly" as prose (analyze_shadow.py:83-85), constants pasted
positionally (band_tracker.py:84) — produced a **realized** divergence: the
watcher's 2026-08-19 max_fill=0.98 gate is absent from `repair_record`
(analyze_shadow.py:95-102 has no PRICE_NO_UPSIDE branch), so re-derived
verdicts already differ from live gates. New logic goes into the canon
library or it does not ship.

---

## 2. NAMING LAW

**Bare "edge" asserts the canonical estimand of §1.1 — per-market mean, OK
first-buys, canonical fill, canonical fee, conflict-checked labels.** Any
number computed differently MUST carry a distinct name in every output,
docstring, log line, and message. No exceptions for "everyone knows what I
mean."

Registered non-canonical names (extend by amendment, §5):

| Name (mandatory) | What it is | Where it lives today |
|---|---|---|
| **fill-weighted edge** | per-fill pooled mean — a many-fill token dominates | analyze_shadow.py:236-239 |
| **per-trader fill-weighted edge (charter, flat fee)** | cohort5's per-trader grade via the per-fill path under flat 2% | cohort5_qualification.py:129-137 |
| **trader-price chain edge** | outcome minus the TRADER's own fill price — measures the whale, never our copy | chain_deep_dive.py:459-460 |
| **maker conditional edge** | bidsim conditional-on-fill edge | BIDSIM_DESIGN.md, MB_STATE.md:87-93 |
| **edge-at-touch** | explicit synonym for the canonical edge when contrasted with a depth model | §1.2 B1 |
| **edge-at-size** | any future depth-weighted (ladder-walk) fill edge | not yet built |

The same law applies to "fill": unqualified "fill" means the §1.2 top-of-book
shadow fill; a ladder-walk price must be named (e.g. "VWAP fill").

Presenting a non-canonical number under the bare name is a reporting defect
of the same class as an unsourced P&L figure (CLAUDE.md Forbidden Pattern 8 /
Protocol 11): strip it or name it before sending.

---

## 3. VERIFICATION PROTOCOL (daily, blind, chain-grounded)

Ground truth ranking: **Polygon tx receipts > CLOB > our own records.** The
verifier (`scripts/canon_verify.py`) runs daily and re-derives a random
sample of our recorded data from sources we do not control.

### 3.1 Date-seeded sampling — samples cannot be cherry-picked

The RNG seed is **the UTC date** (YYYYMMDD), fixed before any value is seen
(canon_verify.py:6-8). Consequences, by design:
- neither the agent nor the code can steer which records get audited;
- a rerun on the same day reproduces the same sample — any third party can
  re-derive it and check the checker;
- sample sizes are fixed parameters (defaults: 8 records, 8 labels, 6 fee
  entries — canon_verify.py:25-26), not chosen after seeing results.

### 3.2 The three checks

1. **RECORD CHECK** — sampled tx-bearing shadow records are re-derived from
   the Polygon transaction receipt (`eth_getTransactionReceipt`): decode the
   OrderFilled events (FILL_TOPIC_V2; price layout validated to 4 decimals,
   copy_watcher.py:20-21) and the receipt transfer-log side rule, and
   confirm the chain agrees the recorded **trader BOUGHT the recorded token
   at the recorded price** (tolerance 5e-4, canon_verify.py:45). This is the
   same kit that exposed the bidsim self-fill artifact and the RTDS side-field
   unreliability on 2026-08-24 (MB_STATE.md:21-38).
2. **LABEL CHECK** — sampled resolved labels from the supplemented cache are
   re-fetched from CLOB `/markets/{condition_id}` and the winner re-derived
   from token prices — the production-proven path (resolution_backfill;
   MB_STATE.md:1717-1722). Never the gamma batch filter, which is a silent
   no-op (MB_STATE.md:1765-1769).
3. **FEE CHECK** — sampled fee-map entries re-fetched from CLOB
   `taker_base_fee` and compared to the stored artifact.

**Blindness rule:** derive first, compare second. The verifier computes the
chain/CLOB value before consulting the recorded one; it never "checks whether
the record can be explained."

### 3.3 Mismatch classes — every one is a loud alarm

ANY mismatch prints a `[canon] ALARM` line (canon_verify.py:21). Silence is
forbidden in both directions: a source that cannot be sampled prints
UNAVAILABLE — never a pass (canon_verify.py:21-22; empty-set false-pass
landmine, MB_STATE.md:1685-1692). Meanings and required responses:

| Class | Meaning | Required response |
|---|---|---|
| **RECORD mismatch** (price/side/trader/token differs from receipt) | The watcher's decode or the sink is corrupt — the forward evidence base itself is suspect | STOP consuming the sink for verdicts; root-cause before any lock is minted; bound the affected time range |
| **LABEL mismatch** (CLOB disagrees with the merged label) | Label-pipeline corruption (bad `markets` row or stale/wrong supplement) — the lane's twice-proven weak point (shadow_readout.py:15-33) | Treat as a CONFLICT (§1.4): exclude, recompute all open statistics; a verdict locked on a since-refuted label gets an operator-flagged annotation (the lock itself stays immutable, §5) |
| **FEE mismatch** (venue rate differs from artifact) | The venue changed its schedule, or the artifact is stale — a silent edge shift for every fill priced off it | Rebuild the map with a fetched-at stamp; disclose the change date; re-price forward records only (never retro-reprice a locked verdict) |
| **UNAVAILABLE** (receipt/CLOB unreachable, or zero sampleable rows) | An evidence gap, not agreement | Counted and reported; persistent UNAVAILABLE across days is itself an alarm |

A day with zero verified samples is reported as such, loudly. "No alarm
because nothing ran" is the failure mode this protocol exists to kill.

---

## 4. CONSUMPTION RULE

1. **Every future instrument imports `mb_canon`** for edge atoms, fee,
   label merge, and population filters. Re-implementation is a defect — the
   guard-asymmetry finding proved why: shadow_readout's refusal and
   disclosure guards existed, and the two NEWEST verdict scripts reproduced
   the exact failure the guards prevent because each script re-decided which
   guards to copy (band_tracker.py:87-92; cohort5_qualification.py:124-130).
2. **Charter-locked instruments keep their charter.** A pre-registered test
   scores under the rules it registered with, forever (§5.3). In particular,
   cohort5's original 20 chain-ADMIT arms were deliberately registered on the
   flat 2% fee (COHORT5_PREREGISTRATION.md froze the flat-fee equation for
   cohorts registered 2026-07-30; a calibrated rate "would apply only to
   cohorts registered after it"), and locked verdicts computed under the flat
   model stay locked (analyze_shadow.py:185-187).
3. **…but they must DISCLOSE divergence in every output.** Each run of a
   charter-locked instrument prints a standing line naming (a) which
   canonical rule it diverges from, (b) the charter that authorizes it, e.g.:
   `NON-CANONICAL (charter 2026-07-30): fee = flat 2%; canonical venue
   formula would price these fills differently (flat overcharges high-p
   fills)`. An undisclosed divergent number violates §2.
4. **Open question flagged, not settled here:** the C1_UNTESTED group
   (epoch 2026-08-24T17:00Z, cohort5_qualification.py:39-57) was registered
   AFTER the venue-rate map existed yet grades under flat 2%
   (cohort5_qualification.py:129-130) — analyze_shadow.py:186-187 says the
   venue formula "governs … post-2026-08-19 registrations." Whether its
   9 not-yet-consumed looks move to the canonical fee before first lock is
   an **operator decision**; the §4.3 disclosure obligation applies to it
   immediately either way.
5. **Locked verdicts are never recomputed.** Canonical-basis numbers printed
   beside a locked verdict are labelled DIAGNOSTIC (the shadow_readout
   pattern, shadow_readout.py:442-448).

---

## 5. CHANGE CONTROL

1. **Amendment-only.** The canon changes ONLY by operator-signed amendment,
   appended to this document with: an **epoch stamp** (UTC ISO timestamp),
   what changed, why, and the measurement that motivated it. `mb_canon.py`
   carries the matching `CANON_EPOCH` (mb_canon.py:33); code and document
   epochs must agree. No session may "improve" a canonical definition in
   passing — that is the three-estimands failure being re-created.
2. **Epoch semantics.** Registrations dated on/after an amendment's epoch use
   the amended canon. Records are never re-scored under rules younger than
   the test consuming them.
3. **Pre-registered running tests never retroactively change scoring.** A
   test registered under canon vN scores under vN until it locks or closes —
   exactly the band test's own cheating list: no re-widening after seeing
   forward data, no threshold lowering, no counting pre-epoch fills, no
   retry-after-futility without operator sign-off
   (BAND_PREREGISTRATION.md:41-44). An amendment can create a NEW
   registration; it cannot touch a running one.
4. **Locks are immutable.** `write_lock` refuses to overwrite an existing
   lock; changing one requires a manual operator edit
   (shadow_readout.py:427-439). Canon amendments annotate locks (§3.3
   LABEL row); they never rewrite them.
5. **No silent equation changes.** Any fee/label artifact rebuild carries a
   fetched-at stamp and a disclosure line at next readout; corrupt artifacts
   are FATAL, missing ones are disclosed fallbacks (§1.3, §1.4). The
   verifier (§3) is the standing detector that this rule held.

---

*Draft prepared 2026-08-25 against worktree `claude/repo-setup-docs-fq9bhn`
(C:/lockes-picks/mb-steward). All file:line citations verified in that
worktree on 2026-08-25, either by direct read this session or by the
adversarial per-finding citation verification recorded in
`docs/mb_overhaul_review_findings.json` (generated 2026-08-25T03:45Z).*


---
## POST-DRAFT RESOLUTION (2026-08-25, same day)
The C1_UNTESTED fee-model tension flagged in section 4 was RESOLVED before
any look was consumed: cohort5_qualification.py now grades the C1 group on
the venue formula via fee_rate_map (canon), per the post-2026-08-19
registration rule, and simultaneously moved the C1 group to an ANYTIME-VALID
e-process (docs/COHORT1_UNTESTED_AMENDMENT.md). The original 20 remain on
their 07-30 charter (flat 2%, single look) with a per-run divergence
disclosure line. Operator authorization: "proceed with your rec"
(2026-08-25 directive, item 6).
