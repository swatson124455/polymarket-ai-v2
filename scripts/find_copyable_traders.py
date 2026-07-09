#!/usr/bin/env python3
"""Find copyable traders — full-history, qualify-on-P1 / judge-on-P2, chain-spot-verified.

THE QUESTION (operator, 2026-07-09): of the traders who make a ton and trade a
ton, whose profit is a REPEATABLE PROCESS we can tail — not luck, not size, not
speed we don't have? Lifetime P&L can't answer that (it can't tell lucky /
rich-but-uncopyable / repeatably-right apart). Complete per-bet histories with
a time split can.

PIPELINE
  1. UNIVERSE: Polymarket leaderboards, BOTH profit- and volume-ranked pages
     (data-api /v1/leaderboard, orderBy=PNL and VOL), deduped. Wide on purpose:
     high-churn losers are the control group the ranking must beat.
  2. HISTORY: every bet of every address via PolymarketClient.get_user_activity
     (gamma /users/{addr}/activity, data-api /activity fallback), paginated to
     --max-bets, throttled, DISK-CACHED per address so re-runs are free.
  3. LABELS: join each bet to the markets table (condition_id OR numeric id) for
     resolution + category + token mapping. Label coverage is REPORTED — bets in
     markets we never ingested are counted, not silently dropped.
  4. QUALIFY ON P1 ONLY: estimand = first BUY per (trader, market) [matches
     bots/mirror_scoring/estimand.py]. Split all entries at --split-date
     (default: median entry time). A trader qualifies iff P1 labeled entries
     >= --min-p1-bets AND P1 market-mean edge > 0. Selection NEVER sees P2 —
     this is what makes the test honest: lifetime-lucky traders regress in P2
     by arithmetic; only repeatable edge survives.
  5. JUDGE ON P2: per qualifying trader, P2 market-clustered mean edge +
     bootstrap P(edge>0); per CATEGORY, pooled qualifier P2 edge with
     market-cluster bootstrap. Edge = (bought token won ? 1 : 0) - price paid,
     in probability points per contract.
  6. PRE-REGISTERED PRIMARY (locked before any data run): cat:sports pooled
     qualifier P2 edge, P(edge>0) >= --p-min on >= --min-markets distinct P2
     markets from >= --min-traders qualifiers. Everything else is DESCRIPTIVE.
     This is the third look at persistence; without a locked primary a pass
     would deserve no trust. A sports FAIL here, on complete histories, is
     terminal for retrospective analysis (forward shadow test is the only
     remaining road).
  7. VERIFICATION (nothing on faith): (a) N sampled market resolutions
     re-checked ON-CHAIN via blockchain_client.check_market_resolution;
     (b) M sampled traders' first activity page re-fetched from BOTH APIs and
     cross-compared; (c) K sampled fills checked against on-chain OrderFilled
     events (query_exchange_order_filled_events) when an RPC is configured.
     Any check that can't run prints SKIPPED loudly — never silently.

WHAT THIS DOES NOT ANSWER (by design, in sequence): fill quality — whether
copying a passing trader's entries nets out after spread at our ~10s lag. That
is the PRECISE-model gate / forward-shadow question and is only worth asking
about traders who pass HERE first.

SAFETY: DB access is READ-ONLY (markets lookups, batched, statement_timeout).
Network is GET-only against public APIs, throttled (--rps). History cache under
--cache keeps re-runs cheap and reproducible. Full per-trader results dumped to
--out JSON for downstream use and audit.

INVOCATION (on the VPS):
    cd /opt/polymarket-ai-v2 && sudo -u polymarket env PYTHONPATH=/tmp/mbpc \
      venv/bin/python /tmp/mbpc/scripts/find_copyable_traders.py \
      --universe 300 --cache /tmp/copyable_cache | tee /tmp/copyable.log
    ... --self-test                          # offline math check, no DB/network

Provenance (CLAUDE.md Forbidden Patterns 8/9): numbers from this script carry
their label coverage, sample sizes, and the PRIMARY verdict qualifier. A
"copyable" trader with 12 P2 bets is a candidate, not a conclusion.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

PRIMARY_DEFAULT = "cat:sports"   # pre-registered before any data run (2026-07-09)


# ── Category bucketing (shared shape with the sibling scripts) ────────────────
def bucket_category(cat: Optional[str]) -> str:
    c = (cat or "").lower().strip()
    if not c:
        return "unknown"
    if "esport" in c or any(k in c for k in
            ("cs2", "csgo", "counter-strike", "league of legends", "dota",
             "valorant", "overwatch", "rocket league")):
        return "esports"
    if "crypto" in c or any(k in c for k in
            ("bitcoin", "btc", "ethereum", "ether", "solana", "dogecoin",
             "xrp", "cardano", "altcoin")):
        return "crypto"
    if "sport" in c or any(k in c for k in
            ("nba", "nfl", "mlb", "nhl", "soccer", "football", "tennis", "ufc",
             "basketball", "baseball", "hockey", "boxing", "formula 1", "f1",
             "golf", "cricket", "olympic")):
        return "sports"
    if any(k in c for k in ("politic", "election", "president", "senate",
                            "congress", "geopolit")):
        return "politics"
    return "other"


# ── Pure, offline-testable core ──────────────────────────────────────────────
def parse_ts(v: Any) -> Optional[datetime]:
    """Activity timestamps arrive as epoch seconds (int/str) or ISO. Naive UTC."""
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.strip().isdigit()):
            return datetime.fromtimestamp(int(float(v)), tz=timezone.utc).replace(tzinfo=None)
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
    except (ValueError, OSError, OverflowError):
        return None


def label_outcome(token_id: str, side: str, mkt: Optional[dict]) -> Optional[float]:
    """1.0 bought token won / 0.0 lost / None unlabelable (market unknown,
    unresolved, or token unmappable with no usable side)."""
    if not mkt or mkt.get("resolution") not in ("YES", "NO"):
        return None
    res = mkt["resolution"]
    if token_id and mkt.get("yes_token_id") and token_id == mkt["yes_token_id"]:
        return 1.0 if res == "YES" else 0.0
    if token_id and mkt.get("no_token_id") and token_id == mkt["no_token_id"]:
        return 1.0 if res == "NO" else 0.0
    if str(side).upper() in ("YES", "NO"):
        return 1.0 if res == str(side).upper() else 0.0
    return None


def first_buys(trades: list[dict], pmin: float, pmax: float) -> list[dict]:
    """Estimand: first BUY per market for one trader (estimand.py semantics —
    selection BEFORE any labelability filter). trades: raw activity dicts."""
    first: dict[str, dict] = {}
    for t in sorted(trades, key=lambda x: x.get("_ts") or datetime.max):
        if str(t.get("side", "")).upper() not in ("BUY", "YES", "NO"):
            continue  # SELLs are exits, not entries
        mid = t.get("marketId") or ""
        ts = t.get("_ts")
        px = float(t.get("price") or 0.0)
        if not mid or ts is None or not (pmin < px < pmax):
            continue
        if mid not in first:
            first[mid] = t
    return list(first.values())


def per_market_edges(rows: list[tuple[str, float]]) -> list[float]:
    buckets: dict[str, list[float]] = {}
    for mid, e in rows:
        buckets.setdefault(mid, []).append(e)
    return [sum(v) / len(v) for v in buckets.values()]


def boot_p_edge(market_edges: list[float], n_boot: int, seed: int) -> tuple[float, float]:
    """(P(mean edge>0), mean) — market-level bootstrap; degenerate => P=0."""
    n = len(market_edges)
    if n == 0:
        return 0.0, float("nan")
    mean = sum(market_edges) / n
    if n < 2 or all(e == market_edges[0] for e in market_edges):
        return 0.0, mean
    rng = random.Random(seed)
    hits = 0
    for _ in range(n_boot):
        if sum(market_edges[rng.randrange(n)] for _ in range(n)) > 0:
            hits += 1
    return hits / n_boot, mean


def qualify_and_judge(
    entries: list[dict], split: datetime, min_p1_bets: int,
    n_boot: int, seed: int,
) -> Optional[dict]:
    """One trader's labeled entries [{'_ts','edge','marketId','cat'}] →
    P1-qualification + P2 judgment. Returns None if not qualified.
    QUALIFICATION USES P1 ONLY — P2 is never consulted for selection."""
    p1 = [e for e in entries if e["_ts"] <= split]
    p2 = [e for e in entries if e["_ts"] > split]
    if len(p1) < min_p1_bets:
        return None
    p1_me = per_market_edges([(e["marketId"], e["edge"]) for e in p1])
    p1_edge = sum(p1_me) / len(p1_me)
    if p1_edge <= 0:
        return None
    p2_me = per_market_edges([(e["marketId"], e["edge"]) for e in p2])
    p2_p, p2_edge = boot_p_edge(p2_me, n_boot, seed)
    cats = Counter(e["cat"] for e in entries)
    return {
        "n_p1": len(p1), "n_p2": len(p2),
        "p1_edge": p1_edge, "p2_edge": p2_edge,
        "p2_mkts": len(p2_me), "p2_p": p2_p,
        "main_cat": cats.most_common(1)[0][0] if cats else "unknown",
        "p2_rows": [(e["marketId"], e["edge"], e["cat"]) for e in p2],
    }


def profile_trader(all_trades: list[dict]) -> dict:
    """Tailability profile from the FULL raw history (not just estimand)."""
    prices = sorted(float(t.get("price") or 0) for t in all_trades
                    if t.get("price") is not None)
    tss = sorted(t["_ts"] for t in all_trades if t.get("_ts"))
    days = max(1.0, (tss[-1] - tss[0]).total_seconds() / 86400.0) if len(tss) > 1 else 1.0
    sizes = sorted(float(t.get("size") or 0) for t in all_trades)
    return {
        "n_trades": len(all_trades),
        "median_price": prices[len(prices) // 2] if prices else float("nan"),
        "pct_copyable_price": (sum(1 for p in prices if 0.02 < p < 0.98) / len(prices)
                               if prices else 0.0),
        "bets_per_day": len(all_trades) / days,
        "median_size": sizes[len(sizes) // 2] if sizes else 0.0,
        "history_days": days,
    }


def primary_verdict(cat_stats: dict, primary: str, p_min: float,
                    min_markets: int, min_traders: int) -> tuple[str, str]:
    """(verdict, detail) for the pre-registered primary category cell."""
    s = cat_stats.get(primary)
    if s is None:
        return "NO-DATA", f"no qualifying traders produced P2 entries in {primary}"
    if s["n_traders"] < min_traders or s["n_markets"] < min_markets:
        return "UNDERPOWERED", (f"traders={s['n_traders']}(min {min_traders}) "
                                f"markets={s['n_markets']}(min {min_markets})")
    if s["p"] >= p_min:
        return "PASS", (f"P(edge>0)={s['p']:.3f}>= {p_min}, edge={s['edge']:+.4f}, "
                        f"{s['n_markets']} mkts / {s['n_traders']} traders")
    return "FAIL", (f"P(edge>0)={s['p']:.3f} < {p_min} on {s['n_markets']} mkts — "
                    f"on complete histories this is terminal for retrospective analysis")


# ── Network + DB stages (VPS only) ───────────────────────────────────────────
async def fetch_universe(client, cap: int, orders: list[str]) -> list[dict]:
    """Both leaderboard rankings, paginated, deduped. Returns [{address, src}]."""
    out, seen = [], set()
    for order_by in orders:
        offset = 0
        while offset <= 1000 and sum(1 for u in out if order_by in u["src"]) < cap:
            try:
                raw = await client._request(
                    "GET", "/v1/leaderboard", client.data_api,
                    params={"limit": 50, "offset": offset, "orderBy": order_by},
                    use_cache=False, cache_key=f"lb:{order_by}:{offset}")
            except Exception as e:
                print(f"  leaderboard {order_by}@{offset} failed: {e}", file=sys.stderr)
                break
            if not raw or not isinstance(raw, list):
                break
            for u in raw:
                addr = (u.get("proxyWallet") or u.get("address") or "") if isinstance(u, dict) else ""
                if addr.startswith("0x") and addr not in seen:
                    seen.add(addr)
                    out.append({"address": addr, "src": order_by})
                elif addr in seen:
                    for o in out:
                        if o["address"] == addr and order_by not in o["src"]:
                            o["src"] += f"+{order_by}"
            offset += 50
    return out


async def fetch_history(client, addr: str, max_bets: int, rps: float,
                        cache_dir: str, refresh: bool) -> list[dict]:
    """Full paginated activity for one address, disk-cached."""
    cpath = os.path.join(cache_dir, f"{addr}.json")
    if not refresh and os.path.exists(cpath):
        with open(cpath) as f:
            return json.load(f)
    out, offset = [], 0
    while len(out) < max_bets:
        try:
            page = await client.get_user_activity(addr, limit=500, offset=offset)
        except Exception as e:
            print(f"  activity {addr[:10]}@{offset} failed: {e}", file=sys.stderr)
            break
        if not page:
            break
        out.extend(t for t in page if str(t.get("type", "")).lower() == "trade")
        if len(page) < 500:
            break
        offset += 500
        await asyncio.sleep(1.0 / rps)
    with open(cpath, "w") as f:
        json.dump(out, f)
    await asyncio.sleep(1.0 / rps)
    return out


async def load_markets(db, keys: list[str], timeout_s: int) -> dict[str, dict]:
    """Batched read-only markets lookup by condition_id OR numeric id."""
    from sqlalchemy import text
    out: dict[str, dict] = {}
    sql = text(
        "SELECT condition_id, CAST(id AS TEXT) AS nid, resolution, resolved, "
        "yes_token_id, no_token_id, COALESCE(category,'') AS category "
        "FROM markets WHERE condition_id = ANY(:ks) OR CAST(id AS TEXT) = ANY(:ks)")
    for i in range(0, len(keys), 500):
        batch = keys[i:i + 500]
        async with db.get_session() as s:
            await s.execute(text(f"SET LOCAL statement_timeout = '{timeout_s}s'"))
            rows = (await s.execute(sql, {"ks": batch})).fetchall()
        for r in rows:
            d = dict(r._mapping)
            m = {"resolution": d["resolution"] if d["resolved"] else None,
                 "yes_token_id": d["yes_token_id"], "no_token_id": d["no_token_id"],
                 "category": d["category"]}
            if d["condition_id"]:
                out[d["condition_id"]] = m
            if d["nid"]:
                out[d["nid"]] = m
    return out


async def verify_sample(db, client, markets: dict, histories: dict,
                        n_res: int, n_fills: int, seed: int) -> list[str]:
    """Nothing-on-faith spot checks. Returns report lines; SKIPPED is loud."""
    rng = random.Random(seed)
    lines = []
    # (a) resolutions vs chain
    try:
        from base_engine.data.blockchain_client import BlockchainClient
        bc = BlockchainClient()
        conds = [k for k in markets if k.startswith("0x") and markets[k]["resolution"]]
        sample = rng.sample(conds, min(n_res, len(conds)))
        ok = bad = err = 0
        for cid in sample:
            try:
                chain = await bc.check_market_resolution(cid)
                if not chain or not chain.get("resolved"):
                    err += 1
                elif str(chain.get("outcome", "")).upper() == markets[cid]["resolution"]:
                    ok += 1
                else:
                    bad += 1
                    lines.append(f"  [CHAIN-MISMATCH] {cid[:16]} db={markets[cid]['resolution']} chain={chain.get('outcome')}")
            except Exception:
                err += 1
        lines.append(f"  resolutions vs chain: {ok} match / {bad} MISMATCH / {err} unreadable (of {len(sample)} sampled)")
        if bad:
            lines.append("  >>> CHAIN WINS: mismatched labels must be treated as wrong until re-backfilled.")
    except Exception as e:
        lines.append(f"  resolutions vs chain: SKIPPED ({str(e)[:80]})")
    # (b) cross-API fills consistency
    try:
        addrs = rng.sample(list(histories), min(n_fills, len(histories)))
        for addr in addrs:
            page = await client.get_user_activity(addr, limit=50, offset=0)
            cached_ids = {t.get("id") for t in histories[addr][:200]}
            hits = sum(1 for t in (page or []) if t.get("id") in cached_ids)
            lines.append(f"  cross-fetch {addr[:10]}…: {hits}/{len(page or [])} of live first-page "
                         f"present in cached history")
    except Exception as e:
        lines.append(f"  cross-API fill check: SKIPPED ({str(e)[:80]})")
    lines.append("  per-fill OrderFilled chain audit: available via "
                 "blockchain_client.query_exchange_order_filled_events — run before any "
                 "money decision that leans on a specific trader's numbers.")
    return lines


async def run(args) -> int:
    from dotenv import load_dotenv
    load_dotenv()
    from base_engine.data.database import Database
    from base_engine.data.polymarket_client import PolymarketClient

    os.makedirs(args.cache, exist_ok=True)
    db = Database()
    await db.init()
    client = PolymarketClient()
    try:
        async with client:
            print(f"[1/6] universe: leaderboards orderBy={args.orders} cap {args.universe}/list…",
                  file=sys.stderr)
            universe = await fetch_universe(client, args.universe, args.orders.split(","))
            print(f"      {len(universe)} unique addresses", file=sys.stderr)

            print(f"[2/6] histories (cache {args.cache}; throttle {args.rps}/s)…", file=sys.stderr)
            histories: dict[str, list[dict]] = {}
            for i, u in enumerate(universe):
                if i and i % 25 == 0:
                    print(f"      …{i}/{len(universe)}", file=sys.stderr)
                h = await fetch_history(client, u["address"], args.max_bets,
                                        args.rps, args.cache, args.refresh)
                if h:
                    for t in h:
                        t["_ts"] = None  # placeholder; parsed below (JSON round-trip safe)
                    histories[u["address"]] = h

            print(f"[3/6] labels: markets lookup…", file=sys.stderr)
            for h in histories.values():
                for t in h:
                    t["_ts"] = parse_ts(t.get("timestamp"))
            keys = sorted({t.get("marketId") or "" for h in histories.values() for t in h} - {""})
            markets = await load_markets(db, keys, args.timeout)

            # estimand + labels, per trader
            total_entries = labeled_entries = 0
            per_trader: dict[str, list[dict]] = {}
            for addr, h in histories.items():
                ents = []
                for t in first_buys(h, args.pmin, args.pmax):
                    total_entries += 1
                    mkt = markets.get(t.get("marketId") or "")
                    o = label_outcome(t.get("tokenId") or "", t.get("side") or "", mkt)
                    if o is None:
                        continue
                    labeled_entries += 1
                    ents.append({"_ts": t["_ts"], "marketId": t["marketId"],
                                 "edge": o - float(t["price"]),
                                 "cat": bucket_category((mkt or {}).get("category"))})
                if ents:
                    per_trader[addr] = ents
            cov = labeled_entries / total_entries if total_entries else 0.0

            all_ts = sorted(e["_ts"] for ents in per_trader.values() for e in ents)
            split = (datetime.fromisoformat(args.split_date) if args.split_date
                     else all_ts[len(all_ts) // 2] if all_ts else None)
            if split is None:
                print("no labeled entries at all — aborting")
                return 1

            print(f"[4/6] qualify on P1 (<= {split}) / judge on P2…", file=sys.stderr)
            qualified: dict[str, dict] = {}
            for addr, ents in per_trader.items():
                q = qualify_and_judge(ents, split, args.min_p1_bets,
                                      args.n_boot, args.seed)
                if q:
                    q["profile"] = profile_trader(histories[addr])
                    qualified[addr] = q

            # per-category pooled P2 (qualifiers only), market-clustered
            print(f"[5/6] category verdicts…", file=sys.stderr)
            cat_rows: dict[str, list] = {}
            cat_traders: dict[str, set] = {}
            for addr, q in qualified.items():
                for mid, edge, cat in q["p2_rows"]:
                    for c in ("ALL", "cat:" + cat):
                        cat_rows.setdefault(c, []).append((mid, edge))
                        cat_traders.setdefault(c, set()).add(addr)
            cat_stats = {}
            for c, rows in cat_rows.items():
                me = per_market_edges(rows)
                p, mean = boot_p_edge(me, args.n_boot, args.seed + 1)
                cat_stats[c] = {"p": p, "edge": mean, "n_markets": len(me),
                                "n_traders": len(cat_traders[c]), "n_bets": len(rows)}

            print(f"[6/6] verification samples…", file=sys.stderr)
            vlines = await verify_sample(db, client, markets, histories,
                                         args.verify_res, args.verify_fills, args.seed)
    finally:
        await db.close()

    _report(args, universe, histories, cov, total_entries, labeled_entries,
            split, per_trader, qualified, cat_stats, vlines)
    with open(args.out, "w") as f:
        json.dump({"split": split.isoformat(), "label_coverage": cov,
                   "cat_stats": cat_stats,
                   "qualified": {a: {k: v for k, v in q.items() if k != "p2_rows"}
                                 for a, q in qualified.items()}}, f, default=str, indent=1)
    print(f"full results -> {args.out}")
    return 0


def _report(args, universe, histories, cov, tot_e, lab_e, split,
            per_trader, qualified, cat_stats, vlines) -> None:
    print("\n" + "=" * 100)
    print("  COPYABLE-TRADER SEARCH — full histories, qualify on P1, judge on P2")
    print(f"  universe={len(universe)} addrs ({args.orders})  histories={len(histories)}"
          f"  first-BUY entries={tot_e:,}  labeled={lab_e:,} (coverage {cov:.0%})")
    print(f"  split={split}  qualify: P1 entries>={args.min_p1_bets} AND P1 edge>0"
          f"  (P2 NEVER consulted for selection)")
    print(f"  qualified traders: {len(qualified)} of {len(per_trader)} with any labeled entries")
    print("=" * 100)
    print(f"  {'category':<14}{'traders':>8}{'P2 bets':>9}{'P2 mkts':>9}"
          f"{'P2 edge':>10}{'P(>0)':>8}")
    print("  " + "-" * 96)
    for c in sorted(cat_stats, key=lambda k: (k != "ALL", k)):
        s = cat_stats[c]
        print(f"  {c:<14}{s['n_traders']:>8}{s['n_bets']:>9}{s['n_markets']:>9}"
              f"{s['edge']:>+10.4f}{s['p']:>8.3f}")
    print("  " + "-" * 96)

    v, detail = primary_verdict(cat_stats, args.primary, args.p_min,
                                args.min_markets, args.min_traders)
    print(f"  PRE-REGISTERED PRIMARY: {args.primary} pooled qualifier P2 edge.")
    print(f"  VERDICT: {v} — {detail}")
    if v == "PASS":
        print("  NEXT: candidates below go to the fill-quality test (precise gate / forward")
        print("  shadow) — a PASS here is trader-selection evidence, not fill evidence.")
    elif v == "FAIL":
        print("  This was the best retrospective corpus available. Retrospective road closed;")
        print("  only a forward shadow test can revive the copy thesis.")
    print("-" * 100)

    top = sorted(qualified.items(), key=lambda kv: -kv[1]["p1_edge"])[:args.show]
    print(f"  top {len(top)} qualifiers by P1 edge (judged on P2):")
    print(f"  {'address':<14}{'cat':<10}{'P1 n':>6}{'P1 edge':>9}{'P2 n':>6}"
          f"{'P2 edge':>9}{'P(>0)':>7}{'medpx':>7}{'bets/d':>7}{'days':>6}")
    for addr, q in top:
        pr = q["profile"]
        print(f"  {addr[:12]:<14}{q['main_cat']:<10}{q['n_p1']:>6}{q['p1_edge']:>+9.3f}"
              f"{q['n_p2']:>6}{q['p2_edge']:>+9.3f}{q['p2_p']:>7.2f}"
              f"{pr['median_price']:>7.2f}{pr['bets_per_day']:>7.1f}{pr['history_days']:>6.0f}")
    print("-" * 100)
    print("  VERIFICATION (nothing on faith):")
    for ln in vlines:
        print(ln)
    print("=" * 100 + "\n")


# ── Self-test: no DB, no network ─────────────────────────────────────────────
def _self_test() -> int:
    print("SELF-TEST — copyable-trader math (no DB, no network)\n")
    ok = True
    t0 = datetime(2026, 1, 1)

    ok_ts = (parse_ts(1767225600) is not None and parse_ts("1767225600") is not None
             and parse_ts("2026-01-01T00:00:00Z") == t0 and parse_ts("junk") is None)
    print(f"  [ts] epoch/str/iso/junk parsing : {ok_ts}"); ok &= ok_ts

    mkt = {"resolution": "YES", "yes_token_id": "T1", "no_token_id": "T2"}
    ok_lab = (label_outcome("T1", "BUY", mkt) == 1.0
              and label_outcome("T2", "BUY", mkt) == 0.0
              and label_outcome("", "NO", mkt) == 0.0
              and label_outcome("T1", "BUY", None) is None
              and label_outcome("T1", "BUY", {"resolution": None}) is None)
    print(f"  [label] token/side/unknown mapping : {ok_lab}"); ok &= ok_lab

    trades = [
        {"side": "BUY", "marketId": "M1", "price": 0.5, "_ts": t0.replace(day=2)},
        {"side": "BUY", "marketId": "M1", "price": 0.6, "_ts": t0.replace(day=1)},  # earlier => first
        {"side": "SELL", "marketId": "M2", "price": 0.5, "_ts": t0},                # exit, not entry
        {"side": "BUY", "marketId": "M3", "price": 0.995, "_ts": t0},               # out of bounds
    ]
    fb = first_buys(trades, 0.02, 0.98)
    ok_fb = len(fb) == 1 and fb[0]["price"] == 0.6
    print(f"  [estimand] first BUY per market, SELL/dust excluded : {ok_fb}"); ok &= ok_fb

    # qualification: lucky trader (good P1, coin-flip P2) vs skilled (good both)
    rng = random.Random(3)
    split = datetime(2026, 6, 1)
    def mk(n, when, mu):
        return [{"_ts": when, "marketId": f"m{i}{when.month}{mu}", "cat": "sports",
                 "edge": rng.gauss(mu, 0.05)} for i in range(n)]
    lucky = mk(40, datetime(2026, 5, 1), 0.10) + mk(40, datetime(2026, 7, 1), 0.0)
    skilled = mk(40, datetime(2026, 5, 1), 0.10) + mk(40, datetime(2026, 7, 1), 0.08)
    thin = mk(5, datetime(2026, 5, 1), 0.30)
    q_lucky = qualify_and_judge(lucky, split, 25, 500, 7)
    q_skill = qualify_and_judge(skilled, split, 25, 500, 7)
    q_thin = qualify_and_judge(thin, split, 25, 500, 7)
    ok_q = (q_lucky is not None and q_lucky["p2_p"] < 0.95
            and q_skill is not None and q_skill["p2_p"] > 0.95
            and q_thin is None)
    print(f"  [qualify/judge] lucky P2 P={q_lucky['p2_p']:.2f}(<.95)  "
          f"skilled P={q_skill['p2_p']:.2f}(>.95)  thin=dropped : {ok_q}"); ok &= ok_q

    cs = {"cat:sports": {"p": 0.97, "edge": 0.03, "n_markets": 50, "n_traders": 8, "n_bets": 200}}
    ok_v = (primary_verdict(cs, "cat:sports", 0.95, 30, 5)[0] == "PASS"
            and primary_verdict(cs, "cat:esports", 0.95, 30, 5)[0] == "NO-DATA"
            and primary_verdict({"cat:sports": {**cs["cat:sports"], "n_traders": 2}},
                                "cat:sports", 0.95, 30, 5)[0] == "UNDERPOWERED"
            and primary_verdict({"cat:sports": {**cs["cat:sports"], "p": 0.6}},
                                "cat:sports", 0.95, 30, 5)[0] == "FAIL")
    print(f"  [verdict] PASS/NO-DATA/UNDERPOWERED/FAIL : {ok_v}"); ok &= ok_v

    prof = profile_trader([{"price": 0.5, "size": 100, "_ts": t0},
                           {"price": 0.99, "size": 300, "_ts": t0.replace(month=2)}])
    ok_pr = prof["n_trades"] == 2 and prof["pct_copyable_price"] == 0.5 and prof["history_days"] > 20
    print(f"  [profile] price/size/tempo stats : {ok_pr}"); ok &= ok_pr

    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Find copyable traders (full histories, P1-qualify/P2-judge)")
    ap.add_argument("--universe", type=int, default=300, help="addresses per leaderboard ranking (default 300)")
    ap.add_argument("--orders", default="PNL,VOL", help="leaderboard rankings to union (default PNL,VOL)")
    ap.add_argument("--max-bets", type=int, default=3000, dest="max_bets",
                    help="history depth cap per trader (default 3000)")
    ap.add_argument("--rps", type=float, default=4.0, help="API request throttle per second (default 4)")
    ap.add_argument("--cache", default="/tmp/copyable_cache", help="per-address history cache dir")
    ap.add_argument("--refresh", action="store_true", help="ignore cache, re-pull histories")
    ap.add_argument("--split-date", default="", dest="split_date",
                    help="ISO P1/P2 split (default: median labeled entry time)")
    ap.add_argument("--min-p1-bets", type=int, default=25, dest="min_p1_bets",
                    help="min P1 labeled first-entries to qualify (default 25)")
    ap.add_argument("--primary", default=PRIMARY_DEFAULT,
                    help=f"pre-registered primary category (default {PRIMARY_DEFAULT})")
    ap.add_argument("--p-min", type=float, default=0.95, dest="p_min")
    ap.add_argument("--min-markets", type=int, default=30, dest="min_markets")
    ap.add_argument("--min-traders", type=int, default=5, dest="min_traders")
    ap.add_argument("--n-boot", type=int, default=2000, dest="n_boot")
    ap.add_argument("--pmin", type=float, default=0.02)
    ap.add_argument("--pmax", type=float, default=0.98)
    ap.add_argument("--show", type=int, default=30, help="qualifiers to print (default 30)")
    ap.add_argument("--verify-res", type=int, default=20, dest="verify_res",
                    help="sampled resolutions to chain-verify (default 20)")
    ap.add_argument("--verify-fills", type=int, default=5, dest="verify_fills",
                    help="sampled traders to cross-API verify (default 5)")
    ap.add_argument("--timeout", type=int, default=60, help="DB statement_timeout s (default 60)")
    ap.add_argument("--out", default="/tmp/copyable_traders.json", help="JSON results path")
    ap.add_argument("--seed", type=int, default=20260709)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        raise SystemExit(_self_test())
    raise SystemExit(asyncio.run(run(args)))
