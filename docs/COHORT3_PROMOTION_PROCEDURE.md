# Batch-Boundary Ledger Mutation — Cohort-3 Promotion + Bum Time-Out (25 → 30)

**Status:** ARMED, pre-validated offline. **Gated on:** run-4 exit (batch
boundary) + operator go. Nothing here has been applied to live state.

**Operator directive 2026-07-20:** FOLD two changes into ONE ledger edit + one
clone refresh + one watcher restart at the run-4 batch boundary:
  1. **Bum time-out** — bench `0x44886115` (chain-verified negative drag: edge
     −0.1051, P(>0)=0.107 on 22 resolved, 40% of cohort-1's first-buys). It
     STAYS in `clean` (watcher keeps watching so recovery is measurable);
     moves `cohort1_original` 16→15 and gains a `benched` entry with
     `from_cohort:"cohort1"`. Reversible: re-admit when its forward-since-bench
     line clears edge ≥ +0.02 AND P(>0) ≥ 0.90 on ≥ 20 resolved (operator go).
  2. **Cohort-3 promotion** — the 6 run-4 ADMITs (below).

**Combined invariant:** clean(30) == cohort1_original(15) ∪ cohort2(8) ∪
cohort3(6) ∪ benched(1) ∪ probe(0) = 30. Validated offline through the REAL
`load_cohorts` (→ `cohort1(15), cohort2(8), cohort3(6), benched(1)`;
`reduced_cohorts` → `{cohort1}`) + 7 adversarial guard cases all fail loud.
Requires readout code ≥ `d423b78` (benched + reduced-cohort support) live in
the `/opt/pa2-shared/mb_readout` clone — refresh it in the SAME step, or the
old clone RAISES on the new ledger at 12:30Z.

Builder: steward scratch `build_combined.py` (stamp real ISO8601 at execution).

**Why this file exists:** the 2026-07-19 session left this procedure in its
session scratchpad, which does not survive session end (§7 landmine: "a frozen
session's unpushed work is GONE"). This is the durable copy.

---

## Preconditions (ALL must hold before touching anything)

1. **Run-4 EXITED.** `ps -p 3269649` reports dead, confirmed on two consecutive
   reads. A mid-run restart resets FirstBuyDedup for zero informational gain
   (§0 block 7) — the promotion is a batch-boundary action, not an urgent one.
2. **Operator go**, given after seeing the final run-4 tally.
3. **Re-check the ADMIT list** — traders 21–28 may add ADMITs; any new ones join
   this SAME single restart (never a second restart).

## The 6 ADMITs (full addresses, from the deep_dive JSONs 2026-07-17→19)

| Address | Note |
|---|---|
| `0xf705fa045201391d9632b7f3cde06a5e24453ca7` | probe — **graduates**, already in `clean` |
| `0x7c3db723f1d4d8cb9c550095203b686cb11e5c6b` | new |
| `0xe542afd3881c4c330ba0ebbb603bb470b2ba0a37` | new |
| `0x216509be5332c6037105b4f871966eb97240f598` | new |
| `0x2ee04b8b6ec338d7dc52e019dabdcaa456eaf57b` | new |
| `0xa6a856a8c8a7f14fd9be6ae11c367c7cbb755009` | new |

All chain-verified: complete sweep, 0 mismatch, 100% of API-BUYs chain-backed,
skill P ≥ 0.9975. Cross-checked JSON verdicts ≡ run-4 log tally (6 ADMIT).

## Edit to `/opt/pa2-shared/mb_copyable_data/chain_audit.json`

Owner `polymarket:polymarket`. Three changes:

1. `clean` += the **5 new** addrs (`0xf705fa` already present) → **25 → 30**
2. ADD `"cohort3": {"addresses": [all 6], "admitted_utc": "<restart ISO8601>"}`
3. EMPTY `"probe": {"addresses": [], ...}` (0xf705fa graduates)

**INVARIANT:** `clean == cohort1_original ∪ cohort2 ∪ cohort3` = 16+8+6 = **30**.

## Offline validation (MANDATORY before deploy)

Run the candidate through the REAL `shadow_readout.load_cohorts` — never a
copy-paste of its logic (a duplicated verdict statistic drifts; cf. `bdcfefb`).

```python
import sys, json; sys.path.insert(0, 'scripts')
import shadow_readout as sr
print(sr.load_cohorts(json.load(open('chain_audit.candidate.json'))))
```

**Expected:** `cohort1(16) epoch=1783985376, cohort2(8) epoch=1784143212,
cohort3(6) epoch=<restart>`. No `probe` group (empty probe is optional → not
emitted). Any raise = STOP and fix the ledger.

**Verified 2026-07-20 (offline, this candidate):** PASS, plus a live-file
control that reproduced the current `cohort1(16)/cohort2(8)/probe(1)` split.

### Guard tests (adversarial pass — all 6 confirmed to fail loud)

| Plausible mistake | Guard result |
|---|---|
| forgot to extend `clean` | raises `clean (25) != union (30)` |
| probe not emptied (addr in 2 groups) | raises `group OVERLAP` |
| `cohort3` present but empty | raises `NO addresses` (silent-pooling class) |
| duplicate addr inside `cohort3` | raises `DUPLICATE address` |
| unparsable `admitted_utc` | raises `unparsable admitted_utc` |
| untouched candidate (control) | OK → 16+8+6 |

## Deploy (operator go, ONE fenced restart)

```bash
cp chain_audit.json chain_audit.json.pre-cohort3-<date>   # backup FIRST
# apply the edit, re-run the offline validation on the REAL file
deploy/mirror3_shadow_deploy.sh
```

Then verify: **roster=30** in the journal, first canary > 0, **0 CANARY ALARM /
QUOTE SANITY** lines. Record the cohort3 epoch in `docs/MB_STATE.md` §0.

## Post-deploy

With the bench folded in (operator 2026-07-20), the next **12:30Z** readout must
show header `15+8+6+1probe→0+1benched` and:
- `[cohort1(15) REDUCED] … :: NO VERDICT (roster REDUCED by a time-out …)`
- a fresh `[cohort3(6)]` line (0 resolved — new epoch, never pooled)
- `[benched(1) TIME-OUT since <date>] … :: NO VERDICT`

or it RAISES on ledger drift — fix before trusting any line. Requires readout
code ≥ `d423b78` live in the clone.

---

## FOLLOW-ON QUEUE — cohort-1 active-trader vetting (operator "proceed" 2026-07-20)

**Why:** cohort-1's 16 were admitted on the deprecated V1 `audit_roster_chain.py`
"clean" verdict — **0/16 ever had a chain deep-dive** (verified 2026-07-20).
Cohort-2 (8) and cohort-3 (6) are deep-dive-vetted. This pass brings cohort-1's
**7 ACTIVE** traders to the run-4 fair-params standard so the whole live roster
is vetted on one method. The 9 dormant cohort-1 addresses are SKIPPED (they do
not trade — deep-diving them is wasted RPC).

**The 7 targets** (cohort-1 addresses with shadow records; regenerable via the
steward `cohort1_active.py`):
```
0x448861155279dbf833d041b963e3ac854599e319   # the benched bum — dive gives a
                                             # chain-native re-admission signal
0x84dbb7103982e3617704a2ed7d5b39691952aeeb
0xab19716584931d81cd9e7763402673a64baa4876
0xc6587b11a2209e46dfe3928b31c5514a8e33b784   # BORDERLINE low activity (3 shadow
                                             # recs, 21 API trades/168h) — may
                                             # return INSUFFICIENT/un-gradeable
0xecb14ac6e9ca447ce2f2912e6217b43d7b655da3
0xee00ba338c59557141789b127927a55f5cc5cea1
0xf2f6af4f27ec2dcf4072095ab804016e14cd5817
```

**Sequencing (peer rule — no jumping run-4 on the shared RPC):** runs AFTER
run-4 exits, serial with the deepen wave. Cheapest as ONE multi-sweep with the
deepen-wave addresses via `chain_fill_cache.populate_multi` once the fill-cache
proof gate passes. Command mirrors run-4 (bare-address roster file,
`--extra-traders`, fresh gamma cache, `--max-receipts 30000`, detached launch,
own fresh log name).

**Outcome (diagnostic, NOT a roster change):** tells us which of the 7 are
chain-copyable (skill-verified) vs noise — explains *why* cohort-1 failed. Any
strong ADMIT becomes a candidate for a FRESH pre-registered cohort (operator
go); this pass does NOT rescue cohort-1's locked NOT-DEMONSTRATED verdict and
does NOT auto-change the roster. INSUFFICIENTs join the deepen backlog.
