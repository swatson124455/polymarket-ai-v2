# EB scan-deadline watch — collect until operator check-in

**Fix under test:** `9c682fc` hard per-scan deadline (`SCAN_DEADLINE_S=300`, esports-only) — bounds unbounded external I/O (PandaScore HTTP, upstream of trade step) so a hung scan aborts+retries instead of wedging → watchdog restart.
**Release:** `20260613_174550`, esports up since **2026-06-13 21:50:44 UTC** (= T0).
**Full stack live:** precompute-gate + command_timeout(90) + stall-startup-grace(1200) + scan-deadline(300).

## What "working + trading at volume" looks like
- **Stability:** `scan_stall_self_restart` → near-zero; replaced by `scan_deadline_exceeded` warnings when PandaScore hangs (the scan aborting cleanly instead of wedging).
- **Trading:** `bot_pnl.py EsportsBot` entries RECOVER from the collapsed 2-entries/48h baseline (churn era was ~3-4/day). This is THE question — does bounding the upstream HTTP let the scan reach the trade step.

## Baseline (pre-fix, for comparison)
- Stability: ~22 stall restarts / 24h, 67 process generations / 40h, 5 completed scans / 40h.
- Trading (bot_pnl.py day-by-day): 06-13 **0** entries · 06-12 **2** · 06-11 **0** → **2 entries / 48h**.

## Snapshots
| Time (UTC) | Δ since T0 | stalls | scan_deadline_exceeded | scan cycles done | process gens | bot_pnl entries (24h) | notes |
|---|---|---|---|---|---|---|---|
| _baseline_ | — | ~22/24h | n/a (not deployed) | ~5/40h | 67/40h | 2/48h | pre-fix |
| 2026-06-14 14:10 | +16.3h | **1** | 30 | **470** | 8 (current up **11h**) | 06-13 **6** / 06-14 **0** so far | ⚠ STALE PARTIAL-DAY snapshot — 14:10 is pre-evening-slate; "0" was a measurement artifact, NOT a bug (see 21:00 row) |
| 2026-06-14 21:00 | +23.2h | **3** | 33 | **637** | — | 06-13 **6** / 06-14 **9** (full day) | VERDICT: working + trading at volume. 0614>0613. 21-agent read-only teardown (wf_36d94467) refuted the "0-entry" premise via symmetric-window + canonical bot_pnl. esports slates are evening-loaded; early-afternoon snapshots legitimately show ~0. Stability: 3 stalls/23h (vs ~22/24h baseline), 637 scans (vs ~5/40h). |

## VERDICT (2026-06-14 21:00 UTC) — VERIFIED WORKING + TRADING AT VOLUME
- **No bug.** The "06-14 0 entries" flagged at 14:10 was a stale partial-day snapshot taken before the evening esports slate. Full-day canonical `bot_pnl.py`: 06-14 = **9 entries** (> 06-13's 6); 48h net **+$483.19**.
- **Stability (the 5-fix stack, since T0 06-13 21:50, ~23h):** 3 `scan_stall_self_restart` (baseline ~22/24h, ~7× better), 33 `scan_deadline_exceeded` (the new clean-abort path doing its job on PandaScore hangs), **637 completed scan cycles** (baseline ~5/40h).
- **Method:** 21-agent read-only teardown (`wf_36d94467-c0b`) — derived the full funnel from code, pulled live 06-13-vs-06-14 counts, investigated all 13 layers, adversarially refuted the leading cause, ran a completeness critic. The null-hypothesis lens (Protocol 17 symmetric windows) is what caught the false premise.
- **Minor real item (not a gate):** cod market `0x44ac61778d` hit the liquidity `depth_exceeded` reject (`order_gateway.py:786-838`) repeatedly on a thin book through the day, but it CLEARED and filled in the evening (19:59, YES 1500 @0.18) — now an open position. Transient thin-book, self-resolved.
- **Honest gaps:** one adversarial agent (alternative-cause lens) died on a socket error mid-run — but the verdict rests on canonical `bot_pnl.py` which I re-confirmed independently, so it stands. A ground agent cited a stale (May-2026) "V2 dry_run=true" claim; live boot log shows V2 dry_run=False, but V2 `matched=0` so it writes 0 entries regardless — all 9 entries are V1 `EsportsBot`.
