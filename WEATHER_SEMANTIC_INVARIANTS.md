# WEATHER_SEMANTIC_INVARIANTS.md — the rules of the road for WeatherBot's signal→fill path

**Created:** S245 (2026-06-14). **Why:** five BLOCKER-class bugs in two weeks all shared one
root — the *meaning* of `model_prob`, `confidence`, `price`, and `side` was never written
down, so each layer made its own assumption and the assumptions drifted apart (S237 exits
flipped to BUY; S238 phantom entries; S245 entry slippage anchor YES-vs-NO; S245 Kelly
NO-side double-inversion). This doc names every convention and points at the boundary
assertions that enforce them. **Read this before touching any signal, sizing, gate, or
fill code.** If you change a convention, change it here too.

---

## 1. The two probability fields are NOT the same denomination

| Field | Meaning | YES opp | NO opp |
|---|---|---|---|
| **`model_prob`** | **ALWAYS P(YES-token resolves YES)** — engine-agnostic, side-agnostic | `P(YES)` | `P(YES)` (NOT 1−P(YES)) |
| **`confidence`** | **P(the bet's OWN side wins)** = P(side) | `P(YES)` | `1 − P(YES)` = P(NO) |

- **`model_prob` is canonical P(YES) for BOTH sides, in ALL FOUR engines** (temperature,
  wind, precip, snow). This was made true in S245 (`0f6c0da`): the wind producer
  (`weather_bot.py:2579`) and the precip/snow producer (`precipitation_engine.py:227`,
  both byte-identical copies) were changed from storing `1−model_prob` to `model_prob` for
  the NO branch. Temperature already complied.
- **`confidence` is P(side)** everywhere and is a *different field* — for a NO bet it is
  `1 − P(YES)`. **Do NOT "fix" the confidence NO branches to match model_prob.** They look
  superficially similar (`1.0 - model_prob`) but are correct as-is. Inline comments at each
  NO producer warn about this.

### Who consumes which
- **`model_prob`** (must be P(YES)): the S-T/Kelly allocator `weather_bot.py:3282`
  (`p = model_prob if YES else 1-model_prob`) and the executable-edge gate
  `weather_bot.py:4769` (`_side_prob = model_prob_yes if YES else 1-model_prob_yes`). Both
  derive P(side) themselves by flipping for NO — so they REQUIRE model_prob = P(YES). If a
  producer stores P(side) instead, they double-invert and silently drop every NO opp.
- **`confidence`** (P(side)): all *sizing* — Kelly fallback `calculate_bot_position_size`
  (`:3664`), the negative-EV gate (`:3799`, `confidence < price`), the confidence-tail
  dampener (`:3782`), the YES confidence floor (`:3050`). Sizing is NOT model_prob-driven.
- **Persistence:** `model_prob` → `prediction_log.predicted_prob`, which the DB scores as
  **P(YES)** (Brier vs `resolution='YES'`, `was_correct = predicted_prob>=0.5 == YES`).
  S245 canonicalization fixed the going-forward scoring for wind/precip/snow NO rows but
  created a mixed-denomination history (no `side` column to migrate cleanly — prefer a
  cutover timestamp).

### Enforced by
`weather_bot.py:3282` engine-boundary assertion (S245): range-check on model_prob +
directional canary (a NO opp's canonical P(YES) must sit at/below `1 − price`; a P(side)
value trips it). `raise AssertionError`, survives `python -O`.

---

## 2. `price` is the cost to buy the BET's own token

- `opp["price"]` = the price of the token you are buying: **YES price for a YES bet,
  `1 − YES_price` (the NO token price) for a NO bet.**
- Therefore for a NO opp: `1 − opp["price"]` = the implied YES price.
- EV/edge are same-token: `edge = confidence − price` (both P(side) / price-of-side);
  a NO opp is generated only when `P(NO) > NO_price` ⇔ `P(YES) < YES_price`.
- **Slippage anchor must be same-token as the book being walked** (S231/S232): anchor to
  the REAL top-of-book of the side's token (`_shadow_best_ask`/`_shadow_best_bid`), NOT a
  synthetic `effective_price ± spread/2` built from cached YES/NO mids. The synthetic value
  is YES-denominated and mismatches a NO-book fill → ~183% phantom slippage (the S245 entry
  bug). [R3 follow-up: stop constructing the synthetic anchor at root.]

---

## 3. `side` means different things at signal-time vs execution-time

- **At signal / sizing:** `side ∈ {"YES","NO"}` = which token to buy. `token_id` is that
  side's token. `model_prob`, `confidence`, `price` follow the conventions above.
- **At execution (`place_order`):** still pass `"YES"`/`"NO"` — `order_gateway` converts to
  a BUY of that token. **NEVER pass `"BUY"`/`"SELL"` as the signal side.**
- **At EXIT:** pass **`side="SELL"`** — NOT the held token side. `order_gateway` routes any
  side ≠ `"SELL"` as a BUY, so passing the token side BUYS the opposite token instead of
  closing (the S237 BLOCKER `882f1fb`). All 3 WeatherBot exit sites use `side="SELL"`
  (`weather_bot.py:4205/4395/4463`).

---

## 4. Keying conventions

- **`market_id`** appears in two id-spaces: Gamma numeric id and `condition_id` (0x…).
  Lookups must try BOTH (`_market_index` AND `_market_index_by_cid`) or they silently miss
  → bid/ask=0 → synthetic-anchor fallback. (`paper_trades.market_id ≠ trade_events.market_id`
  for older rows.)
- **`token_id`** = the bet side's token; used for the order book, position, and exposure.
- **Position / dedup keys:** `(bot, market, side)` in the coordinator/DB; the paper engine
  keys positions by `(bot, market)` only (safe today because YES/NO are disjoint per bot).

---

## 5. The two trees (S239 dead-tree trap)

The live systemd unit runs `main.py` → **TOP-LEVEL `base_engine`**, but `weather_bot.py:46-47`
imports the weather PRODUCER engines from the **SILO**
(`bots.weather.engine.base_engine.weather.*`). So:
- **Producer engines (precipitation_engine, probability_engine):** the SILO copy is LIVE.
- **Execution/risk (paper_trading, order_gateway, liquidity_guardian):** the TOP-LEVEL copy
  is LIVE (injected into the bot).
- A fix in the wrong copy is DEAD CODE — this is exactly how S231/S232/S237/S245 ran nowhere
  for sessions. **Guard:** `tests/unit/test_engine_tree_parity.py` (R1) fails on cross-tree
  drift / missing live-tree fix markers. Cross-branch (wb/main vs master) gaps are inventoried
  in `CROSS_BRANCH_LANDMINES.md`.

---

## Checklist before editing signal→fill code
1. Is the value P(YES) or P(side)? Match the table in §1. `model_prob`=P(YES); `confidence`=P(side).
2. Is `price` the bet-token price, and is any anchor same-token as the walked book? (§2)
3. Is `side` YES/NO (signal) or SELL (exit)? (§3)
4. Are you editing the LIVE tree for this file class? (§5)
5. Add/keep a boundary assertion so a future violation fails loudly, not silently.
