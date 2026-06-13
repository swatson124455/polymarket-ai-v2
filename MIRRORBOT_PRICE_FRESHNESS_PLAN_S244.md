# MirrorBot Price-Freshness Fix — Scoping & Plan (S244, 2026-06-13)

> **IMPLEMENTED 2026-06-13 (awaiting independent review + deploy-on-word).** All 4
> sign-off decisions confirmed by operator. Helper `_fresh_side_price(market_id, side,
> token_id, market_data)` added to `bots/mirror_bot.py`; wired at the single price-override
> site (return-False skip on stale, distinct `mirror_skip_stale_price` log + a
> `mirror_rejected_signals` row at stage `price_freshness`). Live key = the **traded
> `token_id`** (RTDS asset_id, side-correct by construction — more robust than
> market_data's `{side}_token_id`, which is a fallback). `_max_age` moved inside the
> try (malformed config → skip, never crash). 2 Tier-2 knobs in `config/settings.py`:
> `MIRROR_USE_LIVE_MIDPOINT` (default true; rollback=false), `MIRROR_PRICE_STALENESS_MAX_SEC`
> (default 300). 15 new unit tests + 6 integration-test mocks updated; full unit suite
> **3565 passed / 0 failed**.
>
> **Pre-deploy baseline (journalctl, last full hour before deploy — for the post-deploy
> before/after the operator asked for):** of 582 `mirror_split_scoring` lines,
> `wf_slippage=0.0` in **176 (~30%)**, `geo_mean=0.0` in **248 (~43%)**. Post-deploy on
> fresh prices: if the spurious vetoes were stale-driven, `wf_slippage=0` should drop
> substantially. If it does NOT drop → either real fast-market movement (slippage
> graduation becomes relevant) or the fresh fetch isn't fresh (fix didn't land). Also
> watch the `mirror_skip_stale_price` rate (live-fetch coverage proxy).

**Status:** IMPLEMENTED — awaiting independent review + deploy-on-word.
**Origin:** the "graduate slippage" commit's price-freshness precondition found a bigger bug — the price feeding slippage AND entry/cost-basis is median ~57 days stale. This redirects the work.
**Scope:** read-only investigation (3 subagents + DB queries), all claims file:line-verified. No code changed.

---

## The verified bug
MirrorBot reads the side price from `get_market_from_index` ([base_engine.py:329](base_engine/base_engine.py:329)), which is populated **only** from DB `markets.yes_price/no_price` ([base_engine.py:317](base_engine/base_engine.py:317), `_fetch_tradeable_markets`). MirrorBot has **no `on_price_update` handler**, so it never gets the WS freshening flow — it reads whatever the scan/DB last wrote.
- **Freshness (DB query):** active markets — `<1h: 203 · 1-24h: 1,278 · 1-7d: 8,001 · >7d: 134,946` (of 146,169). **~92% are >7 days stale.**
- At [mirror_bot.py:3266-3268](bots/mirror_bot.py:3266) this stale price overrides `price`, which then becomes the slippage anchor ([3284](bots/mirror_bot.py:3284)) **and** the recorded entry/cost basis ([4081](bots/mirror_bot.py:4081)/[4248](bots/mirror_bot.py:4248) → confirm_position).

---

## A — Fresh-price source (the decision)
Two candidates compared:

