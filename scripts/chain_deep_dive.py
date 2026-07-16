#!/usr/bin/env python3
"""Chain-native roster-admission deep dive — nobody joins a roster without it.

WHY (operator mandate, 2026-07-14, docs/MB_STATE.md §0.3b + §5 TO-DO #1):
API/DB data is demoted to CANDIDATE-FINDING only. Before a single trader is
proposed for any shadow/copy roster, their record must be reconstructed and
re-graded on CHAIN data — the one source that cannot be faked — and pass a
four-tier gate. The audit toolchain that preceded this (audit_roster_chain,
readjudicate_discrepant) SPOT-checks a sample of a trader's API-claimed fills;
this script instead reconstructs the trader's ENTIRE dual-era fill record from
the chain and reconciles the API against it BOTH ways, then re-grades skill and
runs forensics on the chain-truth record. It is the admission gate, not a
tripwire.

THE FOUR TIERS (all read-only; RPC + CLOB GETs + DB reads; no orders, no DB
writes, no bot code):

  TIER 1 — LIFETIME FILL RECONSTRUCTION (both eras). Sweep every OrderFilled
    (V1 exchanges) and every V2 fill event (topic0 FILL_TOPIC_V2) where the
    trader is a party, across their whole active block range, on BOTH the main
    and NegRisk exchanges. V1 direction is implicit in the asset ids (USDC leg
    => BUY/SELL, audited semantics, reused from readjudicate._leg/match_fill);
    V2 carries NO direction, so each V2 fill's tx receipt is decoded with
    copy_watcher.side_from_receipt_logs (one receipt per tx, capped). Output:
    the trader's reconstructed [{token, side, price, size, usd, block, tx,
    counterparty, era}] — chain truth.

  TIER 2 — API<->CHAIN RECONCILIATION, BOTH DIRECTIONS.
    A) API -> chain: every API-claimed BUY is matched (size then price,
       readjudicate.match_fill_txexact semantics) against the reconstructed
       chain fills for its token. A size-matched chain tx at a materially
       different price is a LIE (chain wins). An API claim with NO chain
       counterpart, on a COMPLETE low-error sweep, is fabrication evidence —
       but only when it survives the exhaustive sweep (operator rule: not_found
       is an evidence gap, escalate the search, never a quota).
    B) chain -> API: reconstructed chain BUYs absent from a COMPLETE API record
       are HIDDEN activity. Hidden LOSING bets that the public record omits are
       the fraud we care about; hidden activity is only conclusive when the API
       cache is itself complete (status 'ok', not truncated/hft/partial).

  TIER 3 — SKILL RE-GRADE ON CHAIN DATA. First BUY per market (condition_id via
    the token->condition map from the API rows), edge = outcome - CHAIN price
    (chain-truth prices, not the trader's claimed prices), labels from the DB +
    gamma/CLOB resolution cache. Graded against the SAME walk-forward hire bar
    (walkforward_copy_traders.hire_ok: >= min-markets-hire resolved markets,
    prices 2-98c, span >= min-span-days, bootstrap P(edge>0) >= p-hire).

  TIER 4 — FORENSICS on the chain-truth record.
    * counterparty concentration (wash): top-counterparty share of fills.
    * maker/taker mix + TRUE LIFETIME RATE (bets/day from chain timestamps) —
      the fair HFT test the 1-2.5 day burst-page filter never ran.
    * copier-latency (optional, sampled): for sampled entries, are they
      preceded on the same token by another buyer within a short lead? If a
      trader systematically follows a leader, their alpha is the leader's and
      copying them doubles our lag (double-lag).
    * funding lineage (optional): the wallet's earliest inbound pUSD funder,
      for cross-batch sybil clustering.

PRE-REGISTERED ADMISSION RULE (locked here BEFORE any run; chain wins; evidence
gaps deepen the search, they never move a threshold — deep_dive_verdict()).
REJECT is reserved for an AFFIRMATIVE chain contradiction or a MEASURED
infeasibility; every gap or unverified suspicion is INSUFFICIENT, never an
accusation (binding operator rule 2026-07-14: not_found is an evidence gap):
  INSUFFICIENT-EVIDENCE  deepen / investigate — never admit, never accuse. Any
                         of: sweep not provably complete (rpc-error leaf
                         fraction > --max-rpc-err-frac, canary failed, or zero
                         chain fills); block-ts uncomputable; skill un-gradeable
                         (too few labelable chain first-buys) OR underpowered /
                         short-span (positive edge but P<--p-hire, or span <
                         --min-span-days — NOT disproven); too few API BUYs
                         (< --min-api-check) or thin backing (< --min-api-backing);
                         an UNVERIFIED forensic suspicion (wash concentration, or
                         the approximate copier probe -> investigate, not convict).
  REJECT                 on a COMPLETE sweep: a mismatch (size-matched chain tx
                         at a materially different price = a lie); fabrication
                         (unbacked API-BUY fraction >= --fabrication-frac); skill
                         DISPROVEN (adequately-powered AND adequately-spanning
                         NEGATIVE chain edge, P(edge<0) >= --p-hire); or a true
                         chain rate > --hft-max-rate (mechanically un-tailable —
                         a measured fact, not an accusation).
  ADMIT                  COMPLETE sweep AND zero mismatch AND >= --min-api-check
                         API BUYs with backing >= --min-api-backing AND
                         chain-graded skill CLEARS the hire bar AND no forensic
                         flag. Admission is a PROPOSAL to the operator for a
                         cohort — never an auto-add, never pooled with an
                         existing cohort's readout.
  (Direction-B hidden-activity is REPORTED for operator review, not a verdict
  gate: size+price-exact chain-vs-API reconciliation is noisy across fill
  granularity, so an auto-gate would risk a false accusation. A granularity-
  aware hidden-activity gate is a documented follow-up.)

SAFETY: READ-ONLY everywhere. RPC GETs, CLOB public GETs, two indexed DB reads.
No DB writes, no orders, no shared-module edits (blockchain_client constants +
Database + PolymarketClient are imported and used exactly as the sibling
scripts do). Per-trader results written atomically; a batch summary aggregates.

INVOCATION (VPS, from a /tmp clone refreshed to this branch head — NEVER the
deployed tree; new-DB-runner rule: needs the shared .env sourced AND db.init,
so smoke it on ONE trader first):
    set -a; . /opt/pa2-shared/.env; set +a
    cd /opt/polymarket-ai-v2 && sudo -u polymarket env PYTHONPATH=/tmp/mbre \
      DATABASE_URL="$DATABASE_URL" \
      venv/bin/python /tmp/mbre/scripts/chain_deep_dive.py \
      --readjudicate-json /opt/pa2-shared/mb_copyable_data/readjudicate.json \
      --cache /opt/pa2-shared/mb_copyable_data/copyable_cache \
      --gamma-cache /opt/pa2-shared/mb_copyable_data/copyable_cache/gamma_resolutions.json \
      --rpc-url https://polygon.gateway.tenderly.co \
      --out-dir /tmp/deep_dive --limit 1 | tee /tmp/deep_dive_smoke.log   # SMOKE
    ... (drop --limit for the full batch) ...
    ... --self-test    # offline: every tier's pure core + the verdict table
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import audit_roster_chain as ac  # noqa: E402  (audited chain helpers, as-is)
import find_copyable_traders as fc  # noqa: E402  (tested shared helpers)
import readjudicate_discrepant as rj  # noqa: E402  (tx-exact matcher, dual-era)
import walkforward_copy_traders as wf  # noqa: E402  (the hire bar itself)
from mirror_v3.copy_watcher import (  # noqa: E402  (receipt-validated V2 kit)
    EXCHANGE_V2, FILL_TOPIC_V2, NEGRISK_EXCHANGE_V2, SCALE, USDC_ASSET_ID,
    _hex, _topic_addr, _words, addr_topic, side_from_receipt_logs)

PUSD_CONTRACT = "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb"  # V2 collateral
T20_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


# ── Pure, offline-testable core ──────────────────────────────────────────────
def v1_reconstruct_fill(args: dict, trader: str) -> Optional[dict]:
    """One V1 OrderFilled event -> a reconstructed fill for `trader` (BUY OR
    SELL), or None if the trader is not a party. Asset semantics are IDENTICAL
    to the audited match_fill / readjudicate._leg (a USDC leg names the price
    side); direction is inferred from which side of the trade holds USDC.
      maker, makerAsset==USDC  -> pays USDC  -> BUY takerAsset
      maker, takerAsset==USDC  -> receives USDC -> SELL makerAsset
      taker, takerAsset==USDC  -> pays USDC  -> BUY makerAsset
      taker, makerAsset==USDC  -> receives USDC -> SELL takerAsset
    (usd, tokens) are 6-decimal-scaled to units.

    counterparty is DELIBERATELY None for V1: Polymarket's CTF Exchange is
    onlyOperator (matchOrders emits every leg with taker = the Exchange
    contract / operator, never a real order owner), so the OrderFilled `taker`
    field is NOT a counterparty and using it for wash detection would flag
    every operator-matched trader (review 2026-07-14 finding D). Only V2
    maker-side fills name a real counterparty (topics[3])."""
    a = trader.lower()
    mk = str(args.get("maker", "")).lower()
    tk = str(args.get("taker", "")).lower()
    m_id, t_id = int(args.get("makerAssetId", -1)), int(args.get("takerAssetId", -1))
    m_amt = float(args.get("makerAmountFilled", 0))
    t_amt = float(args.get("takerAmountFilled", 0))
    if a == mk:
        if m_id == USDC_ASSET_ID and t_id != USDC_ASSET_ID:
            token, side, usd, tok = t_id, "BUY", m_amt, t_amt
        elif t_id == USDC_ASSET_ID and m_id != USDC_ASSET_ID:
            token, side, usd, tok = m_id, "SELL", t_amt, m_amt
        else:
            return None
    elif a == tk:
        if t_id == USDC_ASSET_ID and m_id != USDC_ASSET_ID:
            token, side, usd, tok = m_id, "BUY", t_amt, m_amt
        elif m_id == USDC_ASSET_ID and t_id != USDC_ASSET_ID:
            token, side, usd, tok = t_id, "SELL", m_amt, t_amt
        else:
            return None
    else:
        return None
    if tok <= 0 or usd <= 0:
        return None
    return {"token_id": str(token), "side": side, "usd": usd / SCALE,
            "tokens": tok / SCALE, "price": (usd / SCALE) / (tok / SCALE),
            "counterparty": None, "era": "v1", "maker": a == mk,
            "tx": str(args.get("_tx", "")), "block": int(args.get("_block", 0))}


def v2_reconstruct_fill(lg: dict, trader: str) -> Optional[dict]:
    """One raw V2 fill log -> a reconstructed fill for `trader`, direction
    PENDING (side=None until a receipt is decoded). Layout is copy_watcher's
    receipt-validated one: topics[2]=owner, data=[?, token, usdc, tokens,...].
    counterparty = topics[3] address unless it is the exchange (taker-side
    aggregate), in which case it is None (no single counterparty is named)."""
    topics = lg.get("topics") or []
    if len(topics) < 3 or _hex(topics[0]).lower() != FILL_TOPIC_V2:
        return None
    if _topic_addr(topics[2]) != trader.lower():
        return None
    w = _words(lg.get("data", "0x"))
    if len(w) < 4 or w[2] <= 0 or w[3] <= 0:
        return None
    exch = str(lg.get("address", "")).lower()
    cp = _topic_addr(topics[3]) if len(topics) > 3 else None
    was_taker = cp == exch
    txh = lg.get("transactionHash", "")
    return {"token_id": str(w[1]), "side": None, "usd": w[2] / SCALE,
            "tokens": w[3] / SCALE, "price": (w[2] / SCALE) / (w[3] / SCALE),
            "counterparty": None if was_taker else cp, "era": "v2",
            "was_taker": was_taker,
            "tx": txh.hex() if hasattr(txh, "hex") else str(txh),
            "block": int(lg.get("blockNumber", 0))}


def reconstruct_first_buys(fills: list[dict], tok2cond: dict[str, str]
                           ) -> list[dict]:
    """First BUY per MARKET (condition_id where known, else token id) from the
    reconstructed record, earliest block wins — the walk-forward estimand on
    chain data. SELLs and direction-unknown fills are not first-buys."""
    first: dict[str, dict] = {}
    for f in sorted(fills, key=lambda x: (int(x.get("block", 0)), str(x.get("tx", "")))):
        if f.get("side") != "BUY":
            continue
        key = tok2cond.get(f["token_id"], "tok:" + f["token_id"])
        if key not in first:
            first[key] = {**f, "market_key": key}
    return list(first.values())


def reconcile_api_to_chain(api_buys: list[dict], chain_fills: list[dict],
                           tol_price: float, tol_size: float) -> dict:
    """Direction A. Each API BUY (token, price, size) is matched against the
    reconstructed chain BUYs for THAT token, using the tx-exact matcher (size
    then nearest price; never blends across txs). Chain wins.

    Only chain fills whose reconstructed side is BUY are candidates — the
    direction Tier 1 worked out (V1 implicit, V2 receipt-decoded) is PRESERVED,
    never re-inferred. Folding SELLs / direction-unknown V2 fills into the
    candidate pool would let an API BUY 'verify' against a SELL (masking a
    price lie) or size-match a SELL at a wrong price (a false 'LIE' REJECT) —
    both defeat the chain-wins adjudication (review 2026-07-14 findings A/#1,2,
    10,11). match_fill_txexact still receipt-shape-checks the leg semantics.
    Returns counts + the mismatch rows (size-matched, materially off price)."""
    by_tok: dict[str, list[dict]] = {}
    for f in chain_fills:
        if f.get("side") == "BUY":  # direction preserved; SELL/None never corroborate a BUY
            by_tok.setdefault(f["token_id"], []).append(f)
    counts = {"verified": 0, "mismatch": 0, "not_found": 0}
    mismatches: list[dict] = []
    for r in api_buys:
        tok = str(r.get("tokenId") or "")
        if not tok.isdigit():
            counts["not_found"] += 1  # non-numeric token can't join the chain side
            continue
        cand = by_tok.get(tok, [])
        # shape chain fills as match_fill_txexact events (maker BUY of `tok`)
        events = [{"maker": "0xowner", "taker": "0x0",
                   "makerAssetId": USDC_ASSET_ID, "takerAssetId": int(tok),
                   "makerAmountFilled": f["usd"] * SCALE,
                   "takerAmountFilled": f["tokens"] * SCALE, "_tx": f.get("tx", "")}
                  for f in cand if f["token_id"] == tok and int(tok) > 0]
        m = rj.match_fill_txexact(events, "0xowner", int(tok) if tok.isdigit() else 0,
                                  "BUY", float(r.get("price", 0)),
                                  float(r.get("size", 0)), tol_price, tol_size)
        if m["status"] == "verified_txexact":
            counts["verified"] += 1
        elif m["status"] == "mismatch_txexact":
            counts["mismatch"] += 1
            mismatches.append({"token": tok, "api_price": r.get("price"),
                               "api_size": r.get("size"),
                               "chain_price": round(m["chain_price"], 4),
                               "tx": m["tx"]})
        else:
            counts["not_found"] += 1
    n = sum(counts.values())
    counts["n"] = n
    counts["backing"] = (counts["verified"] / n) if n else 0.0
    return {"counts": counts, "mismatches": mismatches}


def window_api_buys(api_trades: list[dict], lo, hi) -> list[dict]:
    """API BUY rows (numeric token) whose _ts is inside the SWEPT window
    [lo, hi] (naive-UTC datetimes). Reconciling API BUYs OUTSIDE the swept
    block range against a partial chain sweep manufactures false not_found ->
    a false FABRICATION verdict (smoke 2026-07-14 flagged a 99% artifact; also
    the general form of review finding H — a narrow/stale head must never
    invent unbacked claims). For a default full sweep (from = first_api - pad)
    this includes the whole history and is a no-op."""
    return [t for t in api_trades
            if str(t.get("side", "")).upper() == "BUY"
            and str(t.get("tokenId", "")).isdigit()
            and t.get("_ts") is not None and lo <= t["_ts"] <= hi]


def reconcile_chain_to_api(chain_buys: list[dict], api_rows: list[dict],
                           tol_price: float, tol_size: float) -> dict:
    """Direction B. Reconstructed chain BUYs with no size+price API row for
    the same token = HIDDEN activity. Only conclusive when the API record is
    complete (caller gates on cache status)."""
    api_by_tok: dict[str, list[dict]] = {}
    for r in api_rows:
        api_by_tok.setdefault(str(r.get("tokenId") or ""), []).append(r)
    hidden = 0
    hidden_rows: list[dict] = []
    for f in chain_buys:
        tok = f["token_id"]
        rows = api_by_tok.get(tok, [])
        matched = any(
            (r.get("size") and abs(float(r["size"]) - f["tokens"]) <= tol_size * f["tokens"]
             and abs(float(r.get("price", 0)) - f["price"]) <= tol_price)
            for r in rows)
        if not matched:
            hidden += 1
            if len(hidden_rows) < 25:
                hidden_rows.append({"token": tok, "chain_price": round(f["price"], 4),
                                    "chain_size": round(f["tokens"], 2),
                                    "tx": f.get("tx", "")})
    return {"n_chain_buys": len(chain_buys), "hidden": hidden,
            "hidden_rows": hidden_rows}


def counterparty_concentration(fills: list[dict]) -> dict:
    """Top-counterparty share of fills that name a counterparty (wash signal)."""
    cps: dict[str, int] = {}
    for f in fills:
        cp = f.get("counterparty")
        if cp and cp not in ("0x0", "0x" + "0" * 40):
            cps[cp] = cps.get(cp, 0) + 1
    named = sum(cps.values())
    if named == 0:
        return {"named_fills": 0, "top_share": 0.0, "top_cp": None,
                "n_counterparties": 0}
    top_cp = max(cps, key=cps.get)
    return {"named_fills": named, "top_share": cps[top_cp] / named,
            "top_cp": top_cp, "n_counterparties": len(cps)}


def maker_taker_profile(fills: list[dict]) -> dict:
    """maker vs taker fraction over fills where the role is known (V1: which
    leg the trader is; V2: was_taker)."""
    maker = taker = 0
    for f in fills:
        if f["era"] == "v1":
            is_maker = bool(f.get("maker"))
        elif f["era"] == "v2":
            is_maker = not f.get("was_taker")
        else:
            continue
        if is_maker:
            maker += 1
        else:
            taker += 1
    n = maker + taker
    return {"maker": maker, "taker": taker,
            "maker_frac": (maker / n) if n else 0.0}


def true_rate_per_day(n_fills: int, span_days: float) -> float:
    return n_fills / max(span_days, 1e-9) if n_fills else 0.0


def interp_ts(block: int, b_lo: int, ts_lo: int, b_hi: int, ts_hi: int
              ) -> datetime:
    """Linear ts for a block from two verified (block, ts) endpoints — avoids a
    getBlock per fill. Degenerate single-point range falls back to ts_lo."""
    if b_hi <= b_lo:
        ep = ts_lo
    else:
        ep = ts_lo + (ts_hi - ts_lo) * (block - b_lo) / (b_hi - b_lo)
    return datetime.fromtimestamp(int(ep), tz=timezone.utc).replace(tzinfo=None)


def chain_skill_grade(first_buys: list[dict], markets: dict, tok2cond: dict,
                      b_lo: int, ts_lo: int, b_hi: int, ts_hi: int, cfg,
                      seed: int) -> dict:
    """Tier 3. Label each chain first-BUY, build hire-bar evidence with edges
    from CHAIN prices, apply the exact walk-forward hire bar at the record's
    end. Returns coverage + the hire-bar result AND a `contradicts` flag.

    `clears_bar` (wf.hire_ok) is the ADMIT signal. `contradicts` is the REJECT
    signal and is TRUE only when the record AFFIRMATIVELY disproves skill: an
    adequately-powered, adequately-spanning set (>= min-markets-hire markets,
    span >= min-span-days) whose mean edge is negative AND bootstrap
    P(edge<0) >= p-hire. hire_ok also returns False for evidence GAPS (span
    too short, or positive-but-underpowered P<p-hire) — those must route to
    INSUFFICIENT, never REJECT (operator rule: a gap is not an accusation;
    walk-forward FIRE semantics — not-hired != fired). Review 2026-07-14 finding B."""
    evidence: list[dict] = []
    n_labeled = n_unlabelable = 0
    for f in first_buys:
        cond = tok2cond.get(f["token_id"])
        mkt = markets.get(cond) if cond else None
        o = fc.label_outcome(f["token_id"], "BUY", mkt)
        if o is None:
            n_unlabelable += 1
            continue
        n_labeled += 1
        ts = interp_ts(int(f.get("block", 0)), b_lo, ts_lo, b_hi, ts_hi)
        evidence.append({"_ts": ts, "known": ts, "marketId": cond,
                         "edge": o - f["price"]})
    result = {"n_first_buys": len(first_buys), "n_labeled": n_labeled,
              "n_unlabelable": n_unlabelable, "clears_bar": False,
              "contradicts": False, "n_markets": 0, "span_days": 0.0,
              "edge": None, "p": None, "p_neg": None}
    if not evidence:
        return result
    t_end = max(e["_ts"] for e in evidence) + timedelta(days=1)
    me = fc.per_market_edges([(e["marketId"], e["edge"]) for e in evidence])
    p_pos, mean, _ = fc.boot_stats(me, cfg.n_boot_roster, seed)
    p_neg, _, _ = fc.boot_stats([-x for x in me], cfg.n_boot_roster, seed + 1)
    span = (max(e["_ts"] for e in evidence) - min(e["_ts"] for e in evidence)).days
    adequately_powered = (len(me) >= cfg.min_markets_hire
                          and span >= cfg.min_span_days)
    result.update({"n_markets": len(me), "span_days": span, "edge": mean,
                   "p": p_pos, "p_neg": p_neg,
                   "clears_bar": wf.hire_ok(evidence, t_end, cfg, seed),
                   "contradicts": adequately_powered and mean < 0
                   and p_neg >= cfg.p_hire})
    return result


def deep_dive_verdict(m: dict, cfg) -> tuple[str, list[str]]:
    """PRE-REGISTERED admission rule (module docstring). `m` carries the
    computed metrics; thresholds come from cfg and are NEVER moved during a
    run. Evidence gaps deepen the search (INSUFFICIENT), they never accuse."""
    sweep_complete = (m["rpc_err_frac"] <= cfg.max_rpc_err_frac and m["canary_ok"]
                      and m["n_chain_fills"] > 0)
    if not sweep_complete:
        return "INSUFFICIENT-EVIDENCE", [
            f"sweep not provably complete: rpc_err_frac={m['rpc_err_frac']:.2f}"
            f" (max {cfg.max_rpc_err_frac}), canary_ok={m['canary_ok']}, "
            f"chain_fills={m['n_chain_fills']} — widen range / second RPC, "
            f"never admit or accuse"]

    # UNCOPYABLE is a measured fill-RATE fact (count / span) — it needs no V2
    # direction receipts, so it is judged on the getLogs-complete sweep BEFORE
    # the direction gate: a genuinely un-tailable HFT account rejects fast
    # without paying for full receipts.
    if m["rate_flag"]:
        return "REJECT", [
            f"UNCOPYABLE: true chain rate {m['true_rate']:.0f} bets/day > cap "
            f"{cfg.hft_max_rate:.0f} (mechanically un-tailable — a measured "
            f"fact, not an accusation)"]

    # mismatch / fabrication / skill all need the BUY set fully resolved; if the
    # V2 receipt cap truncated direction classification, DEFER — a capped sweep
    # reads real BUYs as unknown and would manufacture false not_found
    # (smoke 2026-07-14). Response: raise --max-receipts (deepen), never accuse.
    if not m["direction_complete"]:
        return "INSUFFICIENT-EVIDENCE", [
            f"V2 direction incompletely resolved: {m['v2_receipts']} of "
            f"{m['v2_txs']} V2 txs attempted, {m.get('receipts_failed', 0)} "
            f"receipt fetches FAILED after retry — raise --max-receipts / "
            f"retry on a healthier RPC before adjudicating direction-"
            f"dependent tiers (an RPC gap must never read as fabrication)"]

    backing = m["api_backing"]
    checked = m["api_buys_checked"] >= cfg.min_api_check

    # HARD (REJECT): the chain AFFIRMATIVELY contradicts the trader — never a
    # mere evidence gap. Unverified forensic suspicions (wash/copier) are NOT
    # here; they route to INSUFFICIENT below (review 2026-07-14 findings
    # D/copier: an unverified signal is 'investigate', never an accusation).
    hard: list[str] = []
    if m["mismatch"] > 0:
        hard.append(f"LIE: {m['mismatch']} size-matched chain tx at a materially "
                    f"different price than the API claim (chain wins)")
    if checked and (1 - backing) >= cfg.fabrication_frac:
        hard.append(f"FABRICATION: {1 - backing:.0%} of {m['api_buys_checked']} "
                    f"API BUY claims unbacked on a complete sweep "
                    f"(>= {cfg.fabrication_frac:.0%})")
    if m["skill_contradicts"]:
        hard.append(f"SKILL DISPROVEN: adequately-powered negative chain edge "
                    f"(mkts={m['skill_markets']}, span={m['skill_span']}d, "
                    f"edge={m['skill_edge']}, P(edge<0)={m['skill_p_neg']})")
    if hard:
        return "REJECT", hard

    # SOFT (INSUFFICIENT-EVIDENCE): evidence gaps + UNVERIFIED forensic flags.
    # Deepen the search / investigate — never accuse, never admit.
    soft: list[str] = []
    if not m["skill_gradeable"]:
        soft.append(f"skill un-gradeable: {m['skill_labeled']} labelable chain "
                    f"first-buys / ts-computable={m['ts_ok']} — widen resolution "
                    f"coverage / API cache / retry block-ts")
    elif not m["skill_clears"]:
        soft.append(f"skill underpowered/short-span, not disproven: "
                    f"mkts={m['skill_markets']}, span={m['skill_span']}d, "
                    f"P(edge>0)={m['skill_p']} — deepen (more resolved markets)")
    if not checked:
        soft.append(f"too few API BUYs ({m['api_buys_checked']} < "
                    f"{cfg.min_api_check}) to corroborate the claimed record — "
                    f"--refresh a full history / wider range")
    elif backing < cfg.min_api_backing:
        soft.append(f"reconciliation thin: {backing:.0%} of {m['api_buys_checked']} "
                    f"API BUYs chain-backed (< {cfg.min_api_backing:.0%}) — not a "
                    f"lie, not fully corroborated; --refresh cache / wider range")
    if m["wash_flag"]:
        soft.append(f"WASH SUSPECT (investigate, not auto-reject): top V2 "
                    f"counterparty {m['wash_share']:.0%} of {m['wash_named']} "
                    f"named-counterparty fills (>= {cfg.wash_share:.0%})")
    if m["copier_flag"]:
        soft.append(f"COPIER SUSPECT (investigate): {m['copier_frac']:.0%} of "
                    f"sampled entries preceded by another party on the same token "
                    f"within {cfg.copier_lead_s}s (approximate double-lag signal)")
    if soft:
        return "INSUFFICIENT-EVIDENCE", soft

    return "ADMIT", [
        f"complete sweep, 0 mismatch, {backing:.0%} of {m['api_buys_checked']} "
        f"API-BUYs chain-backed (>= {cfg.min_api_backing:.0%}), chain skill "
        f"clears (mkts={m['skill_markets']}, P={m['skill_p']}), no forensic flag "
        f"— PROPOSED to operator for a cohort (own start date, separate readout)"]


def roster_from_readjudicate(blob: dict) -> list[str]:
    """Cohort-2 candidates = VINDICATED addresses from a readjudicate*.json."""
    return sorted(a.lower() for a in (blob.get("vindicated") or []))


def write_summary_from_dir(out_dir: str, out_path: str,
                           params: Optional[dict] = None) -> dict:
    """Rebuild the batch summary from the ON-DISK per-trader JSONs and write it
    atomically. Called after every trader AND at batch end, so the summary is
    crash-durable and automatically spans ALL runs sharing out_dir (session-
    close finding I: run-1 died before its single end-of-run summary write,
    leaving no aggregate; run-2's in-memory dict only covered its own roster)."""
    results: dict[str, dict] = {}
    for fn in sorted(os.listdir(out_dir)):
        if not (fn.startswith("0x") and fn.endswith(".json")):
            continue
        try:
            with open(os.path.join(out_dir, fn)) as f:
                r = json.load(f)
            results[r.get("address", fn[:-5]).lower()] = r
        except Exception:  # noqa: BLE001  (torn/foreign file never kills the batch)
            continue
    counts: dict[str, int] = {}
    for r in results.values():
        counts[r.get("verdict", "?")] = counts.get(r.get("verdict", "?"), 0) + 1
    admitted = sorted(a for a, r in results.items() if r.get("verdict") == "ADMIT")
    by_funder: dict[str, list[str]] = {}
    for a, r in results.items():
        fu = (r.get("tier4_forensics") or {}).get("funder")
        if fu:
            by_funder.setdefault(fu, []).append(a)
    summary = {"n_traders": len(results), "counts": counts, "admitted": admitted,
               "sybil": {fu: ad for fu, ad in by_funder.items() if len(ad) > 1},
               "results": results}
    if params is not None:
        summary["params"] = params
    fc.write_json_atomic(out_path, fc.json_safe(summary))
    return summary


def _merge_gamma_preloaded(markets: dict, keys: list[str], gamma: dict) -> int:
    """fc.merge_gamma_cache with the gamma dict PRELOADED (avoid re-reading the
    ~70k-key resolutions file once per trader — review finding L). Merge
    semantics kept identical to fc.merge_gamma_cache: DB wins; fill only holes
    with a definitive YES/NO gamma resolution."""
    added = 0
    for k in keys:
        if (markets.get(k) or {}).get("resolution") or k not in gamma:
            continue
        g = gamma[k]
        if g.get("resolution") not in ("YES", "NO"):
            continue
        markets[k] = {"resolution": g["resolution"],
                      "resolved_at": g.get("resolved_at"),
                      "yes_token_id": g.get("yes_token_id"),
                      "no_token_id": g.get("no_token_id"),
                      "category": g.get("category") or ""}
        added += 1
    return added


# ── Network run (VPS) ────────────────────────────────────────────────────────
async def _sweep_leaf(fetch: Callable, lo: int, hi: int, chunk: int,
                      min_chunk: int, rps: float, note_err, counters: dict
                      ) -> None:
    """Adaptive getLogs sweep: try `chunk`-sized ranges; on error bisect down to
    min_chunk (topic-filtered queries are sparse, so large chunks usually
    serve; a range/result cap is recovered by halving). Only a failure AT
    min_chunk is an unrecoverable leaf — that is what rpc_err_frac counts, so a
    transiently-large window doesn't inflate the incompleteness signal."""
    for a, b in ac.block_chunks(lo, hi, chunk):
        await asyncio.sleep(1.0 / max(rps, 0.1))
        try:
            counters["logs"].extend(await fetch(a, b))
            counters["leaf_ok"] += 1
        except Exception as e:  # noqa: BLE001
            note_err(e)
            if (b - a + 1) > min_chunk:
                half = max(min_chunk, (b - a) // 2 + 1)
                await _sweep_leaf(fetch, a, (a + b) // 2, half, min_chunk, rps,
                                  note_err, counters)
                await _sweep_leaf(fetch, (a + b) // 2 + 1, b, half, min_chunk, rps,
                                  note_err, counters)
            else:
                counters["leaf_fail"] += 1


async def sweep_lifetime(bc, addr: str, from_b: int, to_b: int, cfg,
                         note_err) -> dict:
    """Tier 1 SWEEP ONLY (NO receipts): all V1 OrderFilled (both exchanges,
    maker+taker) + all V2 fills (owner-topic, both exchanges) in [from_b, to_b],
    decoded. V1 fills carry direction (asset semantics); V2 fills come back
    side=None until classify_v2_directions resolves them from receipts. Split
    from the receipt phase so an UNCOPYABLE rate (a fill-count fact needing NO
    direction) can short-circuit BEFORE the dominant receipt fetch."""
    from base_engine.data.blockchain_client import (
        EXCHANGE_CONTRACT, NEGRISK_EXCHANGE_CONTRACT, ORDER_FILLED_EVENT_ABI)
    from web3 import Web3

    addr_cs = Web3.to_checksum_address(addr)
    v1_contracts = [bc.w3.eth.contract(
        address=Web3.to_checksum_address(c), abi=[ORDER_FILLED_EVENT_ABI])
        for c in dict.fromkeys((EXCHANGE_CONTRACT, NEGRISK_EXCHANGE_CONTRACT))]
    v2_addrs = [Web3.to_checksum_address(a)
                for a in (EXCHANGE_V2, NEGRISK_EXCHANGE_V2)]

    counters = {"logs": [], "leaf_ok": 0, "leaf_fail": 0}
    v1_fills: list[dict] = []
    for c in v1_contracts:
        for field in ("maker", "taker"):
            async def _fetch(a, b, _c=c, _f=field):
                evs = await ac.get_logs_compat(_c.events.OrderFilled, a, b,
                                               {_f: addr_cs})
                out = []
                for e in evs:
                    d = dict(e["args"])
                    txh = e.get("transactionHash", "")
                    d["_tx"] = txh.hex() if hasattr(txh, "hex") else str(txh)
                    d["_block"] = int(e.get("blockNumber", 0))
                    out.append(d)
                return out
            base = len(counters["logs"])
            await _sweep_leaf(_fetch, from_b, to_b, cfg.chunk_blocks,
                              cfg.min_chunk, cfg.rps, note_err, counters)
            for d in counters["logs"][base:]:
                f = v1_reconstruct_fill(d, addr)
                if f is not None:
                    v1_fills.append(f)
            counters["logs"] = counters["logs"][:base]  # decoded; free memory

    async def _fetch_v2(a, b):
        return await bc.w3.eth.get_logs({
            "fromBlock": a, "toBlock": b, "address": v2_addrs,
            "topics": [FILL_TOPIC_V2, None, addr_topic(addr)]})
    base = len(counters["logs"])
    await _sweep_leaf(_fetch_v2, from_b, to_b, cfg.chunk_blocks, cfg.min_chunk,
                      cfg.rps, note_err, counters)
    v2_fills = []
    for lg in counters["logs"][base:]:
        f = v2_reconstruct_fill(dict(lg), addr)
        if f is not None:
            v2_fills.append(f)

    total_leaves = counters["leaf_ok"] + counters["leaf_fail"]
    return {"v1_fills": v1_fills, "v2_fills": v2_fills,
            "leaf_ok": counters["leaf_ok"], "leaf_fail": counters["leaf_fail"],
            "rpc_err_frac": (counters["leaf_fail"] / total_leaves) if total_leaves else 1.0}


async def classify_v2_directions(bc, addr: str, v2_fills: list[dict], cfg,
                                 note_err) -> dict:
    """Resolve V2 BUY/SELL direction from tx receipts (one per unique tx, capped
    at --max-receipts); sets f['side'] IN PLACE. Uncapped/errored fills stay
    side=None and are excluded from BUY-based tiers (never guessed BUY). This is
    the batch's dominant cost, so it runs only AFTER the rate short-circuit.

    Returns SUCCESS counts, not attempt counts (session-close review 2026-07-15
    findings B: an errored receipt left side=None but still counted as
    'resolved', so a flaky-RPC stretch could manufacture false not_found ->
    a FABRICATION accusation from an evidence gap). Failed receipts are retried
    once; what still fails is reported as receipts_failed and gates
    direction_complete in the caller."""
    tx_logs: dict[str, Optional[list]] = {}
    uniq_tx = list(dict.fromkeys(f["tx"] for f in v2_fills))
    n_receipts = min(len(uniq_tx), cfg.max_receipts)
    attempt = list(uniq_tx[:n_receipts])
    for rnd in (1, 2):  # second round = one retry over transient failures
        failed: list[str] = []
        for i, tx in enumerate(attempt):
            await asyncio.sleep(1.0 / max(cfg.rps, 0.1))
            try:
                rcpt = await bc.w3.eth.get_transaction_receipt(tx)
                tx_logs[tx] = [dict(lg) for lg in rcpt["logs"]]
            except Exception as e:  # noqa: BLE001
                note_err(e)
                tx_logs[tx] = None
                failed.append(tx)
            if rnd == 1 and i and i % 2000 == 0:  # heartbeat (finding J)
                print(f"      … receipts {i}/{len(attempt)} "
                      f"({len(failed)} failed so far)", file=sys.stderr)
        if not failed:
            break
        attempt = failed  # retry only the failures, once
    for f in v2_fills:
        logs = tx_logs.get(f["tx"])
        f["side"] = (side_from_receipt_logs(logs, addr, f["token_id"])
                     if isinstance(logs, list) else None)
    receipts_ok = sum(1 for v in tx_logs.values() if isinstance(v, list))
    return {"v2_txs": len(uniq_tx), "v2_receipts": n_receipts,
            "receipts_ok": receipts_ok,
            "receipts_failed": n_receipts - receipts_ok}


async def detection_canary(bc, head: int) -> bool:
    """Cheap 'the RPC serves V2 fill logs' check (copy_watcher canary shape):
    a settled 20-block window must hold SOME fills (Polymarket never sleeps)."""
    from web3 import Web3
    v2_addrs = [Web3.to_checksum_address(a)
                for a in (EXCHANGE_V2, NEGRISK_EXCHANGE_V2)]
    b1 = head - 60
    try:
        logs = await bc.w3.eth.get_logs({"fromBlock": b1 - 20, "toBlock": b1,
                                         "address": v2_addrs,
                                         "topics": [FILL_TOPIC_V2]})
        return len(logs) > 0
    except Exception:  # noqa: BLE001
        return False


async def find_funder(bc, addr: str, from_b: int, to_b: int, cfg,
                      note_err) -> Optional[str]:
    """Earliest inbound pUSD transfer's sender (best-effort sybil lineage)."""
    from web3 import Web3
    earliest_block = None
    earliest_from = None

    async def _fetch(a, b):
        return await bc.w3.eth.get_logs({
            "fromBlock": a, "toBlock": b,
            "address": Web3.to_checksum_address(PUSD_CONTRACT),
            "topics": [T20_TRANSFER, None, addr_topic(addr)]})
    counters = {"logs": [], "leaf_ok": 0, "leaf_fail": 0}
    await _sweep_leaf(_fetch, from_b, to_b, cfg.chunk_blocks, cfg.min_chunk,
                      cfg.rps, note_err, counters)
    for lg in counters["logs"]:
        blk = int(lg.get("blockNumber", 0))
        topics = lg.get("topics") or []
        if len(topics) >= 3 and (earliest_block is None or blk < earliest_block):
            earliest_block = blk
            earliest_from = _topic_addr(topics[1])
    return earliest_from


async def copier_probe(bc, first_buys: list[dict], addr: str, cfg, note_err
                       ) -> dict:
    """Tier 4 (sampled, APPROXIMATE): for K entries, is the same token filled by
    ANOTHER party in the short window before the trader's entry? A systematic
    follower's alpha is really the leader's, and copying a copier doubles our
    lag. NOTE: V2 fills carry no direction without a receipt, so this counts
    any earlier same-token fill by another owner (buy OR sell) — a lead
    indicator, not proof; a flag here means 'investigate', gated by --copier-frac."""
    from web3 import Web3
    v2_addrs = [Web3.to_checksum_address(a)
                for a in (EXCHANGE_V2, NEGRISK_EXCHANGE_V2)]
    lead_blocks = max(1, int(cfg.copier_lead_s / 2.1))
    sample = [f for f in first_buys if f["era"] == "v2"][:cfg.copier_sample]
    preceded = 0
    for f in sample:
        blk = int(f["block"])
        await asyncio.sleep(1.0 / max(cfg.rps, 0.1))
        try:
            logs = await bc.w3.eth.get_logs({
                "fromBlock": blk - lead_blocks, "toBlock": blk,
                "address": v2_addrs, "topics": [FILL_TOPIC_V2]})
        except Exception as e:  # noqa: BLE001
            note_err(e)
            continue
        for lg in logs:
            topics = lg.get("topics") or []
            if len(topics) < 3 or int(lg.get("blockNumber", 0)) >= blk:
                continue
            if _topic_addr(topics[2]) == addr.lower():
                continue  # the trader's own earlier fill doesn't count
            w = _words(lg.get("data", "0x"))
            if len(w) >= 2 and str(w[1]) == f["token_id"]:
                preceded += 1
                break
    n = len(sample)
    return {"sampled": n, "preceded": preceded,
            "frac": (preceded / n) if n else 0.0}


async def deep_dive_one(bc, addr: str, cache_blob: dict, cache_status: str,
                        markets_loader, cfg) -> dict:
    """Full four-tier deep dive for one trader. markets_loader(cids)->markets
    dict (async) is injected so the DB session lifetime is caller-owned. The
    chain head is fetched PER TRADER (not threaded from batch start) so a
    late-in-batch sweep never ends at a stale head (review finding H)."""
    first_err: list[str] = []

    def note_err(e):
        if not first_err:
            first_err.append(repr(e))

    api_trades = cache_blob.get("trades", []) if isinstance(cache_blob, dict) else (cache_blob or [])
    for t in api_trades:
        t["_ts"] = fc.parse_ts(t.get("timestamp"))
    api_ts = sorted(t["_ts"] for t in api_trades if t.get("_ts"))
    if not api_ts:
        return {"address": addr, "verdict": "INSUFFICIENT-EVIDENCE",
                "reasons": ["no API history to bound the sweep or reconcile "
                            "against — fetch a history first (--refresh)"],
                "cache_status": cache_status}

    anchors: list[tuple[int, int]] = []

    async def _get_block_ts(b):
        await asyncio.sleep(1.0 / max(cfg.rps, 0.1))
        return int((await bc.w3.eth.get_block(b))["timestamp"])

    try:
        latest = await bc.w3.eth.get_block("latest")
        latest_num, latest_ts = int(latest["number"]), int(latest["timestamp"])
    except Exception as e:  # noqa: BLE001
        note_err(e)
        return {"address": addr, "verdict": "INSUFFICIENT-EVIDENCE",
                "reasons": [f"could not fetch chain head: {e!r}"],
                "cache_status": cache_status,
                "first_error": first_err[0] if first_err else None}

    # Block range low bound. For a COMPLETE ('ok') API history, the first
    # API-claimed fill (minus --pad-days) bounds the sweep. For an INCOMPLETE
    # cache (hft/truncated/partial/missing) the shallow API page's first ts is
    # NOT the trader's true start — bounding by it would sweep only the last
    # ~1-2 days and defeat Tier 4's fair lifetime-rate test on exactly the 34
    # HFT-borderline. So incomplete caches sweep from --floor-date. An explicit
    # --from-date always overrides. (Pre-first-claim hidden activity on a
    # complete history still needs --from-date — documented scope limit.)
    floor_dt = fc.parse_ts(cfg.floor_date)
    floor_epoch = int(floor_dt.replace(tzinfo=timezone.utc).timestamp())
    from_dt = fc.parse_ts(cfg.from_date) if cfg.from_date else None
    incomplete_cache = cache_status in ("hft", "truncated", "partial", "missing")
    if from_dt is not None:
        low_epoch = int(from_dt.replace(tzinfo=timezone.utc).timestamp())
    elif incomplete_cache:
        low_epoch = floor_epoch
    else:
        low_epoch = int((api_ts[0] - timedelta(days=cfg.pad_days))
                        .replace(tzinfo=timezone.utc).timestamp())
    low_epoch = max(low_epoch, floor_epoch)
    try:
        from_b = await ac.locate_block_by_ts(low_epoch, _get_block_ts, latest_num,
                                             latest_ts, anchors, tol_s=7200)
        if from_b is None:
            raise RuntimeError("from-block locate did not converge")
    except Exception as e:  # noqa: BLE001
        note_err(e)
        return {"address": addr, "verdict": "INSUFFICIENT-EVIDENCE",
                "reasons": [f"could not locate the start block: {e!r}"],
                "cache_status": cache_status, "first_error": first_err[0] if first_err else None}
    to_b = latest_num

    canary_ok = await detection_canary(bc, latest_num)
    recon = await sweep_lifetime(bc, addr, from_b, to_b, cfg, note_err)
    fills = recon["v1_fills"] + recon["v2_fills"]  # V2 side=None until classified

    # span endpoints for ts interpolation (2 getBlock calls). If either fails,
    # ts_ok=False -> skill is NOT graded on a fabricated span (review finding I:
    # a transient getBlock error must yield INSUFFICIENT, never a span-0 REJECT).
    blocks = [int(f.get("block", 0)) for f in fills if int(f.get("block", 0)) > 0]
    ts_ok = True
    if blocks:
        b_lo, b_hi = min(blocks), max(blocks)
        try:
            ts_lo, ts_hi = await _get_block_ts(b_lo), await _get_block_ts(b_hi)
        except Exception as e:  # noqa: BLE001
            note_err(e)
            ts_lo = ts_hi = latest_ts
            b_lo = b_hi = b_lo or latest_num
            ts_ok = False
    else:
        b_lo = b_hi = latest_num
        ts_lo = ts_hi = latest_ts
        ts_ok = False  # no fills -> no span; skill ungradeable anyway

    # fractional span-days for the rate (integer floor inflates the rate of the
    # 1-2.5-day burst accounts Tier 4 exists to re-judge fairly — review finding
    # J). conc / maker-taker / rate need only the SWEEP (no direction), so
    # compute them HERE — before the dominant receipt phase.
    span_secs = (interp_ts(b_hi, b_lo, ts_lo, b_hi, ts_hi)
                 - interp_ts(b_lo, b_lo, ts_lo, b_hi, ts_hi)).total_seconds()
    span_days_frac = max(span_secs / 86400.0, 0.0)
    span_days = int(span_days_frac)  # whole days for display
    conc = counterparty_concentration(fills)
    mt = maker_taker_profile(fills)
    rate = true_rate_per_day(len(fills), span_days_frac)
    sweep_complete = (recon["rpc_err_frac"] <= cfg.max_rpc_err_frac and canary_ok
                      and len(fills) > 0)
    rate_flag = (bool(cfg.hft_max_rate) and ts_ok and len(fills) >= 100
                 and rate > cfg.hft_max_rate)

    # EARLY UNCOPYABLE SHORT-CIRCUIT (efficiency; verdict identical): a complete
    # sweep whose measured fill-RATE already exceeds the cap is REJECT-uncopyable
    # regardless of BUY/SELL split — so skip the dominant V2 receipt fetch. This
    # is exactly what deep_dive_verdict returns (the rate check precedes the
    # direction gate); direction-independent forensics are still reported.
    if sweep_complete and rate_flag:
        v2_txs_est = len({f["tx"] for f in recon["v2_fills"]})
        funder = None
        if cfg.funding:
            # sybil lineage must survive the short-circuit (session-close
            # finding G): a rate-rejected wallet still matters to funder
            # clustering, and the trace is cheap vs the skipped receipts.
            try:
                floor_b = await ac.locate_block_by_ts(
                    floor_epoch, _get_block_ts, latest_num, latest_ts, anchors,
                    tol_s=7200) or from_b
            except Exception as e:  # noqa: BLE001
                note_err(e)
                floor_b = from_b
            funder = await find_funder(bc, addr, floor_b, to_b, cfg, note_err)
        return {
            "address": addr, "verdict": "REJECT",
            "reasons": [f"UNCOPYABLE: true chain rate {rate:.0f} bets/day > cap "
                        f"{cfg.hft_max_rate:.0f} (mechanically un-tailable — a "
                        f"measured fact, not an accusation; V2 receipts SKIPPED, "
                        f"direction not needed to judge rate)"],
            "cache_status": cache_status, "incomplete_cache_sweep": incomplete_cache,
            "ts_ok": ts_ok, "block_range": [from_b, to_b], "span_days": span_days,
            "tier1_reconstruction": {
                "n_fills": len(fills), "n_v1": len(recon["v1_fills"]),
                "n_v2": len(recon["v2_fills"]), "v2_txs": v2_txs_est,
                "v2_receipts": 0, "receipts_skipped_uncopyable": True,
                "leaf_ok": recon["leaf_ok"], "leaf_fail": recon["leaf_fail"],
                "rpc_err_frac": round(recon["rpc_err_frac"], 4),
                "canary_ok": canary_ok},
            "tier4_forensics": {"counterparty": conc, "maker_taker": mt,
                                "true_rate_per_day": round(rate, 2),
                                "funder": funder},
            "first_error": first_err[0] if first_err else None,
        }

    # NOT uncopyable -> resolve V2 direction (the receipt phase, capped), then
    # the direction-dependent tiers (skill, reconciliation).
    print(f"      … sweep done: {len(fills)} fills "
          f"({len(recon['v2_fills'])} V2, rpc_err={recon['rpc_err_frac']:.3f}); "
          f"fetching receipts", file=sys.stderr)
    cls = await classify_v2_directions(bc, addr, recon["v2_fills"], cfg, note_err)
    recon.update(cls)  # v2_txs, v2_receipts, receipts_ok, receipts_failed

    # token -> condition map from API rows; load resolutions for Tier 3
    tok2cond = {str(t.get("tokenId")): str(t.get("marketId"))
                for t in api_trades if t.get("tokenId") and t.get("marketId")}
    cids = sorted({c for c in tok2cond.values() if c})
    markets = await markets_loader(cids)

    chain_first_buys = reconstruct_first_buys(fills, tok2cond)
    chain_buys = [f for f in fills if f.get("side") == "BUY"]
    skill = chain_skill_grade(chain_first_buys, markets, tok2cond,
                              b_lo, ts_lo, b_hi, ts_hi, cfg, cfg.seed)

    # reconcile ONLY API BUYs inside the swept window [from_b_ts, head] — API
    # BUYs outside the sweep were never searched on chain, so counting them
    # 'unbacked' is a windowing artifact, not fabrication (finding H + smoke
    # 2026-07-14 99% false-FABRICATION). Default full sweep -> whole history.
    from_b_ts = next((ts for ts, b in anchors if b == from_b), low_epoch)
    sweep_lo = datetime.fromtimestamp(from_b_ts, tz=timezone.utc).replace(tzinfo=None)
    sweep_hi = datetime.fromtimestamp(latest_ts, tz=timezone.utc).replace(tzinfo=None)
    api_buys = window_api_buys(api_trades, sweep_lo, sweep_hi)
    a2c = reconcile_api_to_chain(api_buys, fills, cfg.tol_price, cfg.tol_size)
    api_complete = cache_status == "ok"
    # Direction B over ALL chain BUYs (not just first-buys) so a hidden RE-ENTRY
    # BUY in a market whose first buy is public is still caught (review finding
    # E). Capped at the API cache's NEWEST trade ts (session-close finding H:
    # the chain sweep runs to the live head, so post-cache-fetch fills would
    # all read 'hidden' — a staleness artifact, not concealment). REPORT-ONLY:
    # size+price-exact chain-vs-API reconciliation is noisy across fill
    # granularity, so hidden count is an operator-reviewed forensic, NOT a
    # verdict gate (a granularity-aware hidden gate is a documented follow-up).
    if api_complete:
        api_hi = max(api_ts)
        c2a_buys = [f for f in chain_buys
                    if interp_ts(int(f.get("block", 0)), b_lo, ts_lo, b_hi,
                                 ts_hi) <= api_hi]
        c2a = reconcile_chain_to_api(c2a_buys, api_trades, cfg.tol_price,
                                     cfg.tol_size)
        c2a["capped_at_api_ts"] = api_hi.isoformat()
        c2a["chain_buys_after_cache"] = len(chain_buys) - len(c2a_buys)
    else:
        c2a = {"n_chain_buys": len(chain_buys), "hidden": None,
               "hidden_rows": [], "note": f"API cache status={cache_status}: "
                                          f"hidden-activity not conclusive "
                                          f"(record incomplete)"}

    copier = {"sampled": 0, "preceded": 0, "frac": 0.0}
    if cfg.copier_sample > 0 and chain_first_buys:
        copier = await copier_probe(bc, chain_first_buys, addr, cfg, note_err)
    funder = None
    if cfg.funding:
        # funding predates the first trade — search from the floor, not from_b
        # (review finding K); one extra locate, only when --funding is set.
        try:
            floor_b = await ac.locate_block_by_ts(floor_epoch, _get_block_ts,
                                                  latest_num, latest_ts, anchors,
                                                  tol_s=7200) or from_b
        except Exception as e:  # noqa: BLE001
            note_err(e)
            floor_b = from_b
        funder = await find_funder(bc, addr, floor_b, to_b, cfg, note_err)

    metrics = {
        "rpc_err_frac": recon["rpc_err_frac"], "canary_ok": canary_ok,
        "n_chain_fills": len(fills),
        # V2 direction is resolved by per-tx receipts; if the CAP truncated them
        # OR receipt fetches FAILED beyond the error-fraction tolerance, the BUY
        # set is incomplete and mismatch/fabrication/skill can't be trusted
        # (smoke 2026-07-14 + session-close finding B: an errored receipt left
        # side=None but counted as resolved -> false not_found -> a FABRICATION
        # accusation from an RPC gap). rate/UNCOPYABLE fires before this gate.
        "direction_complete": (recon["v2_receipts"] >= recon["v2_txs"]
                               and recon.get("receipts_failed", 0)
                               <= cfg.max_rpc_err_frac
                               * max(recon["v2_receipts"], 1)),
        "v2_txs": recon["v2_txs"], "v2_receipts": recon["v2_receipts"],
        "receipts_failed": recon.get("receipts_failed", 0),
        "mismatch": a2c["counts"]["mismatch"],
        "api_buys_checked": a2c["counts"]["n"], "api_backing": a2c["counts"]["backing"],
        "ts_ok": ts_ok,
        "skill_gradeable": (ts_ok and skill["n_markets"] >= 1
                            and skill["n_labeled"] >= cfg.min_markets_hire),
        "skill_clears": skill["clears_bar"], "skill_contradicts": skill["contradicts"],
        "skill_markets": skill["n_markets"], "skill_span": skill["span_days"],
        "skill_p": skill["p"], "skill_p_neg": skill["p_neg"],
        "skill_edge": round(skill["edge"], 4) if skill["edge"] is not None else None,
        "skill_labeled": skill["n_labeled"],
        "wash_flag": (conc["top_share"] >= cfg.wash_share
                      and conc["named_fills"] >= cfg.wash_min_fills),
        "wash_share": conc["top_share"], "wash_named": conc["named_fills"],
        # min 100 fills before the rate can flag — a tiny-span handful of fills
        # must not read as HFT (mirrors fc.is_hft_history's sample floor); and
        # only when ts_ok (a collapsed span would inflate the rate — see finding I)
        "rate_flag": (bool(cfg.hft_max_rate) and ts_ok and len(fills) >= 100
                      and rate > cfg.hft_max_rate),
        "true_rate": rate,
        "copier_flag": (copier["sampled"] >= cfg.copier_min_sample
                        and copier["frac"] >= cfg.copier_frac),
        "copier_frac": copier["frac"],
    }
    verdict, reasons = deep_dive_verdict(metrics, cfg)
    return {
        "address": addr, "verdict": verdict, "reasons": reasons,
        "cache_status": cache_status, "incomplete_cache_sweep": incomplete_cache,
        "ts_ok": ts_ok,
        "block_range": [from_b, to_b], "span_days": span_days,
        "tier1_reconstruction": {
            "n_fills": len(fills),
            "n_buys": sum(1 for f in fills if f.get("side") == "BUY"),
            "n_sells": sum(1 for f in fills if f.get("side") == "SELL"),
            "n_v1": sum(1 for f in fills if f["era"] == "v1"),
            "n_v2": sum(1 for f in fills if f["era"] == "v2"),
            "v2_side_unknown": sum(1 for f in fills
                                   if f["era"] == "v2" and f.get("side") is None),
            "v2_txs": recon["v2_txs"], "v2_receipts": recon["v2_receipts"],
            "receipts_ok": recon.get("receipts_ok"),
            "receipts_failed": recon.get("receipts_failed"),
            "leaf_ok": recon["leaf_ok"], "leaf_fail": recon["leaf_fail"],
            "rpc_err_frac": round(recon["rpc_err_frac"], 4),
            "canary_ok": canary_ok},
        "tier2_reconcile": {"api_to_chain": a2c["counts"],
                            "mismatches": a2c["mismatches"][:5],
                            "chain_to_api": c2a},
        "tier3_skill": skill,
        "tier4_forensics": {"counterparty": conc, "maker_taker": mt,
                            "true_rate_per_day": round(rate, 2),
                            "copier": copier, "funder": funder},
        "first_error": first_err[0] if first_err else None,
    }


async def run(args) -> int:
    from dotenv import load_dotenv
    load_dotenv()
    if os.path.exists("/opt/pa2-shared/.env"):
        load_dotenv("/opt/pa2-shared/.env")
    from base_engine.data.blockchain_client import BlockchainClient
    from base_engine.data.database import Database

    rpc_url, rpc_err = ac.resolve_rpc_url(args.rpc_url, args.rpc_env, os.environ)
    if rpc_err:
        print(rpc_err, file=sys.stderr)
        return 2

    # roster: explicit --traders, else readjudicate VINDICATED, else fail loud
    roster: list[str] = []
    if args.traders:
        roster = [a.strip().lower() for a in args.traders.split(",") if a.strip()]
    elif args.readjudicate_json and os.path.exists(args.readjudicate_json):
        with open(args.readjudicate_json) as f:
            roster += roster_from_readjudicate(json.load(f))
    if args.extra_traders and os.path.exists(args.extra_traders):
        with open(args.extra_traders) as f:
            roster += [a.strip().lower() for a in f
                       if a.strip().startswith("0x")]
    roster = list(dict.fromkeys(roster))
    if not roster:
        print("no roster (need --traders, --readjudicate-json, or --extra-traders)",
              file=sys.stderr)
        return 2
    if args.limit > 0:
        roster = roster[:args.limit]
    os.makedirs(args.out_dir, exist_ok=True)
    print(f"chain deep dive: {len(roster)} traders  rps={args.rps}  "
          f"chunk={args.chunk_blocks}  max_receipts={args.max_receipts}  "
          f"copier_sample={args.copier_sample}  funding={args.funding}",
          file=sys.stderr)

    client = None
    db = Database()
    await db.init()
    bc = BlockchainClient(rpc_url=rpc_url)
    await bc.ensure_client()

    # preload the gamma resolution cache ONCE (review finding L)
    gamma_dict: dict = {}
    if args.gamma_cache and os.path.exists(args.gamma_cache):
        try:
            with open(args.gamma_cache) as gf:
                gamma_dict = json.load(gf)
        except Exception as e:  # noqa: BLE001
            print(f"gamma cache load failed ({e!r}); DB labels only",
                  file=sys.stderr)

    async def markets_loader(cids: list[str]) -> dict:
        if not cids:
            return {}
        markets = await fc.load_markets(db, cids, args.timeout)
        _merge_gamma_preloaded(markets, cids, gamma_dict)
        return markets

    try:
        results: dict[str, dict] = {}
        for i, addr in enumerate(roster):
            print(f"  [{i + 1}/{len(roster)}] {addr[:14]}… …", file=sys.stderr)
            # cache load is INSIDE the per-trader try (review finding C): one
            # torn/corrupt cache json must record an INSUFFICIENT and continue,
            # never abort the ~47-trader / multi-hour batch and lose the summary.
            cache_status = "missing"
            try:
                cpath = os.path.join(args.cache, f"{addr}.json")
                if os.path.exists(cpath) and not args.refresh:
                    with open(cpath) as f:
                        cache_blob = json.load(f)
                    cache_status = (cache_blob.get("status", "ok")
                                    if isinstance(cache_blob, dict) else "ok")
                else:
                    from base_engine.data.polymarket_client import PolymarketClient
                    if client is None:
                        client = PolymarketClient()
                        await client.__aenter__()
                    trades, cache_status = await fc.fetch_history(
                        client, addr, args.max_bets, args.rps, args.cache,
                        args.refresh, allow_deepen=True,
                        hft_max_bets_per_day=0.0)  # deep dive wants the full record
                    cache_blob = {"status": cache_status, "trades": trades}
                res = await deep_dive_one(bc, addr, cache_blob, cache_status,
                                          markets_loader, args)
            except Exception as e:  # noqa: BLE001  (one bad trader must NOT kill the batch)
                res = {"address": addr, "verdict": "INSUFFICIENT-EVIDENCE",
                       "reasons": [f"deep dive raised: {e!r}"],
                       "cache_status": cache_status}
            results[addr] = res
            fc.write_json_atomic(os.path.join(args.out_dir, f"{addr}.json"),
                                 fc.json_safe(res))
            # crash-durable, CROSS-RUN summary: rebuilt from the on-disk
            # per-trader JSONs after EVERY trader (session-close finding I —
            # run-1 died pre-summary and left no aggregate; deriving from
            # out_dir makes the summary automatically span all runs sharing it)
            try:
                write_summary_from_dir(args.out_dir, args.out)
            except Exception as e:  # noqa: BLE001
                print(f"      (summary rebuild failed: {e!r})", file=sys.stderr)
            v = res["verdict"]
            t1 = res.get("tier1_reconstruction", {})
            print(f"      -> {v}  (api={cache_status}) fills={t1.get('n_fills', '?')} "
                  f"buys={t1.get('n_buys', '?')} rpc_err={t1.get('rpc_err_frac', '?')} "
                  f"| {('; '.join(res.get('reasons', []))[:120])}", file=sys.stderr)
    finally:
        await db.close()
        if client is not None:
            await client.__aexit__(None, None, None)

    # batch summary + sybil clustering (shared funders across ADMIT/near traders)
    by_funder: dict[str, list[str]] = {}
    for a, r in results.items():
        fu = (r.get("tier4_forensics") or {}).get("funder")
        if fu:
            by_funder.setdefault(fu, []).append(a)
    sybil = {fu: addrs for fu, addrs in by_funder.items() if len(addrs) > 1}
    counts = {}
    for r in results.values():
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    admitted = sorted(a for a, r in results.items() if r["verdict"] == "ADMIT")

    print("\n" + "=" * 78)
    print("  CHAIN DEEP DIVE — roster-admission gate (chain wins; gaps deepen)")
    print(f"  traders={len(roster)}  " + "  ".join(f"{k}={v}" for k, v in
                                                    sorted(counts.items())))
    for a, r in results.items():
        t3 = r.get("tier3_skill", {})
        print(f"    {a[:14]}…  {r['verdict']:<20} "
              f"skill(mkts={t3.get('n_markets')},P={t3.get('p')},clears="
              f"{t3.get('clears_bar')})  {('; '.join(r.get('reasons', []))[:90])}")
    if sybil:
        print(f"  SYBIL WATCH (shared pUSD funders): {sybil}")
    print(f"  ADMIT (PROPOSED to operator, own cohort/start date): {admitted}")
    print("  READ: ADMIT is a proposal, never an auto-add; never pool an admitted")
    print("  cohort with cohort-1's readout. INSUFFICIENT = deepen the search,")
    print("  never accuse. REJECT = chain contradicts the trader or un-tailable.")
    print("=" * 78)
    write_summary_from_dir(args.out_dir, args.out, params={
        "tol_price": args.tol_price, "tol_size": args.tol_size,
        "min_markets_hire": args.min_markets_hire,
        "min_span_days": args.min_span_days, "p_hire": args.p_hire,
        "max_rpc_err_frac": args.max_rpc_err_frac,
        "min_api_backing": args.min_api_backing,
        "fabrication_frac": args.fabrication_frac,
        "wash_share": args.wash_share, "hft_max_rate": args.hft_max_rate})
    print(f"summary (ALL runs sharing {args.out_dir}) -> {args.out}")
    return 0


# ── Self-test (no network, no DB) ────────────────────────────────────────────
def _self_test() -> int:
    print("SELF-TEST — chain deep dive: every tier's pure core (no network)\n")
    ok = True
    A = "0xabcd000000000000000000000000000000000001"

    # V1 reconstruction: BUY (maker pays USDC), SELL (maker pays token), taker
    buy = v1_reconstruct_fill(
        {"maker": A, "taker": "0x9", "makerAssetId": 0, "takerAssetId": 777,
         "makerAmountFilled": 60_000_000, "takerAmountFilled": 100_000_000,
         "_tx": "0xt1", "_block": 5}, A)
    sell = v1_reconstruct_fill(
        {"maker": A, "taker": "0x9", "makerAssetId": 777, "takerAssetId": 0,
         "makerAmountFilled": 100_000_000, "takerAmountFilled": 80_000_000}, A)
    tkbuy = v1_reconstruct_fill(
        {"maker": "0x9", "taker": A, "makerAssetId": 777, "takerAssetId": 0,
         "makerAmountFilled": 50_000_000, "takerAmountFilled": 33_000_000}, A)
    ok1 = (buy and buy["side"] == "BUY" and abs(buy["price"] - 0.60) < 1e-9
           and buy["token_id"] == "777" and buy["counterparty"] is None  # V1 taker is the operator, not a cp
           and sell and sell["side"] == "SELL" and abs(sell["price"] - 0.80) < 1e-9
           and tkbuy and tkbuy["side"] == "BUY" and abs(tkbuy["price"] - 0.66) < 1e-9)
    print(f"  [v1 recon] maker-BUY/maker-SELL/taker-BUY, prices (V1 cp=None) : {ok1}")
    ok &= ok1

    # V1 semantics must agree with the audited _leg on the BUY case
    leg = rj._leg({"maker": A, "taker": "0x9", "makerAssetId": 0,
                   "takerAssetId": 777, "makerAmountFilled": 60_000_000,
                   "takerAmountFilled": 100_000_000}, A, 777, True)
    ok2 = leg is not None and abs(leg[0] / leg[1] - buy["price"]) < 1e-9
    print(f"  [v1 vs _leg] BUY price agrees with the audited matcher : {ok2}")
    ok &= ok2

    # V2 reconstruction: owner topic + data layout; direction stays None
    def w32(n):
        return "%064x" % n
    lg = {"topics": ["0x" + FILL_TOPIC_V2.replace("0x", ""), "0x" + w32(1),
                     addr_topic(A), addr_topic("0x00000000000000000000000000000000000000ff")],
          "data": "0x" + w32(0) + w32(777) + w32(90_000_000) + w32(100_000_000)
                  + w32(0) + w32(0) + w32(0),
          "address": "0x5", "transactionHash": "0xv2", "blockNumber": 9}
    v2 = v2_reconstruct_fill(lg, A)
    ok3 = (v2 and v2["side"] is None and v2["token_id"] == "777"
           and abs(v2["price"] - 0.90) < 1e-9 and v2["era"] == "v2"
           and v2["counterparty"] == "0x00000000000000000000000000000000000000ff")
    print(f"  [v2 recon] owner-topic + layout, direction pending : {ok3}")
    ok &= ok3

    # first BUY per market (condition via map), earliest block wins
    fills = [
        {"token_id": "777", "side": "BUY", "price": 0.6, "tokens": 100, "usd": 60,
         "block": 5, "tx": "0x1", "era": "v1"},
        {"token_id": "777", "side": "BUY", "price": 0.7, "tokens": 50, "usd": 35,
         "block": 9, "tx": "0x2", "era": "v1"},   # later add — not first
        {"token_id": "888", "side": "SELL", "price": 0.4, "tokens": 10, "usd": 4,
         "block": 3, "tx": "0x3", "era": "v1"},   # SELL — never a first-buy
        {"token_id": "999", "side": None, "price": 0.5, "tokens": 10, "usd": 5,
         "block": 2, "tx": "0x4", "era": "v2"}]   # unknown dir — excluded
    fbs = reconstruct_first_buys(fills, {"777": "0xCOND", "888": "0xC2"})
    ok4 = (len(fbs) == 1 and fbs[0]["market_key"] == "0xCOND"
           and fbs[0]["block"] == 5)
    print(f"  [first-buy] one per market, earliest block, SELL/None excluded : {ok4}")
    ok &= ok4

    # Tier 2A: only BUY-side chain fills are candidates — a SELL@0.9 must NOT
    # verify an API BUY@0.9 (that masking flipped REJECT->ADMIT, review finding A)
    chain = [{"token_id": "777", "usd": 60, "tokens": 100, "tx": "0xa",
              "price": 0.6, "side": "BUY"},
             {"token_id": "777", "usd": 90, "tokens": 100, "tx": "0xb",
              "price": 0.9, "side": "SELL"}]  # ignored — SELL never backs a BUY
    r = reconcile_api_to_chain(
        [{"tokenId": "777", "side": "BUY", "price": 0.60, "size": 100},   # verified vs BUY@0.6
         {"tokenId": "777", "side": "BUY", "price": 0.90, "size": 100},   # SELL ignored -> matches BUY@0.6 by size -> mismatch
         {"tokenId": "777", "side": "BUY", "price": 0.60, "size": 7}],     # not_found
        chain, 0.02, 0.05)
    ok5 = (r["counts"]["verified"] == 1 and r["counts"]["mismatch"] == 1
           and r["counts"]["not_found"] == 1
           and abs(r["counts"]["backing"] - 1 / 3) < 1e-9
           and abs(r["mismatches"][0]["chain_price"] - 0.6) < 1e-9)  # BUY, not the SELL@0.9
    print(f"  [tier2A] api->chain BUY-only candidates (SELL never backs a BUY) : {ok5}")
    ok &= ok5

    # Tier 2B: chain BUY absent from API = hidden
    c2a = reconcile_chain_to_api(
        [{"token_id": "777", "price": 0.6, "tokens": 100, "tx": "0xa"},
         {"token_id": "555", "price": 0.3, "tokens": 40, "tx": "0xb"}],
        [{"tokenId": "777", "price": 0.6, "size": 100}], 0.02, 0.05)
    ok6 = c2a["hidden"] == 1 and c2a["n_chain_buys"] == 2
    print(f"  [tier2B] chain->api hidden-activity detection : {ok6}")
    ok &= ok6

    # Tier 4: wash concentration + maker/taker + rate
    fills2 = [{"era": "v1", "maker": True, "counterparty": "0xCP"},
              {"era": "v1", "maker": False, "counterparty": "0xCP"},
              {"era": "v2", "was_taker": True, "counterparty": None}]
    conc = counterparty_concentration(fills2)
    mt = maker_taker_profile(fills2)
    ok7 = (abs(conc["top_share"] - 1.0) < 1e-9 and conc["named_fills"] == 2
           and mt["maker"] == 1 and mt["taker"] == 2
           and abs(true_rate_per_day(300, 3) - 100) < 1e-9)
    print(f"  [tier4] wash share / maker-taker / true rate : {ok7}")
    ok &= ok7

    # ts interpolation
    it = interp_ts(50, 0, 1000, 100, 3000)
    ok8 = int(it.replace(tzinfo=timezone.utc).timestamp()) == 2000
    print(f"  [interp] linear block->ts between endpoints : {ok8}")
    ok &= ok8

    # Tier 3 skill: labelable chain first-buys graded on the hire bar
    class _Cfg:
        min_markets_hire = 3
        min_span_days = 0
        p_hire = 0.10
        n_boot_roster = 200
    markets = {f"0xC{i}": {"resolution": "YES", "yes_token_id": str(i),
                           "no_token_id": "n" + str(i)} for i in range(6)}
    tok2cond = {str(i): f"0xC{i}" for i in range(6)}
    # varied positive edges (0.58..0.70) so the bootstrap is non-degenerate;
    # identical edges correctly bootstrap to P=0 (no evidence) — a real record
    # has spread. Blocks 10..15 span the interpolation endpoints.
    fbs3 = [{"token_id": str(i), "side": "BUY", "price": 0.30 + 0.03 * i,
             "block": 10 + i, "era": "v1"} for i in range(6)]
    sk = chain_skill_grade(fbs3, markets, tok2cond, 10, 1000, 15, 5_000_000,
                           _Cfg(), 7)
    ok9 = (sk["n_labeled"] == 6 and sk["n_markets"] == 6 and sk["clears_bar"]
           and sk["p"] >= 0.10)
    print(f"  [tier3] chain first-buys labeled + hire-bar graded : {ok9}")
    ok &= ok9

    # verdict table (pre-registered rule): REJECT only on affirmative
    # contradiction or mechanical infeasibility; gaps + unverified forensic
    # suspicions -> INSUFFICIENT (never accuse); ADMIT enforces backing.
    class _VC:
        max_rpc_err_frac = 0.05
        min_api_check = 10
        min_api_backing = 0.80
        fabrication_frac = 0.50
        wash_share = 0.50
        hft_max_rate = 200.0
        copier_lead_s = 30
    base = {"rpc_err_frac": 0.0, "canary_ok": True, "n_chain_fills": 500,
            "direction_complete": True, "v2_txs": 100, "v2_receipts": 100,
            "mismatch": 0, "api_buys_checked": 40, "api_backing": 0.95,
            "ts_ok": True, "skill_gradeable": True, "skill_clears": True,
            "skill_contradicts": False, "skill_markets": 40, "skill_span": 120,
            "skill_p": 0.99, "skill_p_neg": 0.01, "skill_edge": 0.05,
            "skill_labeled": 40, "wash_flag": False, "wash_share": 0.1,
            "wash_named": 100, "rate_flag": False, "true_rate": 5.0,
            "copier_flag": False, "copier_frac": 0.0}
    V = lambda **o: deep_dive_verdict({**base, **o}, _VC())[0]  # noqa: E731
    checks = {
        "admit": (V(), "ADMIT"),
        "incomplete": (V(rpc_err_frac=0.5), "INSUFFICIENT-EVIDENCE"),
        "canary": (V(canary_ok=False), "INSUFFICIENT-EVIDENCE"),
        "lie": (V(mismatch=2), "REJECT"),
        "fabrication": (V(api_backing=0.3), "REJECT"),
        "skill_disproven": (V(skill_contradicts=True), "REJECT"),
        "skill_underpowered": (V(skill_clears=False), "INSUFFICIENT-EVIDENCE"),
        "rate_uncopyable": (V(rate_flag=True), "REJECT"),
        # UNCOPYABLE fires even with receipts capped (needs no direction)
        "rate_before_direction": (V(rate_flag=True, direction_complete=False),
                                  "REJECT"),
        "direction_capped": (V(direction_complete=False, v2_receipts=200,
                               v2_txs=5000), "INSUFFICIENT-EVIDENCE"),
        "wash_investigate": (V(wash_flag=True), "INSUFFICIENT-EVIDENCE"),
        "copier_investigate": (V(copier_flag=True), "INSUFFICIENT-EVIDENCE"),
        "thin_backing": (V(api_backing=0.6), "INSUFFICIENT-EVIDENCE"),
        "too_few_buys": (V(api_buys_checked=5, api_backing=0.0),
                         "INSUFFICIENT-EVIDENCE"),  # can't ADMIT below the check floor
        "ungradeable": (V(skill_gradeable=False, skill_labeled=2),
                        "INSUFFICIENT-EVIDENCE"),
        "ts_failed": (V(ts_ok=False, skill_gradeable=False),
                      "INSUFFICIENT-EVIDENCE"),
    }
    bad = {k: got for k, (got, want) in checks.items() if got != want}
    ok10 = not bad
    print(f"  [verdict] 16-case table (REJECT only on contradiction/uncopyable) "
          f": {ok10}" + (f"  MISMATCHES={bad}" if bad else ""))
    ok &= ok10

    ok11 = roster_from_readjudicate({"vindicated": ["0xB", "0xA"]}) == ["0xa", "0xb"]
    print(f"  [roster] VINDICATED from readjudicate json, lowered+sorted : {ok11}")
    ok &= ok11

    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Chain-native roster-admission deep dive (4 tiers)")
    # roster sources
    ap.add_argument("--traders", default="",
                    help="explicit comma-separated addresses (overrides json)")
    ap.add_argument("--readjudicate-json", default="", dest="readjudicate_json",
                    help="readjudicate*.json; roster = its VINDICATED (cohort-2)")
    ap.add_argument("--extra-traders", default="", dest="extra_traders",
                    help="text file of extra addresses, one per line (e.g. the "
                         "34 HFT-borderline)")
    ap.add_argument("--limit", type=int, default=0,
                    help="process only the first N (smoke run; 0 = all)")
    # data locations
    ap.add_argument("--cache", default="/tmp/copyable_cache",
                    help="per-address API history cache dir")
    ap.add_argument("--gamma-cache",
                    default="/tmp/copyable_cache/gamma_resolutions.json",
                    dest="gamma_cache", help="resolution backfill JSON")
    ap.add_argument("--refresh", action="store_true",
                    help="re-pull each API history fresh (direction-B completeness)")
    ap.add_argument("--max-bets", type=int, default=200000, dest="max_bets")
    ap.add_argument("--out", default="/tmp/deep_dive.json")
    ap.add_argument("--out-dir", default="/tmp/deep_dive", dest="out_dir")
    # RPC / sweep
    ap.add_argument("--rpc-url", default="", dest="rpc_url",
                    help="Polygon archive RPC serving eth_getLogs (tenderly)")
    ap.add_argument("--rpc-env", default="", dest="rpc_env")
    ap.add_argument("--rps", type=float, default=6.0)
    ap.add_argument("--chunk-blocks", type=int, default=50000, dest="chunk_blocks",
                    help="getLogs range for topic-filtered sweeps (bisects on error)")
    ap.add_argument("--min-chunk", type=int, default=900, dest="min_chunk",
                    help="smallest getLogs range; a failure here is an "
                         "unrecoverable coverage leaf (feeds rpc_err_frac)")
    ap.add_argument("--max-receipts", type=int, default=4000, dest="max_receipts",
                    help="cap on V2 direction receipts per trader (HFT bound)")
    ap.add_argument("--pad-days", type=int, default=14, dest="pad_days",
                    help="sweep starts this far before the first API-claimed fill")
    ap.add_argument("--from-date", default="", dest="from_date",
                    help="force the sweep start date (catches pre-first-claim "
                         "hidden activity; overrides --pad-days low bound)")
    ap.add_argument("--floor-date", default="2023-01-01", dest="floor_date",
                    help="hard earliest sweep bound (pre-Polymarket-volume floor)")
    # Tier 3 hire bar (identical defaults to walkforward_copy_traders)
    ap.add_argument("--min-markets-hire", type=int, default=25, dest="min_markets_hire")
    ap.add_argument("--min-span-days", type=int, default=60, dest="min_span_days")
    ap.add_argument("--p-hire", type=float, default=0.90, dest="p_hire")
    ap.add_argument("--n-boot-roster", type=int, default=400, dest="n_boot_roster")
    # Tier 2 tolerances + verdict thresholds (PRE-REGISTERED; do not move mid-run)
    ap.add_argument("--tol-price", type=float, default=0.02, dest="tol_price")
    ap.add_argument("--tol-size", type=float, default=0.05, dest="tol_size")
    ap.add_argument("--max-rpc-err-frac", type=float, default=0.05, dest="max_rpc_err_frac",
                    help="above this unrecoverable-leaf fraction the sweep is "
                         "INSUFFICIENT (widen, never admit/accuse)")
    ap.add_argument("--min-api-check", type=int, default=10, dest="min_api_check",
                    help="min API BUYs to reconcile before backing gates apply")
    ap.add_argument("--min-api-backing", type=float, default=0.80, dest="min_api_backing")
    ap.add_argument("--fabrication-frac", type=float, default=0.50, dest="fabrication_frac",
                    help="unbacked-API-BUY fraction that, on a complete sweep, is "
                         "fabrication (REJECT)")
    # Tier 4 forensics
    ap.add_argument("--wash-share", type=float, default=0.50, dest="wash_share")
    ap.add_argument("--wash-min-fills", type=int, default=20, dest="wash_min_fills")
    ap.add_argument("--hft-max-rate", type=float, default=200.0, dest="hft_max_rate",
                    help="true chain bets/day above which a trader is un-tailable "
                         "(the fair lifetime-rate test; 0 disables)")
    ap.add_argument("--copier-sample", type=int, default=0, dest="copier_sample",
                    help="sampled copier-latency probe size (0 = skip; heavy)")
    ap.add_argument("--copier-min-sample", type=int, default=8, dest="copier_min_sample")
    ap.add_argument("--copier-lead-s", type=int, default=30, dest="copier_lead_s")
    ap.add_argument("--copier-frac", type=float, default=0.60, dest="copier_frac")
    ap.add_argument("--funding", action="store_true",
                    help="trace each wallet's earliest pUSD funder (sybil lineage)")
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--seed", type=int, default=20260714)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        raise SystemExit(_self_test())
    raise SystemExit(asyncio.run(run(args)))
