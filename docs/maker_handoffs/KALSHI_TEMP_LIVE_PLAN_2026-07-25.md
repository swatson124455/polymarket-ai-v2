# KALSHI — TEMP-LIVE CONFIGURATION PLAN (2026-07-25)

Operator directive: *"Proceed as if temp is live. Just monitor until it's back up."*
Watcher is **RUNNING** (`temp_watch.py`, 60s cadence, appends `temp_poll.jsonl`; emits TEMP BACK /
ERROR / hourly HEARTBEAT). Temp absent from every active pull since `2026-07-22T17:00:00Z`.
**Absent ≠ gone** — temp programs are ~58-minute hourly windows that exist only while that hour's
market is live. All five `KXTEMP*` cities are already in the live `KALSHI_SERIES_ALLOW`, so nothing
must be added for the bot to see them return.

---

## §1 — THE FINDING THAT CHANGES THE CONFIG

**Temp is the biggest earner by TOTAL, but it is the WORSE book per window.**

Source: `rewards_ui.tsv`, the 31 canonical operator-UI reward rows summing to $88.07.
ESTABLISHED.

| | TEMP | GAS |
|---|--:|--:|
| total | **$55.03** (62.5%) | $33.04 (37.5%) |
| credit items | 18 of 31 | 13 of 31 |
| distinct events | **12** | **4** |
| **median $/EVENT** | **$2.46** | **$9.45** |
| median $/credit item | $2.09 | $2.02 |

**Temp's lead is event COUNT (12 vs 4), not event QUALITY — gas pays ~4x more per event.** Per
credit item the two families are statistically indistinguishable ($2.09 vs $2.02).

Two robustness checks, both of which weaken temp further:
- **Drop the uncorroborable `KXTEMPNYCH-26JUL2206 $12.94` row** (open operator decision #2, a 9x
  outlier on reward/notional): temp falls to **$42.09 of $75.13 (56.0%)**, and its mean per credit
  item falls to **$2.48 — marginally BELOW gas's $2.54**.
- **Concentration** (n=12 temp events): the top event (`KXTEMPAUSH-26JUL2021`, $16.48) is **29.9%**
  of temp income; the **top 2 are 53.5%**. Leave-two-out leaves **$25.61 over 10 events, median
  $2.09**. The typical temp window pays ~$2, not ~$4.59 (the mean).

Set against the handoff's measured fill cost — temp maker round trips **−6.12¢/ct** vs gas
**−1.55¢/ct** (INFERRED: handoff-stated, **not re-derived this session**) — the shape is:

> **Temp's edge is the NUMBER of windows. Temp's cost is per CONTRACT, and it is ~4x gas.**
> ⇒ The correct temp configuration harvests MANY windows with the SMALLEST viable footprint each.
> It is the opposite of sizing up.

The ledger's own controlled triple already says this, and it is the only clean natural experiment
we have (same hour closing 2026-07-22T10:00Z, same pool, behaviourally identical):

| event | footprint | paid |
|---|---|--:|
| `KXTEMPNYCH-26JUL2206` | 20ct, 1 strike | **$12.94** |
| `KXTEMPDCH-26JUL2206` | 20ct, 1 strike | $1.51 |
| `KXTEMPCHIH-26JUL2206` | **52ct, 2 strikes, earlier, longer span** | **$0.00** |

**The largest credit came from the smallest footprint; the largest footprint earned nothing.** More
size did not buy more reward. That is the signature of a pro-rata denominator we do not measure —
not of a behaviour we can tune.

---

## §2 — WE DO NOT NEED TO UN-PARK TO LEARN THIS

The per-market telemetry committed this session (`e01e188`) runs **while parked**. The orderbook is
fetched and the desired book computed for every footprint market *before* the capital cap gates the
create, so at `KALSHI_MAX_TOTAL_CAPITAL=1` every column is still measured with our resting size
honestly logged as 0. Verified against the live parked bot: its plan rows show
`capped_markets=5 / creates=0` while still computing a **$56.35 intended book**.

So when temp returns we can measure, **at zero capital risk and without operator sign-off**:
- the **competing qualifying depth** on each temp strike (the R4 denominator — the one variable §3
  of the handoff identified as dominant and which nothing has ever recorded);
