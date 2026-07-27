# KALSHI MAKER — HANDOFF 2026-07-27 (latency work + go-live state)

**Bot still PARKED.** STOP present, `MAX_TOTAL_CAPITAL=1`, `TAKER_FLATTEN=0`.
Un-park needs explicit operator sign-off and has NOT been given.

⚠ **RULE TEN**: the earlier part of the 2026-07-27 session is QUARANTINED (order-lifetime
study, ALL presence figures, exit-vs-settle, capture $/day, program pagination counts, the
framework review). Nothing below is drawn from it. Everything here is a fresh measurement
with its own timestamp, or the two 07-26 audit docs cited by section.

---

## ⛔ SCOPE OF THE NEXT SESSION — OPERATOR DIRECTIVE 2026-07-27

**THE NEXT SESSION DOES OPTION B (§0) AND NOTHING ELSE.**

Every other item in this document is **TABLED** — carry it forward untouched into the
handoff AFTER option B lands. That includes §6 items 2-6, M2b, M4/M5, and everything in §7.
Do not start them, do not "quickly also" them, do not reorder them. Option B only.

Nothing here is cancelled — tabled means deferred with its priority intact (RULE NINE).

---

## §0 — THE ONE THING TO DO NEXT: option B (WS mirror in the cold cycle)

**NOT DONE. Deliberately not started** — it is a change to the book-read path of a
live-money bot and deserves the same adversarial pass Stage B got, not a rushed half-edit.

**The problem, measured 2026-07-27:**
- per-REST-read round trip **254ms p50** (min 251, max 451, n=12), from the VPS
- a cold cycle does **41 reads** -> `41 x 254ms = 10.4s` of pure serialized network
- measured cold cycle wall-clock: **14s** (down from 40s, see §2)
- so ~10.4s of the 14s is REST round trips. Lowering `REQ_SPACING_S` further buys nothing.

**The fix:** the WS daemon ALREADY holds live book mirrors (feed latency **46.3ms p50**,
measured, 0 gaps over 269 updates / 25 tickers / 45s). The cold cycle re-fetches over REST
what is already in memory. Serve `desired_quotes` from the mirror instead.

**Why it needs care, not speed:**
- it makes the WS mirror AUTHORITATIVE for quoting decisions, not just hot reprices
- mirror staleness / sequence-gap handling becomes load-bearing for the whole cycle
- `Feed` already tracks `gap_count`, `msg_count`, `last_msg_mono`, `confirmed_channels` —
  a staleness predicate must gate the fallback to REST, and that predicate is the
  entire safety of the change
- the honest failure mode: quoting off a silently stale mirror is worse than quoting
  slowly off fresh REST

**Interim option NOT taken (operator chose B):** parallelize the 41 reads
(~10.4s -> ~250ms-1s). Smaller blast radius, no mirror authority change, but it changes
burst behaviour and the 429 question becomes real. Recorded here so it is not lost.

---

## §1 — CEILING ON "MS", STATED HONESTLY

Two different paths; do not conflate them.

| path | what it does | latency |
|---|---|---|
| **hot (Stage B)** | WS event -> reprice off the mirror, no scan | detect **46ms** measured + venue WRITE round trip **UNMEASURED** |
| **cold cycle** | full footprint re-evaluation, 41 reads | **14s** measured |

**The floor is one HTTP round trip to the venue.** Reads measured 254ms p50; a write is
likely >= that. So a realistic hot reprice is **~300ms order of magnitude — INFERRED, NOT
MEASURED.** Sub-10ms is not reachable without colocation.

**TWO THINGS TO MEASURE NEXT SESSION (both cheap, neither needs un-park):**
1. **the venue WRITE round trip** — a dry_run order write, or an authenticated read. This
   replaces the ~300ms inference with a number and is the real ms ceiling.
2. **Stage B has NEVER fired on a real quote.** It is armed (`KALSHI_WS_HOT=1`) and inert
   while parked. Its actual reprice latency and its safety interlocks are unproven live.

---

## §2 — WHAT CHANGED ON THE BOX THIS SESSION (all reversible, backups listed)

