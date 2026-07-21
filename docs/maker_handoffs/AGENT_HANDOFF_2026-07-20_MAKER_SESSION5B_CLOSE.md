# MAKER LANE — SESSION-5B CLOSE HANDOFF (2026-07-20, supersedes SESSION5_CLOSE)

**One-line state:** operator said GO — staged live pilot approved (min-size first,
DEDICATED wallet, $75 floor). New engine DEPLOYED to the VPS (paper, halted).
Everything is ready except the wallet, which only the operator can provision.
Numbers discipline was rebuilt this session after a trust collapse — read §2
before quoting ANYTHING.

---

## 0. HARD RULES (operator-set; additions this session marked NEW)

- **"Maker"** never "MB"/"MM"; background processes are RECORDER ARMS, never "sim".
- **NUMBERS RULE** (`MAKER_MASTER_PLAN.md` §0) + **NEW §0a REWARDS BASIS ONLY** +
  **NEW §0b NO DERIVED EV UNTIL A RECEIPT**. Definition of record =
  `docs/MAKER_NUMBERS_LEDGER.md`. BANNED outright: net/EV/ROI points, blind or
  steered tier figures ("+116%", "−$698", "+$2,214"). Quotable: rewROI/day
  (labelled "model, unverified"), MEASURED facts (census, chain), CONFIG w/ line.
- **NEW — NO TAKER ANYWHERE.** Operator directive: a maker never crosses the
  spread. The preflight `fill` stage (the last taker code) was REMOVED
  (`6f9352f`). Live path = sanity → scoring → tiny live footprint → receipts,
  all read-only or post-only. Do not reintroduce a taker order.
- **Priority = PEER** (07-20). CLAUDE.md's "MB has all priorities" is STALE —
  trust the peer rule (memory `project_claudemd_priority_stale.md`).
- `git branch --show-current` before any repo write; main checkout is held by
  another bot. ONE branch `claude/maker-bot`. Worktree it.
- Everything numeric you present must be sourced: fresh canon run, in-session
  measurement w/ method tag, or chain decode. The operator's trust collapsed
  once this session over shifting EV numbers; it was rebuilt via the root fix
  in §2 — do not spend that trust again.

## 1. OPERATOR DECISIONS LOCKED THIS SESSION (do not relitigate)