| | Live fetch `get_token_midpoint(token_id)` ([polymarket_client.py:919](base_engine/data/polymarket_client.py:919)) | Side-key the WS cache (`_ws_price_cache`, [base_bot.py:308](bots/base_bot.py:308)) |
|---|---|---|
| Side-correct | **Yes — per-token, native** | Needs change: cache keys by `market_id` and drops `token_id` → YES/NO **collide** (latent bug, also hits EnsembleBot's only use at [ensemble_bot.py:1189](bots/ensemble_bot.py:1189)) |
| Coverage | **Every market** (incl. neg-risk / low-liq whale tail) | Only the ~500 WS-subscribed markets ([base_engine.py:2293](base_engine/base_engine.py:2293)); misses the whale tail (the same tail the 3-tier fallback exists for) |
| Freshness | Always live (`use_cache=False`) | Real-time when present; needs a TTL/recv-time field added |
| Latency | ~1 CLOB round-trip per signal, **only on signals past the $25 whale gate** (low volume) | Zero (cached) |
| Rate limit | Non-issue: 100/s budget ([settings.py:171](config/settings.py:171)); per-signal ≪ 1/s | n/a |

**Recommendation: live `get_token_midpoint(side_token_id)` as the primary fresh source.** It is side-correct by construction and covers every whale market — decisive for a low-volume path. Side-keying the WS cache is worth doing *separately* (it's a real latent bug affecting EnsembleBot) and can later become a zero-latency fast-path, but it is insufficient alone (coverage gap).

---

## B — Fallback chain + staleness guard
Replace the current "use the DB price even if 57 days old" with, in order:
1. **(optional, later) WS price** — if side-keyed AND `recv_t` within TTL.
2. **Live `get_token_midpoint(side_token_id)`** — primary.
3. **DB `markets.*_price`** — ONLY if `markets.updated_at` is within a **staleness threshold** (proposed **5 minutes**, fast-market-appropriate).
4. **Else SKIP the signal** (reuse the existing `mirror_market_data_retry_fail` hard-block shape at [mirror_bot.py:3201](bots/mirror_bot.py:3201)) — **never** evaluate/enter on a >threshold-stale price.
- **Coverage watch:** a fresh source returning None where the stale dict returned a value would skip the trade. Given ~92% of DB prices are >7d stale, the live fetch becomes the de-facto source; skips should be rare if the CLOB fetch succeeds. Log skip-rate post-deploy.

---

## C — Blast radius (what the fix touches)
Within MirrorBot, **only the [line 3126](bots/mirror_bot.py:3126) read consumes price** (the [1359](bots/mirror_bot.py:1359) exit read is date-only; the [3689](bots/mirror_bot.py:3689) read is behind a default-off flag). Making that `price` fresh affects:
- **Helps (currently wrong → fixed):** entry/cost-basis value, kelly/NO-edge gating ([3924](bots/mirror_bot.py:3924)/[3908](bots/mirror_bot.py:3908)), MQ/spread/volume gates, share-count sizing ([3823](bots/mirror_bot.py:3823)).
- **Behavior-changes by design:** `wf_slippage` ([3284](bots/mirror_bot.py:3284)) and `wf_price_dir` ([3277](bots/mirror_bot.py:3277)) will finally measure **real** slippage/drift vs the whale fill instead of 57-day noise → the gate_score distribution **shifts**. This is the intended fix, but it means **the gate-threshold recalibration (later Commit) must come after this lands** and be measured on fresh data. It also means the **slippage-graduation commit may be unnecessary** once the input is real — reassess then.
- **Untouched / safe:** mark-to-market (`position_manager._update_current_prices`, [position_manager.py:550](base_engine/execution/position_manager.py:550)) already uses a fresher source (`market_prices_latest` + live CLOB orderbook), independent of `get_market_from_index`. But its `uPnL = mark − entry` still has a **stale entry leg** until this fix (improves new positions only).

---

## D — Ledger link: PARTIAL contributor (mechanism confirmed, not the whole story)
- **CONFIRMED in code:** both paper AND live record the stale signal `price` (= `effective_price`) as `positions.entry_price` and the ENTRY trade_event price ([trade_coordinator.py:224](base_engine/coordination/trade_coordinator.py:224)/[:335](base_engine/coordination/trade_coordinator.py:335)). The **real CLOB fill is never read back** — `execution_engine.place_order` echoes the submitted price ([execution_engine.py:590](base_engine/execution/execution_engine.py:590)); the live fill exists only in the `shadow_fills` audit table ([order_gateway.py:1413](base_engine/execution/order_gateway.py:1413)), never in the position.
- **But the dominant ledger problem is COMPLETENESS, not value:** `LIVE_ONCHAIN_RECONCILIATION_2026-06-03.md` found 44/57 rows with **no** cost basis, 0 live RESOLUTION events, entry_cost cleared on close. Stale-price corrupts the *value* of the rows that DO record; it cannot explain the 77% that record nothing.
- **Therefore:** this freshness fix improves cost-basis *value* going forward, but does **NOT** make it the true fill. **Separate work item (NOT this fix): capture the real fill price** — requires `execution_engine.place_order` to return an actual fill instead of echoing the submitted price, then feed that into `confirm_position`. Flagged, not bundled.

---

## Proposed implementation shape (for the price-freshness commit — NOT YET WRITTEN)
1. A small helper (e.g. `_fresh_side_price(market_id, side, market_data) -> (price|None, source)`) that: resolves the side's `token_id` from `market_data` (`yes_token_id`/`no_token_id`); tries live `get_token_midpoint(token_id)`; falls back to DB price only if `updated_at` within the staleness threshold; else returns `None`.
2. At [mirror_bot.py:3264-3268](bots/mirror_bot.py:3264), use the helper; if `None`, take the existing skip path (don't enter on stale).
3. Config knobs (Tier-2): `MIRROR_PRICE_STALENESS_MAX_SEC` (default 300), `MIRROR_USE_LIVE_MIDPOINT` (default true) for rollback.
4. **Do NOT** change the slippage factor, the threshold, or sizing in this commit — those are downstream and recalibrated after, on fresh data.

## Test plan
- Unit: helper returns live midpoint when available; returns DB price when fresh; returns None (→skip) when DB stale + live unavailable; side-correctness (YES vs NO token).
- Full `tests/unit/` (shared-path change).
- Post-deploy: skip-rate (coverage check), and the `wf_slippage`/`wf_price_dir`/`gate_score` distribution shift (input now fresh) — feeds the later threshold recalibration.

## Revised fix order
1. **Price freshness (THIS plan)** → 2. reassess slippage graduation (may be moot on fresh data) → 3. move track-record to sizing → 4. recalibrate gate threshold on fresh distribution. Separately: **capture real fill price** (true cost basis — its own WI).

---

## Sign-off asks (before any code)
1. **Fresh source:** live `get_token_midpoint` primary + DB-with-5min-staleness-skip fallback (WS side-keying deferred as a separate optimization/EnsembleBot fix)? Or do you want WS side-keying first?
2. **Skip-on-stale:** confirm the bot should **skip** a signal when no fresh price is available, rather than ever using a >5min-stale price (accepts a possibly-lower trade rate; verification flow comes from the gate fixes, not stale prices).
3. **Staleness threshold:** 5 minutes for the DB fallback — OK, or tighter/looser?
4. **Scope boundary:** capture-real-fill-price (true cost basis) is a **separate** work item, not this commit — confirm.