| change | from | to | why |
|---|---|---|---|
| `KALSHI_WS_HOT` | absent (0) | **1** | Stage B armed, operator instruction |
| WS daemon service | did not exist | **enabled + active** | event-driven, replaces the timer |
| `polymarket-maker-kalshi-live.timer` | enabled, 2min | **disabled + inactive** | superseded by the daemon |
| `KALSHI_MAX_MARKET_CAPITAL` | 15 | **30** | operator: market size can be 30 |
| `KALSHI_THROTTLE_SMART` | 1 | **0** | operator rule: never carry that much unhedged |
| `KALSHI_REQ_SPACING_S` | 0.55 (code default) | **0.05** | self-inflicted sleep; 40s -> 14s |
| `websockets` | not installed | **16.1.1** | daemon transport (new dep, daemon only) |
| cash recorder | did not exist | **5-min timer, live** | forward reward attribution |

Backups: `live.env.bak-throttle-spacing-*`, `live.env.bak-wshot-*`, `live.env.bak-mktcap30-*`.
Deployed md5s match worktree for `maker_kalshi_ws_daemon.py`, `kalshi_ws_feed.py`,
`kalshi_cash_recorder.py`.

**`THROTTLE_SMART=0` reasoning, for the record:** its own docstring says it restores the
placement measured to ~triple naked-inventory build, and that its risk cost is NOT measured
while its reward gain IS. Naked is the dominant loss driver — **−$0.14596/ct vs −$0.02248/ct
hedged** (INTENT §5c-FIXED, 75 settlements / 4,733.1 ct, API 07-26T16:00Z). Operator ruled:
decide on not being unhedged. So: off.

**`REQ_SPACING_S=0.05` CAVEAT:** the code's own instruction is *"Measure 429s if changed."*
It has been validated over exactly **one** 41-read cycle (`fails=0c/0cr/0q`, no 429s). That
is thin. Watch the first live cycles.

---

## §3 — THE BLOCKER THAT IS NOW CLOSED: THE BOT QUOTES

The open question was whether the live quoter produces quotes at all. **It does.**

Dry-run of the LIVE quoter, temp dir (no STOP), `KALSHI_TRADING_MODE` unset, capital cap
lifted in-process, 2026-07-27:

```
cycle ok mode=dry_run footprint=40 quoted=19 ops=38 (cancel 0/create 38) fails=0c/0cr/0q
  programs_seen = 1948   footprint = 40   gated_out = 21
  drop_far_close = 13    empty_books = 2  presence_skipped_markets = 0
  quoted_markets = 19    two_sided_markets = 19    committed = $358.83
```

**19 quoted, 19 two-sided — 38 creates / 19 markets = exactly 2 per market, 100% two-sided.**

This also closes the "does `MAX_MARKET_CAPITAL` force one-sided quoting" question: **it does
not**, at cap 30 with `JOIN_SIZE=20`.

**A false alarm I raised and then disproved — do not re-raise it:** an earlier probe showed
"0 quotes on 120 markets". That was MY harness: hand-built market dicts (wrong keys — `end`
not `close_time`, plus `ramp_min`) on the wrong sample (highest-POOL programs, not the bot's
capture-ranked footprint). It was not a bot defect.

---

## §4 — TIMER / TIMING ARTIFACT AUDIT (asked for, completed)

**No stale live-trading timer remains.** Verified unit-by-unit:

| unit | state | what it runs |
|---|---|---|
| `-ws.service` | **enabled, active** | the WS daemon — THE live path |
| `-live.timer` | **disabled, inactive** | old 2-min quoter timer, superseded |
| `-quoter.timer` | enabled, 10min | **`mode=dry_run`**, plan-only, `/opt/pa2-maker-kalshi-quoter`. Places NOTHING — journal-verified. Left running. |
| `-kalshi.timer` | enabled, 5min | recorder, read-only, no trading. Left running. |
| `-cash.timer` | enabled, 5min | cash recorder (new). Read-only. |
| `-ledger.timer` | enabled, 60min | attribution ledger |

Timing constants still in code, NOT changed: `WS_COLD_S=60` (daemon heartbeat),
`STOP_ESCALATE_S=90` (flatten sleep, only bites under STOP — it is why a STOP-era cold cycle
measures 92.06s and why Stage A could not be validated end-to-end while parked).

---

## §5 — REWARD ATTRIBUTION: SOLVED ENOUGH TO USE

- **Reconcile on CASH ALONE.** `balance_dollars` is GROSS — it is NOT net of resting-order
  collateral. Proven: an operator-confirmed **$20.39** reward raised
  `cash − fills − settlements` by exactly **+$20.3865** while the
  `cash + reservation` form FELL. A reward must lift both; only cash did.
- **The trade API has NO reward line-item — 35 paths probed, all 404.** Only
  `/portfolio/balance`, `/portfolio/fills`, `/portfolio/settlements` exist.