- what a 20ct join at reference **would** have captured, per window, per city;
- whether temp windows are thin (we would be a real fraction) or saturated (our share ~0).

That converts open decision #3 (un-park or not) from a judgement call into a measurement. **This is
the recommended path.** The 12-event history cannot settle it — its top 2 events are 53.5% of the
income.

---

## §3 — THE CONFIGURATION, READY TO APPLY (NOT APPLIED)

**Nothing below is applied. Raising `KALSHI_MAX_TOTAL_CAPITAL` is un-parking a real-money bot and
needs explicit operator sign-off.** Present state verified 2026-07-25 21:00Z: deployed build md5
`727ca7c59840a42b51c19e24c65a0982`, `MAX_TOTAL_CAPITAL=1`, `MAX_ACTIVATE_CAPITAL=0`,
`TAKER_FLATTEN=0`, `REDUCE_ONLY_KEEP_BOTH=1`, timer active, no effective STOP sentinel (the file
present is a renamed archive, `STOP.halted-20260722_1731`; the quoter matches only `<dir>/STOP`).

### Stage 0 — OBSERVE (no capital, no sign-off needed beyond the deploy)
| key | value | why |
|---|---|---|
| `KALSHI_MKT_TELEMETRY` | `1` (default) | per-market rows; the whole point |
| `KALSHI_MAX_TOTAL_CAPITAL` | **`1` — UNCHANGED** | stays parked |

Deploy the telemetry build parked, let the watcher fire, collect **≥10 temp windows** of per-market
rows. Then re-decide with measured competition instead of a 12-event history.

### Stage 1 — IF the operator un-parks (only after Stage 0 data)
| key | now | proposed | rationale |
|---|--:|--:|---|
| `KALSHI_JOIN_SIZE` | 20 | **20 (hold)** | 20ct/1 strike is the only footprint that ever produced a large temp credit; 52ct produced $0 |
| `KALSHI_MAX_MARKET_CAPITAL` | 15 | **15 (hold)** | caps one strike; prevents the 2-strike shape that paid $0 |
| `KALSHI_MAX_TOTAL_CAPITAL` | 1 | **100** | 5 cities x 1 strike x two-sided 20ct ≈ $20/strike (yes+no ≈ $1) |
| `KALSHI_MAX_ACTIVATE_CAPITAL` | 0 | **0 (hold)** | never supply Target depth into a thin temp book |
| `KALSHI_TAKER_FLATTEN` | 0 | **0 (hold)** | temp's fill cost is the problem; do not add taker cost |
| `KALSHI_HELD_MAX_USD` | 100 | **100 (hold)** | |
| `KALSHI_DAILY_LOSS_HALT_USD` | 40 | **40 (hold)** | |

**Explicitly NOT proposed:** raising `JOIN_SIZE`, adding strikes per event, or dropping any city.
`KXTEMPLAXH` has **n=1 event / $1.85** — far too small a sample to exclude on, and excluding it
would bias the very measurement Stage 0 exists to make.

**Rollback (either stage):**
```bash
sudo -n bash -c 'sed -i "s/^KALSHI_MAX_TOTAL_CAPITAL=.*/KALSHI_MAX_TOTAL_CAPITAL=1/" /opt/pa2-maker-kalshi-live/live.env'
```
The 2-min timer starts a fresh process each cycle and re-reads `live.env`, so the next cycle is
parked again — no restart needed.

---

## §4 — WHAT THIS PLAN DOES NOT ESTABLISH

- The **−6.12¢/ct vs −1.55¢/ct** fill-cost split is handoff-stated and was **not re-derived here**.
  It is load-bearing for "small footprint," so it should be re-derived from the fills tape before
  Stage 1.
- Whether temp windows are thin or saturated is **unmeasured** — that is precisely what Stage 0
  answers. No claim here rests on it.
- `$12.94` remains uncorroborable (open decision #2). Every conclusion above was tested with and
  without it and does not flip: temp stays the larger family by total either way, and stays
  per-event *worse* than gas either way.
- Reward totals are **$88.07, of which $25.21 (28.6%) is receipt-verified**; the other $62.86
  (71.4%) is unattested. Family splits inherit that limitation.
- `KXAAAGASW-26JUL27` is **PENDING, not zero** — a weekly closing 07-27, credits ~07-28. It is not
  in the gas figures above and must not be scored as a failure.
