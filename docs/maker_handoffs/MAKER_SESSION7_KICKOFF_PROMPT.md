# MAKER SESSION 7 — KICKOFF PROMPT (paste this to start the new session)

---

MAKER LANE — SESSION 7. Local Windows session, direct SSH to the VPS
(key `C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem`, ubuntu@18.201.216.0).

HARD RULES (operator-set, non-negotiable):

* "Maker" — NEVER "MB" (=MirrorBot) or "MM". Background processes are RECORDER
  ARMS, never "sim".
* NUMBERS: rewards basis ONLY (rewROI/day, labelled "model, unverified"). NO
  net/EV headline ever. NO derived EV until a real receipt. Every number needs a
  source: a fresh canon/probe run, an in-session measurement with a method tag,
  or a chain decode. Read `docs/MAKER_NUMBERS_LEDGER.md` before quoting anything.
  Trust collapsed once over shifting numbers — do not spend it again.
* NO TAKER ANYWHERE. A maker never crosses the spread. There is no taker path in
  the engine; never add one. Live path is read-only or post-only.
* KALSHI IS KING (RULE FIVE, 2026-07-23). Kalshi is live + trading. This
  Polymarket Maker session/bot makes NO change to any SHARED item without
  explicit operator permission — the VPS beyond `/opt/pa2-maker-live`, systemd,
  shared `/opt/pa2-shared/.env`, `base_engine/**`, `deploy.sh`, master. STOP and
  ask, even if the change looks obviously good. Maker-OWNED resources stay free
  (branch `claude/maker-bot`, `/opt/pa2-maker-live` + its own env,
  `/opt/pa2-maker-sim*`, census, `scripts/maker_*`). Kalshi is a SEPARATE
  session/venue — never operate/modify its bot, its units
  (`polymarket-maker-kalshi*`), or its branch `claude/maker-kalshi-live`.
* `git branch --show-current` before ANY repo write. The main checkout
  C:/lockes-picks/polymarket-ai-v2 is HELD BY ANOTHER BOT — never write there.
  Work in a worktree on `claude/maker-bot` (HEAD `f706588`, pushed, clean). Bash
  cwd drifts to the held checkout — use `git -C <worktree>` + absolute paths.
* Windows CRLF: compare engine md5 via `tr -d '\r' | md5sum`.

STEP ZERO (read in this order, before writing any code):

1. `docs/maker_handoffs/AGENT_HANDOFF_2026-07-23_MAKER_SESSION6B_CLOSE.md`
   ← START HERE. Full state, what shipped, VPS state, open items, the 3 binding
   review lessons.
2. `docs/POLYMARKET_MAKER_RUNNING_TAB.md` — the append-only ledger of record.
   Read the ⚠ DO-NOT-CONFLATE table at the top (two separate cap problems).
3. `docs/MAKER_NUMBERS_LEDGER.md` + `MAKER_MASTER_PLAN.md` §0/§0a/§0b.
4. `deploy/maker-pilot-env.staged` — the staged (NOT applied) pilot config.

Then verify §5 of the handoff yourself before believing it (read-only):
`systemctl is-active polymarket-maker-live`; heartbeat via
`journalctl -u polymarket-maker-live -n 1`; engine md5 vs branch HEAD
(`tr -d '\r' | md5sum`).

STATE IN ONE LINE: the capital deadlock is FIXED / tested (157) / 3× reviewed /
DEPLOYED to the paper arm / A/B-proven on real infra, and is now firing live on
legacy inventory (`derisk1` climbing). The pilot is re-scoped to a ~$60 /
$20-reward-qualifying-tier footprint (NOT weather — weather is uniformly $100),
with caps re-sized so the day-floor fits the wallet, and a softness probe built
to pick soft markets (share-rank ≠ pool-rank). The ONLY blocker is the
operator-provisioned DEDICATED wallet. Nothing has ever traded real money.

YOUR JOB, in order:

1. Verify the paper arm is healthy and the deployed engine md5 == branch HEAD.
   Diagnose before touching. Note whether `derisk1` is still climbing (the
   deadlock fix working through legacy one-sided inventory).
2. ASK THE OPERATOR FOR THE WALLET (§2 of the handoff): fresh dedicated wallet →
   ~$5 POL → connect on polymarket.com → deposit USDC → `MAKER_PK` + `MAKER_FUNDER`
   into `/opt/pa2-maker-live/env`. Determine `MAKER_SIG_TYPE` at sanity by testing.
   Do not engineer around the wallet.
3. Wallet in env → `scripts/maker_preflight.py --stage sanity` (read-only) → SHOW
   THE OPERATOR the output → `--stage scoring`. At scoring ALSO settle the open
   question empirically: place a $3 and a $20 order and call `is_order_scoring` on
   BOTH — measure whether sub-msz orders score, do not reason about it.
4. Before the tiny live footprint, re-run `scripts/maker_research/mm_softness_probe.py`
   a few times and average — one snapshot is noisy. Pick the softest 2-3 of the
   $20-tier candidates (FIFA-viewership `2954097` was the standout: soft AND
   big-pool). Apply `deploy/maker-pilot-env.staged` (caps already re-sized).
5. Tiny live footprint on operator go: `MAKER_SUBMIT_MODE=live` + `MAKER_LIVE_ACK`,
   the re-scoped allowlist + `MAKER_MAX_MARKETS=3`, post-only, through one 00:00Z
   reward window. First real maker fill = the settlement test; watch it and
   confirm the cancel-response shape the engine's `_cancel_shortfall` expects.
6. Next day: `--stage receipts` vs model accrual = THE FIRST VERIFIED NUMBER. Then
   `scripts/maker_onchain_recon.py --wallet <MAKER_FUNDER>`. Report both; the
   scaling decision is the operator's, on the receipt, not the model.

PARKED (propose-only, do not just do — several need OPERATOR input):
* GAP-4 cap SIZING (flat $150/market leaves 30/140 unquotable; needs an operator
  capital decision; MOOT for a sub-$150 pilot). DO NOT conflate with
  merge-blindness (FIXED) — see the tab's top table.
* Revisit `MAKER_ONESIDED_DERISK=ON` on first receipts / scaling / frequent `derisk1`.
* Active delta shaping (GAP-1); wind-down keeps reducing side (GAP-2); qh row
  consumption so paper stops over-crediting a partly-filled one-sided row (G2);
  "unknown"-sector gate hole (de-fanged for pilot by the allowlist); gate-policy
  lock (calendar-gated ≥3 clean days); reconciler→engine wiring.

STANDING DISCIPLINE (this arc paid for every word with 2 DO-NOT-SHIPs): ship
nothing to live state without tests + an INDEPENDENT adversarial review + a
first-output cross-check against a separate source + a live smoke test where
possible. Binding lessons: (1) a guard cannot be reviewed apart from its CALLER;
(2) extracting only the arithmetic RELOCATES the untested surface — extract the
whole caller; (3) a safety test that doesn't kill a deliberately-broken
implementation pins nothing — MUTATION-TEST every safety test. Money math gets
differential tests. First-output cross-checks against LIVE data caught both
classifier bugs — keep doing them. Assume one more bug exists. data-api 429s hard
— pace requests; a zeros-result on re-fetch is rate-limiting, never "empty".
Report verdict-first, plain English, every number source-tagged. If a number
looks impossible, stop and re-verify instead of presenting it.