- **The WEB UI DOES itemize, per EVENT.** Operator screenshot 2026-07-27 showed six rows
  summing **$20.32** against the recorder's **$20.3865** (list scrollable, partial). So:
  **recorder = reliable total, UI/CSV export = per-event split, and they cross-check.**
- **Multi-day / week-long programs DO pay** — closes MEASUREMENT §8.1, the audit's #1 open
  question. Same event paid **three separate rows**, so credits are not one-per-event.
- Conventions validated: fills **ACTION-ONLY, YES-SIGNED** on `yes_price_dollars`
  (integrity 75/75 settled tickers); settlements pay on **NET position** (the gross
  "paired pays $1/pair" model is REFUTED — implied a −$1,977 residual).
- Recorder: `kalshi_cash_recorder.py`, 5-min timer, records `cash` AND `resting_reservation`
  separately plus raw orders/positions, and emits unexplained-to-date under BOTH candidate
  invariants so neither is assumed.

**NOT DONE:** M2b, an automated per-event export. Today the split needs a manual UI/CSV pull.

---

## §6 — OPEN ITEMS FOR GO-LIVE  (ALL TABLED — see scope directive at top)

1. **Option B** (§0) — the ms fix.
2. **Measure the venue write round trip** (§1) — replaces the ~300ms inference.
3. **Un-park** — operator's go. Mechanically: clear STOP, raise `MAX_TOTAL_CAPITAL`.
4. **Per-family exposure caps — TABLED by operator.** Recorded so it is not lost: M3 measured
   `gpu_restock` at **$23,600/day = 21.1% of venue across 922 markets / 18 series**, and the
   **top 5 underlyings = 50.3%** of venue pool (live read 2026-07-27T14:34:23Z, 1,692
   programs, $111,888.33/day). Spreading across markets does NOT diversify — 922 GPU markets
   are one bet. All figures are LOWER bounds (15.8% of pool still unmapped, each counted as
   its own underlying).
5. **`REQ_SPACING_S=0.05` 429 watch** (§2) — one cycle of evidence only.
6. **Stage B unproven live** (§1).

---

## §7 — WHAT WAS MEASURED AND CAME BACK NEGATIVE (do not re-litigate)

**Phase 3 — velocity-conditional placement is NOT justified on available evidence.**
Committed as `phase3_velocity.py`. Reusing study3.py's conventions verbatim (fill DIRECTION
and fill SIZE capping had each already silently inverted a result once), bucketing every
snapshot by observed reference velocity:

**k=0 wins in QUIET, DRIFT and FAST, under BOTH queue models (swept and touched).** Only the
UNKNOWN bucket flips, and UNKNOWN is excluded by construction (snapshot gap > 300s, so its
"velocity" is a stale difference).

**It is also underpowered and that is stated, not buried:** of 4,159 snapshots, QUIET 2,827
(68.0%), UNKNOWN 1,238 (29.8%), and the two moving buckets together only **94 (2.3%)** —
FAST carries 6 fill events swept / 14 touched. It rules the policy out on the evidence
available; it does not prove no effect exists. Same fill-sparsity wall as MEASUREMENT §4.

**Already-built things I proposed and then found existing — do not rebuild:**
- per-event inventory aggregation (`event_deltas`, consumed by `desired_quotes`)
- adjacent-strike floored pairing (`ladder_pairing`) AND active acquisition of the
  offsetting leg (the LADDER ESCAPE HATCH at `maker_kalshi_quoter.py:2166`)
- the strand-path hole from INTENT §5.3 — closed by `447c271`, with three plan counters so a
  position with no exit is counted, not silent

**A rules misreading that was corrected — do not revert to it:** R3's two-sided test is on
**the MARKET's book, not our own orders** (`KALSHI_LIP_RULE_CANON.md` §R3, quoting S1).
Quoting one-sided does NOT zero us where others make the market two-sided; it costs only the
normalised score on the side dropped. Two-sided is still correct (R4 pays the mean of
`(yes_share + no_share)/2`) — but the justification is a PROPORTIONAL loss, not a $0 cliff.

---

## §8 — COMMITS THIS SESSION

```
dcdbfa0  feat(kalshi): forward cash recorder + venue census
7e0f3ea  study(kalshi): Phase 3 velocity conditioning (NEGATIVE) + M3 underlying map
cd817ee  fix(kalshi): M3 family table — venue far more concentrated than series counting shows
```
Suite **567 passed + 2 xfailed** throughout.
