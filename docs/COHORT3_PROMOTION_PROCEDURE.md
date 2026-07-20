# Cohort-3 Promotion Procedure (shadow roster 25 → 30)

**Status:** ARMED, pre-validated offline. **Gated on:** run-4 exit (batch
boundary) + operator go. Nothing here has been applied to live state.

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

The next **12:30Z** readout must show `16+8+6` plus a fresh `cohort3` line, or
it RAISES on ledger drift — fix before trusting any line. cohort3 starts at
0 resolved; it is a NEW collection epoch, never pooled with cohort1/cohort2.
