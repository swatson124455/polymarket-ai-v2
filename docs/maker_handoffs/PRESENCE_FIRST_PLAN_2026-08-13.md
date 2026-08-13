# PRESENCE-FIRST PLAN v1.1 — with adversarial threat register (2026-08-13)
Mandate: operator 08-13 — money deep dive; $3k contingent, unlimited if measured; maker/LIP
ONLY (no directional, no cross-venue); naming delegated; decision date compressed to ~08-21/22.
Evidence base: D1/D2/D3 frozen studies (`ef517d8`) + D4 tape (live since 08-13T01:15Z).

## Core design (v1.0, restated)
Money-map daily job (full program universe + books) → EARN/AVOID/UNKNOWN buckets; quoter may
act ONLY on EARN; new markets auto-enroll in observation (≥24h tape, no exceptions); entry at
minimum qualifying size; HOLD iff trailing accrual ≥ 2× trailing fill cost; EXIT on eviction /
flow signal / catalyst horizon / stale map (fail-closed). Deterministic rules, no runtime
learning; rules change only via reviewed deploys.

## THREAT REGISTER (devil's advocate pass, operator-ordered) — each with the plan change it forced

**T1 — "Band-edge presence earns ≈ nothing."** Reward = score-share; TOUCH proximity and
PAIREDNESS drive score (07-26 audit); deep quotes may score ~0 and the $1 floor is a terminal
crumb. → PLAN CHANGE: in empty/one-sided books we quote AT the touch (we ARE the best price —
top score by construction), paired where possible; in competitive books we don't play at all.
Canary day-1 is explicitly an ACCRUAL-YIELD measurement: ~30 markets at min size, read
$/day/market from the estimates feed within 24h; every projection replaced by that empirical
number before any scale-up. No yield → verdict data, cheaply.

**T2 — "Empty books are empty for a reason."** The lone quote in an informed market is a free
option for the one informed trader; and some 'empty' markets are dead/closed (D2 caveat:
KXEOWEEK-26AUG01 rows showed 0 levels BOTH sides). → PLAN CHANGE: (a) market status verified
open+active at entry; (b) catalyst-horizon exclusion (close_time and event schedule) applies to
empties too; (c) min size caps the option value given away — worst case per market is cents to
low dollars; (d) 24h of D4 tape must show genuinely absent flow, not a snapshot.

**T3 — Capital lockup in long-dated empties.** KXMAMDANIOUT pool $250/d but runs to 27JAN01 —
min-size capital could sit ~5 months; eviction before settlement pays taker spread. → PLAN
CHANGE: prefer near-dated (capital velocity); per-market lockup cap = close_time ≤ N days
(operator question Q-D, default 30) unless a passive maker exit is realistically available;
exits are SELL and PASSIVE-FIRST — taker exit only on risk triggers.

**T4 — Competition/venue reaction.** Copycats join our empties; pools reprice (universe moved
3,591→3,735 in ~13h, both reads cursor-exhausted); LIP terms/DF/targets can change; payment
regime can shift. → Already structural: daily full-universe reprice, per-market eviction, no
dependence on any single market or the program list staying still. Venue-terms change =
detected by accrual collapse across the book → mass-eviction is the correct automatic response.

**T5 — Estimates feed dies or lies (our revenue eye is an undocumented API).** Eviction logic
keys on the feed; if it breaks we fly blind; if it drifts optimistic we hold losers. →
Fidelity is continuously scored against PAID credits (reward_pnl ratios + gate-eval
sensor-fidelity, first firing 08-18); feed unreachable/stale → FREEZE new entries, keep book,
paid-basis only (fail-closed, same pattern as stale map). Feed-vs-paid ratio < 0.5 on any
event → distrust flag, eviction threshold doubles until re-validated.

**T6 — The 2× eviction threshold is a guess.** Too strict → churn costs (re-entry spread);
too loose → slow bleed. → PLAN CHANGE: threshold PRE-REGISTERED before the proof but
CALIBRATED from canary day-1's observed accrual and fill-cost distributions (not from theory);
eviction executes passively (rest the exit) except on risk triggers, so a wrong threshold
costs churn, not spread-crossing.

**T7 — New trading code is itself the historic #1 loss source.** The defect eras dwarf
structural cost; a one-day rebuild is how incidents are born. → PLAN CHANGE (binding):
(a) mode-change on the EXISTING quoter chassis (governors, caps, dd halt, OBS_HOLD plumbing
stay), NOT a rewrite; (b) full ship discipline incl. the same 8-angle adversarial review that
found 10 real defects in my own 199-line script today; (c) SHADOW MODE first — the new
selector runs dry, emitting would-quote plans, verified against gates/map before any live
order; (d) canary at min size under the operator halt budget. HONESTY CLAUSE: if review or
shadow surfaces majors, the relight slips a day — flawless-by-rule beats fast-by-hope
(operator question Q-C).

**T8 — Cancel-on-signal is partly placebo.** A sweep executes in ms; D4 cadence is 60s; even
the ws feed can't cover 40 markets with guaranteed latency. → PLAN CHANGE (honesty): the
PRIMARY snap defense is SIZE + SELECTION (quiet markets, min size, no catalysts) — stated as
such; cancel-on-signal (ws-fed where available, D4 elsewhere) is best-effort hygiene and is
never load-bearing in the risk math. Worst-case math must close on size alone.

**T9 — Rate-limit collisions.** Map job (~37 min of reads at 0.6s for 3,735 books) + D4 +
recorders + live quoter share one API budget. → Map runs in a fixed window (05:00Z),
D4 list map-driven but hard-capped, quoter keeps its own spacing; 429 counts logged and
alarmed in canary; breadth target degrades gracefully (fewer markets, never faster reads).

**T10 — Gauge risk (we just fixed 10 findings in our own scoreboard).** Any new artifact can
lie the same way. → All new artifacts (map job, scorecard, mode telemetry) go through the
same review gauntlet BEFORE the proof scores anything; the proof itself is scored by the
already-fixed scoreboard; identity checks stay mandatory.

**T11 — Small-sample verdict distortion.** At min size a single credit or snap can flip a
7-day verdict; credits lag ~38-48h so early trailing-counted undercounts. → PRE-REGISTER:
pace gauge = accrued (fidelity-checked) for the rung-up rule; PASS/FAIL verdict = counted
credits vs position-aware drag (unchanged, the honest basis); rung-up (>$350→$1k) requires
BOTH accrued-pace ≥ 2× drag over trailing 3 days AND zero non-shrug losses; verdict reported
with per-market medians alongside totals so one outlier is visible.

## OPERATOR ANSWERS (2026-08-13, locked)
- **Q-A: halt budget $10/day realized.**
- **Q-B: canary at $252.53 (current venue cash); operator can fund more if the yield
  measurement justifies it.**
- **Q-C: CONFIRMED "yes"** — if the final safety review finds serious bugs, fix first and
  slip the relight one day (08-14 → 08-15) rather than start on unreviewed code.
- **Q-D: entry only if close_time ≤ 8 days out; hard exit any position held > 10 days.**
  (Consequence: every canary position self-liquidates inside the proof horizon; the
  MAMDANIOUT-class lockup trap is excluded by rule.)

## Build order (today): money-map job → scorecard → quoter mode → 8-angle review → shadow →
relight ask. Timeline: relight target 08-14 (slips to 08-15 only under T7's honesty clause);
proof 7 days from relight; scale-or-kill ~08-21/22.