1. **GO LIVE — staged, min-size first** (not full footprint).
2. **DEDICATED Maker wallet** (NOT MB's shared wallet). Operator provisions:
   fresh wallet → ~$5 POL → connect on polymarket.com (creates deposit wallet +
   approvals) → deposit USDC (operator picks, $100–300 suggested) → hand over
   `MAKER_PK` + `MAKER_FUNDER` (deposit-wallet address) for
   `/opt/pa2-maker-live/env`. `MAKER_SIG_TYPE` to be determined at sanity by
   testing, not guessed.
3. **Day-loss floor stays $75** for the pilot.
4. **No taker, ever** (see §0).

## 2. THE NUMBERS ROOT FIX (why answers used to shift; never regress this)

Operator caught the same data giving four different "EV"s. Root cause: canon
headlined trading-inclusive points; trading is a ±$thousands wave that
reshuffles with every reslice. Fix (structural): `mm_roi_canon.py` REBUILT so it
cannot emit a net/EV headline — every table leads with the REWARDS basis
(rew/day, rewROI/day, deterministic across slices); trading appears only as a
24h band or labelled `tradeDrag`. Steered tiers rank by FORWARD rewards density
(rew/cap), never hindsight net/cap (the "+116%" mirage). Commits `adbf149`
(canon) + `cf641c5` (ledger + §0b). Memory: `feedback_maker_rewards_basis_only`.

## 3. WHAT SHIPPED (branch `claude/maker-bot`, all pushed)

| Commit | What |
|---|---|
| `a68173b` | on-chain ledger reconciliation tool + 130 tests (pre-live gap #1) |
| `0063c61` | kill primitive scoped to Maker's own tokens (shared-account safety) |
| `971e68e` | asset-chain twin + cancel-response proof (verification round) |
| `adbf149` | canon root fix — rewards basis only |
| `cf641c5` | `MAKER_NUMBERS_LEDGER.md` + no-derived-EV-until-receipt (§0b) |
| `b1dfd61` | `mm_chain_verify_rewards.py` — chain-decoded reward payments |
| `6f9352f` | taker `fill` stage removed from preflight |

Engine tests 115 / reconciler 130 / maker-family 330 — all pass.

## 4. VPS STATE (verified at close)

- **NEW ENGINE DEPLOYED**: `/opt/pa2-maker-live/maker_live_engine.py` md5
  `1961f4b9…` (= branch HEAD, LF-normalized). Clean boot verified
  (`ENGINE START mode=PAPER … floor=$75`), zero errors. Backup:
  `maker_live_engine.py.bak-20260720_161119` (+ restart = rollback).
- Unit `polymarket-maker-live` ACTIVE, `MAKER_SUBMIT_MODE=paper`, **HALTED**
  (day-floor; operator resumes by deleting `/opt/pa2-maker-live/HALT`).
- venv FIXED: `py_clob_client_v2==1.0.1` + `httpx` installed (live mode used to
  crash at import; handoff-4's "1.1.0" was wrong — 1.0.1 is what's proven here).
- Recorder arms v1–v6 + sensor + census: untouched, running per master plan.

## 5. CHAIN/MEASURED FACTS ESTABLISHED (safe to cite with tags)

- **The pool pays, on-chain**: 26/26 recent REWARD payments decoded from Polygon
  receipts, $1,074.93, token = pUSD `0xc011…82dfb` read from logs
  (`mm_chain_verify_rewards.py`). Public RPCs only retain RECENT receipts —
  older return null (retention, not fraud). Never use the shared archive RPC key.
- **Census (real, hourly)**: ~$70–90k/day offered across ~1,000 rewarded
  markets; spike $348k Jul-19 (WC final) then cliff to ~$77k; weather = biggest
  sector pool all week; sports collapsed post-cup.
- **Elite cohort**: 34 of 2,293 sampled wallets earn ≥$2k/mo rewards (~1.5%,
  steep power law, top $52.7k/mo). Weather/econ-led; 20/34 two-sided vs 9/34
  one-sided holders. **Net P&L is UNMEASURABLE for 33/34** (data-api 3,000-row
  pagination cap truncates whales — source of two prior fake-number incidents:
  snapshot −$58k artifact and truncation +$25M artifact. DO NOT retry whale
  P&L from data-api). n=1 measurable ≈ breakeven, UNCONFIRMED (429s).
- **Gate hole (propose-only fix pending)**: in-play gate keys on
  `sector in ('sports','esports')`; mis-categorized sports props land in
  "unknown" and quote through settlement — 45% of a 40-market "unknown" sample
  had event-settlement signatures. Biggest fixable trading-drag cause.
- **Rewards accrue to RESTING QUOTES, not held inventory.** A held YES+NO pair
  earns nothing; the live orders on the book are the paycheck.

## 6. NEXT ACTIONS, IN ORDER

1. **Ask operator for the wallet** (§1.2). Blocked until provided. Do not
   engineer around it.
2. Wallet in env → `scripts/maker_preflight.py --stage sanity` (read-only) →
   SHOW OPERATOR the output → `--stage scoring` (post-only, confirms
   is_order_scoring, cancels).
3. **Tiny live footprint**: `MAKER_SUBMIT_MODE=live` + `MAKER_LIVE_ACK` +
   shrink `MAKER_MAX_MARKETS` to a handful, weather-led (census-backed). Rest
   through one 00:00Z reward window. First real maker fill here = the
   settlement test (no taker needed).
4. `--stage receipts` next day vs model accrual → **the first verified number**.
   Then run `maker_onchain_recon.py --wallet <MAKER_FUNDER>` — first real
   reconciliation of OUR positions.
5. Scaling decision = operator's, made on the receipt, not the model.
6. Parked: cap redesign (flat $150 gross cap makes 30/140 markets unquotable;
   no global cap — proposal before scaling), gate-hole fix (§5), gate-policy
   lock (calendar-gated ≥3 clean post-cliff days; v5-report trap: headline
   table ranks the WORST policy first), reconciler→engine wiring (own review
   cycle), CLAUDE.md priority fix (central).

## 7. LANDMINES

- Windows CRLF: compare engine md5 via `tr -d '\r' | md5sum` (raw checkout
  hashes differ from VPS).
- data-api 429s hard after heavy use — back off 3s×attempt, pace 0.1s+;
  a zeros-result on re-fetch = rate-limited, NOT "empty" — never present it.
- `_cancel_shortfall` demands an affirmative `canceled` list; a benign-but-
  unanticipated response shape fails LOUD at the first live kill — confirm the
  real shape during the tiny-footprint step.
- Kalshi = SEPARATE session/venue. This session audited their retool
  (design-level; findings handed over) — do not operate their bot.
- Live mode refuses partial config (PK+ACK+wallet all required) — deliberate.
- Trading only from the VPS (residential = geo-403).

## 8. STANDING DISCIPLINE

Tests + independent adversarial review + first-output cross-check vs a separate
source + live smoke where possible. Assume one more bug exists — every round
this session found one, twice via the adjacent-shape miss (fix one spelling
chain, grep for its twin). Money math changes get differential tests. Numbers
get sources or they don't get said.
