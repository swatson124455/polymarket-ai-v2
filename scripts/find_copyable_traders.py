#!/usr/bin/env python3
"""Find copyable traders — full-history, qualify-on-P1 / judge-on-P2, chain-spot-verified.

THE QUESTION (operator, 2026-07-09): of the traders who make a ton and trade a
ton, whose profit is a REPEATABLE PROCESS we can tail — not luck, not size, not
speed we don't have? Lifetime P&L can't answer that (it can't tell lucky /
rich-but-uncopyable / repeatably-right apart). Complete per-bet histories with
a time split can.

DESIGN (hardened per the 2026-07-09 two-agent adversarial review; every item
below marked [rev] closes a confirmed false-PASS/false-FAIL channel):

  1. UNIVERSE: data-api /v1/leaderboard, orderBy=PNL and VOL, timePeriod=ALL
     pinned [rev: unpinned window made the universe irreproducible and
     recent-window-selected]. Wide on purpose — churners are the control group.
     [rev CRITICAL] PNL-board membership itself conditions on late-period wins
     (a collider: among P1-lucky traders, the ones ALSO on a PnL board are
     disproportionately P2-lucky — mechanically inflating pooled P2 edge, the
     same circularity class as the documented broken validation harness).
     Therefore THE PRIMARY VERDICT COUNTS ONLY VOL-SOURCED QUALIFIERS (volume
     conditions on activity, not outcomes); PNL-only traders are descriptive.
  2. HISTORY: full pagination of data-api /activity per address (newest-first),
     normalized in-script [rev: shared client's gamma branch returns raw rows —
     routed around, not "fixed", per shared-module protocol]. The API rejects
     offset >= 3500 (400, measured live 2026-07-09), so deep histories are
     walked by TIME-WINDOWED pagination: offset-page to 3000, re-anchor `end`
     at the oldest timestamp seen, repeat; cross-window dedupe removes
     boundary re-fetches. Disk-cached per address; [rev CRITICAL] cache
     written ONLY on clean completion — a partial pull (timeout/circuit-
     breaker) is never cached and the address is counted FAILED in the report,
     not silently treated as a complete record. [rev CRITICAL] histories
     hitting --max-bets are flagged TRUNCATED and excluded from the primary
     (their early bets are missing, so "first BUY" and P1 counts are wrong for
     exactly the most active traders).
  3. LABELS: markets-table lookup split into two INDEX-BACKED queries
     (condition_id ANY / integer id ANY) [rev: the OR+CAST form seq-scans —
     tonight's timeout failure mode]. Label coverage reported PER PERIOD
     (P1 vs P2) since our ingestion skews recent [rev].
  4. QUALIFY ON P1 ONLY: estimand = first BUY per (trader, market).
     Qualify iff >= --min-p1-markets DISTINCT P1 markets [rev: entry-count
     threshold could be met with 3 markets] AND P1 market-clustered bootstrap
     P(edge>0) >= --p1-p-min (default 0.90) [rev: 'edge>0' alone lets ~half of
     all noise-traders through, diluting the pool toward a false terminal
     FAIL]. Selection NEVER sees P2.
  5. JUDGE ON P2: per-trader and pooled per category, market-clustered
     bootstrap. Edge = (bought token won ? 1:0) - price, prob-points/contract.
  6. PRE-REGISTERED PRIMARY (locked in code before any data run):
     cat:sports pooled P2 edge over VOL-SOURCED, NON-TRUNCATED qualifiers;
     PASS needs P(edge>0) >= --p-min on >= --min-markets distinct P2 markets
     from >= --min-traders qualifiers, AT THE LOCKED CALENDAR SPLIT
     (--split-date default 2026-02-01) AND positive pooled edge at both
     robustness splits (--robustness-splits, default 2025-12-01 + 2026-04-01)
     [rev: a data-chosen single split lets one season/regime masquerade as
     persistence]. FAIL splits into FAIL-TERMINAL (bootstrap upper bound below
     --econ-floor, default +0.02 ≈ fee+slippage floor) vs INCONCLUSIVE
     [rev: 'FAIL is terminal' at P=0.70 on 35 markets was an overclaim].
  7. VERIFICATION (nothing on faith): sampled resolutions re-checked ON-CHAIN
     (chain-unresolved-vs-DB-resolved surfaced as a DISCREPANCY, split/50-50
     payouts flagged ambiguous rather than mismatched [rev]); sampled live
     re-fetch vs cache; per-fill OrderFilled chain audit documented as the
     mandatory step before any money decision leans on a specific trader.

WHAT THIS DOES NOT ANSWER (sequenced, not dodged): fill quality after spread at
our ~10s lag — the precise-gate / forward-shadow question, only worth asking
about traders who pass HERE.

SAFETY: DB reads only (two indexed batched lookups under statement_timeout);
GET-only APIs, throttled (--rps); per-address cache keeps re-runs cheap; full
results to --out JSON (NaN-sanitized).

INVOCATION (on the VPS):
    cd /opt/polymarket-ai-v2 && sudo -u polymarket env PYTHONPATH=/tmp/mbpc \
      venv/bin/python /tmp/mbpc/scripts/find_copyable_traders.py \
      --universe 300 --cache /tmp/copyable_cache | tee /tmp/copyable.log
    ... --self-test                          # offline math check, no DB/network

Provenance (CLAUDE.md Forbidden Patterns 8/9): numbers from this script carry
their label coverage, truncation/failure counts, sample sizes, and the primary
verdict qualifier. A "copyable" trader with 12 P2 bets is a candidate, not a
conclusion.
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

PRIMARY_DEFAULT = "cat:sports"        # pre-registered 2026-07-09
SPLIT_DEFAULT = "2026-02-01"          # locked calendar split (not data-chosen)
ROBUSTNESS_DEFAULT = "2025-12-01,2026-04-01"


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
    """Epoch seconds (int/str), ISO (aware or naive) → naive UTC."""
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.strip().isdigit()):
            return datetime.fromtimestamp(int(float(v)), tz=timezone.utc).replace(tzinfo=None)
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
    except (ValueError, OSError, OverflowError):
        return None


def normalize_activity(raw: list, seen: Optional[set] = None) -> list[dict]:
    """data-api /activity rows → the fields this script depends on, deduped.
    Dedupe key includes token/ts/price/size because the tx hash is shared by
    multi-fill transactions [rev]. Pass a persistent `seen` set to dedupe
    ACROSS pages/windows (time-windowed pagination re-fetches boundary rows)."""
    out = []
    if seen is None:
        seen = set()
    for act in raw or []:
        if not isinstance(act, dict) or str(act.get("type", "")).upper() != "TRADE":
            continue
        mid = (act.get("conditionId") or act.get("slug") or "").strip()
        if not mid:
            continue
        asset = (act.get("asset") or "").strip()
        ts = act.get("timestamp")
        key = ((act.get("transactionHash") or "").strip(), asset, str(ts),
               str(act.get("price")), str(act.get("size")))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "marketId": mid, "tokenId": asset,
            "side": (act.get("side") or "BUY").upper(),
            "size": float(act.get("size", 0) or 0),
            "price": float(act.get("price", 0) or 0),
            "timestamp": ts,
        })
    return out


def label_outcome(token_id: str, side: str, mkt: Optional[dict]) -> Optional[float]:
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
    """First BUY per market for one trader (selection before labelability)."""
    first: dict[str, dict] = {}
    for t in sorted(trades, key=lambda x: x.get("_ts") or datetime.max):
        if str(t.get("side", "")).upper() not in ("BUY", "YES", "NO"):
            continue
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


def boot_stats(market_edges: list[float], n_boot: int, seed: int
               ) -> tuple[float, float, float]:
    """(P(mean>0), mean, upper95) via market-level bootstrap. Degenerate
    inputs => P=0, upper=mean (no evidence, no pass, no terminal claim)."""
    n = len(market_edges)
    if n == 0:
        return 0.0, float("nan"), float("nan")
    mean = sum(market_edges) / n
    if n < 2 or all(e == market_edges[0] for e in market_edges):
        return 0.0, mean, mean
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        means.append(sum(market_edges[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    p = sum(1 for m in means if m > 0) / n_boot
    upper = means[min(n_boot - 1, int(0.95 * n_boot))]
    return p, mean, upper


def qualify_and_judge(entries: list[dict], split: datetime, min_p1_markets: int,
                      p1_p_min: float, n_boot: int, seed: int) -> Optional[dict]:
    """P1-qualification (never sees P2) + P2 judgment for one trader.
    Qualify: >= min_p1_markets DISTINCT P1 markets AND P1 clustered
    bootstrap P(edge>0) >= p1_p_min [rev: raw edge>0 admits half of all
    noise traders and dilutes the pooled verdict]."""
    p1 = [e for e in entries if e["_ts"] <= split]
    p2 = [e for e in entries if e["_ts"] > split]
    p1_me = per_market_edges([(e["marketId"], e["edge"]) for e in p1])
    if len(p1_me) < min_p1_markets:
        return None
    p1_p, p1_edge, _ = boot_stats(p1_me, n_boot, seed)
    if p1_p < p1_p_min:
        return None
    p2_me = per_market_edges([(e["marketId"], e["edge"]) for e in p2])
    p2_p, p2_edge, p2_up = boot_stats(p2_me, n_boot, seed + 1)
    cats = Counter(e["cat"] for e in entries)
    return {
        "n_p1": len(p1), "n_p1_mkts": len(p1_me), "p1_edge": p1_edge, "p1_p": p1_p,
        "n_p2": len(p2), "p2_mkts": len(p2_me), "p2_edge": p2_edge,
        "p2_p": p2_p, "p2_upper95": p2_up,
        "main_cat": cats.most_common(1)[0][0] if cats else "unknown",
        "p2_rows": [(e["marketId"], e["edge"], e["cat"]) for e in p2],
    }


def profile_trader(all_trades: list[dict]) -> dict:
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


def primary_verdict(stats: Optional[dict], p_min: float, min_markets: int,
                    min_traders: int, econ_floor: float,
                    robust_means: list[float]) -> tuple[str, str]:
    """Verdict for the primary cell (already restricted to VOL-sourced,
    non-truncated qualifiers). FAIL is TERMINAL only when the bootstrap upper
    bound sits below the economic floor; PASS additionally requires positive
    pooled edge at every robustness split [rev]."""
    if stats is None or stats["n_bets"] == 0:
        return "NO-DATA", "no qualifying VOL-sourced traders produced P2 entries"
    if stats["n_traders"] < min_traders or stats["n_markets"] < min_markets:
        return "UNDERPOWERED", (f"traders={stats['n_traders']}(min {min_traders}) "
                                f"markets={stats['n_markets']}(min {min_markets})")
    if stats["p"] >= p_min:
        bad = [m for m in robust_means if not (isinstance(m, float) and m > 0)]
        if bad:
            return "INCONCLUSIVE", (f"P={stats['p']:.3f} at the locked split but "
                                    f"robustness splits disagree ({robust_means}) — "
                                    f"regime artifact risk; not a PASS")
        return "PASS", (f"P(edge>0)={stats['p']:.3f}>={p_min}, edge={stats['edge']:+.4f}, "
                        f"{stats['n_markets']} mkts / {stats['n_traders']} traders; "
                        f"robustness splits agree ({['%+.4f' % m for m in robust_means]})")
    if not math.isnan(stats["upper95"]) and stats["upper95"] < econ_floor:
        return "FAIL-TERMINAL", (f"P={stats['p']:.3f}; bootstrap upper95 "
                                 f"{stats['upper95']:+.4f} < econ floor {econ_floor:+.2f} — "
                                 f"even the optimistic bound is below costs")
    return "INCONCLUSIVE", (f"P={stats['p']:.3f} < {p_min} but upper95 "
                            f"{stats['upper95']:+.4f} >= {econ_floor:+.2f} — "
                            f"not passed, not disproven; more data, not a rework")


def looks_like_market_maker(page: list[dict], max_bets_per_day: float) -> bool:
    """First-page rate check (2026-07-10 tinker): an account trading hundreds
    of times a day is a bot/market-maker — mechanically uncopyable (nobody
    mirrors 2,000 daily orders; the 'edge' is spread capture, not knowledge)
    and the dominant download cost. Newest-first page of up to 500 trades:
    rate = trades / days-spanned. 0 disables."""
    if not max_bets_per_day or len(page) < 100:
        return False
    ts = [parse_ts(t.get("timestamp")) for t in page]
    ts = [t for t in ts if t is not None]
    if len(ts) < 100:
        return False
    span_days = max(0.02, (max(ts) - min(ts)).total_seconds() / 86400.0)
    return (len(ts) / span_days) > max_bets_per_day


def is_hft_history(trades: list[dict], max_bets_per_day: float) -> bool:
    """Analysis-time twin of looks_like_market_maker for histories already on
    disk (rate over the full record). 0 disables."""
    if not max_bets_per_day or len(trades) < 100:
        return False
    ts = sorted(t["_ts"] for t in trades if t.get("_ts"))
    if len(ts) < 100:
        return False
    span_days = max(0.02, (ts[-1] - ts[0]).total_seconds() / 86400.0)
    return (len(ts) / span_days) > max_bets_per_day


def should_refetch(cached_status: str, cached_len: int, max_bets: int,
                   allow_deepen: bool) -> bool:
    """A TRUNCATED cache entry is re-pulled when the current --max-bets could
    deepen it (2026-07-09 first-run lesson: 329/496 truncated at the old cap
    starved the primary to 1 trader; without this, a higher cap silently
    reuses the shallow cache). Clean ('ok') entries are never re-pulled."""
    return allow_deepen and cached_status == "truncated" and cached_len < max_bets


def merge_gamma_cache(markets: dict, keys: list[str], path: str) -> int:
    """Merge gamma-API resolutions (scripts/backfill_resolutions_gamma.py)
    under the DB map — DB wins on conflict; the backfill only fills holes
    (2026-07-10 tinker: DB label coverage was 24%; the other 76% of bets
    live in markets we never ingested but gamma has). A hole is a key the
    DB either doesn't know or knows WITHOUT a resolution — a metadata row
    with resolution=None must not block its gamma label (2026-07-10 run:
    that mismatch merged 0 of the backfill's labels). Returns keys added."""
    if not path or not os.path.exists(path):
        return 0
    with open(path) as f:
        gamma = json.load(f)
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


def write_json_atomic(path: str, obj) -> None:
    """Dump to <path>.tmp then os.replace — a kill mid-write can never leave
    a truncated JSON that bricks the next run's json.load (2026-07-10 data-
    hygiene audit; this session pkills long runs as a matter of course)."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def json_safe(obj):
    """Recursively replace NaN/inf with None (bare NaN is invalid JSON) [rev]."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


# ── Network + DB stages (VPS only) ───────────────────────────────────────────
async def fetch_universe(client, cap: int, orders: list[str]) -> list[dict]:
    """Leaderboard pages, timePeriod=ALL pinned [rev], deduped with src tags."""
    out, seen = [], {}
    for order_by in orders:
        offset, got = 0, 0
        while offset <= 1000 and got < cap:
            try:
                raw = await client._request(
                    "GET", "/v1/leaderboard", client.data_api,
                    params={"limit": 50, "offset": offset, "orderBy": order_by,
                            "timePeriod": "ALL", "category": "OVERALL"},
                    use_cache=False, cache_key=f"lb:{order_by}:{offset}")
            except Exception as e:
                print(f"  leaderboard {order_by}@{offset} failed: {e}", file=sys.stderr)
                break
            if not raw or not isinstance(raw, list):
                break
            for u in raw:
                addr = (u.get("proxyWallet") or u.get("address") or "") if isinstance(u, dict) else ""
                if not addr.startswith("0x"):
                    continue
                got += 1
                if addr in seen:
                    if order_by not in seen[addr]["src"]:
                        seen[addr]["src"] += f"+{order_by}"
                else:
                    seen[addr] = {"address": addr, "src": order_by}
                    out.append(seen[addr])
            offset += 50
    return out


async def fetch_history(client, addr: str, max_bets: int, rps: float,
                        cache_dir: str, refresh: bool,
                        allow_deepen: bool = True,
                        hft_max_bets_per_day: float = 0.0) -> tuple[list[dict], str]:
    """(trades, status) with status ok | truncated | partial.
    Cache is written ONLY on clean completion (ok/truncated) [rev: a partial
    pull cached as a complete record silently corrupts every later run].
    A truncated cache entry is re-pulled when --max-bets can deepen it and
    allow_deepen is set (see should_refetch). Data-api /activity is called
    directly (normalized here) [rev: the shared client's gamma branch returns
    raw rows; routed around, not modified]."""
    cpath = os.path.join(cache_dir, f"{addr}.json")
    if not refresh and os.path.exists(cpath):
        with open(cpath) as f:
            blob = json.load(f)
        if isinstance(blob, dict):
            trades, status = blob.get("trades", []), blob.get("status", "ok")
        else:
            trades, status = blob, "ok"  # legacy cache shape
        if not should_refetch(status, len(trades), max_bets, allow_deepen):
            return trades, status
        # fall through to a fresh, deeper pull (old entry replaced on success)

    # The data-api rejects offset >= 3500 with 400 Bad Request (measured live
    # 2026-07-09 — every whale-depth history died there on the first run). So:
    # page by offset only up to MAX_OFFSET, then RE-ANCHOR BY TIME — pass the
    # oldest timestamp seen as `end` and restart offset=0. Boundary rows are
    # re-fetched and removed by the cross-window `seen` dedupe; a window that
    # makes no backward progress breaks (defensive, e.g. `end` unsupported).
    MAX_OFFSET = 3000
    out, offset, status = [], 0, "ok"
    seen: set = set()
    end_ts: Optional[int] = None
    oldest: Optional[int] = None
    while True:
        params = {"user": addr, "limit": 500, "offset": offset, "type": "TRADE"}
        if end_ts is not None:
            params["end"] = end_ts
        try:
            raw = await client._request(
                "GET", "/activity", client.data_api, params=params,
                use_cache=False, cache_key=f"act:{addr}:{offset}:{end_ts}")
        except Exception as e:
            print(f"  activity {addr[:10]}@{offset}/end={end_ts} failed: {e} — NOT cached",
                  file=sys.stderr)
            return out, "partial"
        page = normalize_activity(raw if isinstance(raw, list) else [], seen)
        if offset == 0 and end_ts is None and looks_like_market_maker(
                page, hft_max_bets_per_day):
            status = "hft"          # bot/market-maker: keep the sample page,
            out.extend(page)        # skip the deep download, exclude from grading
            break
        out.extend(page)
        for t in page:
            ts = parse_ts(t.get("timestamp"))
            if ts is not None:
                ep = int(ts.replace(tzinfo=timezone.utc).timestamp())
                oldest = ep if oldest is None else min(oldest, ep)
        if not raw or not isinstance(raw, list) or len(raw) < 500:
            break  # history exhausted — clean completion
        if len(out) >= max_bets:
            status = "truncated"
            break
        offset += 500
        if offset > MAX_OFFSET:
            if oldest is None or (end_ts is not None and oldest >= end_ts):
                # cannot progress backward (no timestamps / end unsupported):
                # treat as truncated-at-cap, never as a complete record
                status = "truncated"
                break
            end_ts = oldest
            offset = 0
        await asyncio.sleep(1.0 / rps)
    write_json_atomic(cpath, {"status": status, "trades": out})
    await asyncio.sleep(1.0 / rps)
    return out, status


async def load_markets(db, keys: list[str], timeout_s: int) -> dict[str, dict]:
    """Two INDEX-BACKED batched lookups [rev: the OR+CAST single query
    seq-scans markets per batch — tonight's timeout failure mode]."""
    from sqlalchemy import text
    cids = [k for k in keys if k.startswith("0x")]
    nids = [int(k) for k in keys if k.isdigit()]
    out: dict[str, dict] = {}

    async def _run(sql, param_key, batch):
        async with db.get_session() as s:
            await s.execute(text(f"SET LOCAL statement_timeout = '{timeout_s}s'"))
            return (await s.execute(sql, {param_key: batch})).fetchall()

    sql_c = text("SELECT condition_id, CAST(id AS TEXT) AS nid, resolution, resolved, "
                 "resolved_at, yes_token_id, no_token_id, COALESCE(category,'') AS category "
                 "FROM markets WHERE condition_id = ANY(:ks)")
    sql_n = text("SELECT condition_id, CAST(id AS TEXT) AS nid, resolution, resolved, "
                 "resolved_at, yes_token_id, no_token_id, COALESCE(category,'') AS category "
                 "FROM markets WHERE id = ANY(:ks)")
    for sql, batch_all in ((sql_c, cids), (sql_n, nids)):
        for i in range(0, len(batch_all), 500):
            for r in await _run(sql, "ks", batch_all[i:i + 500]):
                d = dict(r._mapping)
                m = {"resolution": d["resolution"] if d["resolved"] else None,
                     "resolved_at": d.get("resolved_at"),
                     "yes_token_id": d["yes_token_id"], "no_token_id": d["no_token_id"],
                     "category": d["category"]}
                if d["condition_id"]:
                    out[d["condition_id"]] = m
                if d["nid"]:
                    out[d["nid"]] = m
    return out


async def verify_sample(db, client, markets: dict, histories: dict,
                        n_res: int, n_fills: int, seed: int) -> list[str]:
    """Spot checks; SKIPPED is loud, discrepancies are their own bucket [rev]."""
    rng = random.Random(seed)
    lines = []
    try:
        from base_engine.data.blockchain_client import BlockchainClient
        bc = BlockchainClient()
        conds = [k for k in markets if k.startswith("0x") and markets[k]["resolution"]]
        sample = rng.sample(conds, min(n_res, len(conds)))
        ok = bad = unresolved = ambiguous = err = 0
        for cid in sample:
            try:
                chain = await bc.check_market_resolution(cid)
                if not chain:
                    err += 1
                elif not chain.get("resolved"):
                    unresolved += 1  # DB says resolved, chain says not: DISCREPANCY
                    lines.append(f"  [CHAIN-DISCREPANCY] {cid[:16]} db="
                                 f"{markets[cid]['resolution']} chain=UNRESOLVED")
                else:
                    payouts = chain.get("payouts") or []
                    nonzero = sum(1 for p in payouts if p)
                    if nonzero > 1:
                        ambiguous += 1  # split resolution — outcome label ambiguous
                    elif str(chain.get("outcome", "")).upper() == markets[cid]["resolution"]:
                        ok += 1
                    else:
                        bad += 1
                        lines.append(f"  [CHAIN-MISMATCH] {cid[:16]} db="
                                     f"{markets[cid]['resolution']} chain={chain.get('outcome')}")
            except Exception:
                err += 1
        lines.append(f"  resolutions vs chain: {ok} match / {bad} MISMATCH / "
                     f"{unresolved} chain-unresolved / {ambiguous} split-payout / "
                     f"{err} unreadable (of {len(sample)})")
        if bad or unresolved:
            lines.append("  >>> CHAIN WINS: discrepant labels are wrong until re-backfilled.")
    except Exception as e:
        lines.append(f"  resolutions vs chain: SKIPPED ({str(e)[:80]})")
    try:
        addrs = rng.sample(list(histories), min(n_fills, len(histories)))
        for addr in addrs:
            raw = await client._request(
                "GET", "/activity", client.data_api,
                params={"user": addr, "limit": 50, "offset": 0, "type": "TRADE"},
                use_cache=False, cache_key=f"vfy:{addr}")
            page = normalize_activity(raw if isinstance(raw, list) else [])
            # Compare against the FULL cached history (first-run lesson: a
            # 1000-bets/day whale pushes 50 new fills in minutes, so matching
            # only the newest cached rows reads as 0/50 pure staleness).
            cached = {(t["marketId"], t["tokenId"], str(t["timestamp"]))
                      for t in histories[addr]["trades"]}
            hits = sum(1 for t in page if (t["marketId"], t["tokenId"], str(t["timestamp"])) in cached)
            lines.append(f"  live-refetch {addr[:10]}…: {hits}/{len(page)} of live first page "
                         f"in cached history (shortfall = trades since caching, not corruption)")
    except Exception as e:
        lines.append(f"  live-refetch check: SKIPPED ({str(e)[:80]})")
    lines.append("  per-fill OrderFilled chain audit (blockchain_client."
                 "query_exchange_order_filled_events): MANDATORY before any money "
                 "decision leaning on a specific trader.")
    return lines


def pooled_stats(qualified: dict, primary_cat: str, n_boot: int, seed: int,
                 restrict: Optional[set] = None) -> Optional[dict]:
    """Pooled P2 stats for one category over (optionally restricted) qualifiers."""
    rows, traders = [], set()
    for addr, q in qualified.items():
        if restrict is not None and addr not in restrict:
            continue
        for mid, edge, cat in q["p2_rows"]:
            if "cat:" + cat == primary_cat or primary_cat == "ALL":
                rows.append((mid, edge))
                traders.add(addr)
    if not rows:
        return None
    me = per_market_edges(rows)
    p, mean, upper = boot_stats(me, n_boot, seed)
    return {"p": p, "edge": mean, "upper95": upper, "n_markets": len(me),
            "n_traders": len(traders), "n_bets": len(rows)}


async def run(args) -> int:
    from dotenv import load_dotenv
    load_dotenv()
    from base_engine.data.database import Database
    from base_engine.data.polymarket_client import PolymarketClient

    split = parse_ts(args.split_date)
    robust_splits = [parse_ts(s) for s in args.robustness_splits.split(",") if s.strip()]
    if split is None or any(s is None for s in robust_splits):
        print("bad --split-date/--robustness-splits"); return 2

    os.makedirs(args.cache, exist_ok=True)
    db = Database()
    await db.init()
    client = PolymarketClient()
    try:
        async with client:
            print(f"[1/6] universe: leaderboard timePeriod=ALL orderBy={args.orders}…",
                  file=sys.stderr)
            universe = await fetch_universe(client, args.universe, args.orders.split(","))
            src_map = {u["address"]: u["src"] for u in universe}
            print(f"      {len(universe)} unique addresses "
                  f"({Counter(u['src'] for u in universe)})", file=sys.stderr)

            print(f"[2/6] histories (cache {args.cache}; throttle {args.rps}/s)…",
                  file=sys.stderr)
            histories: dict[str, dict] = {}
            n_partial = 0
            for i, u in enumerate(universe):
                if i and i % 25 == 0:
                    print(f"      …{i}/{len(universe)} ({n_partial} partial-failed)",
                          file=sys.stderr)
                allow_deepen = (args.deepen == "all"
                                or (args.deepen == "vol" and "VOL" in u["src"]))
                trades, status = await fetch_history(
                    client, u["address"], args.max_bets, args.rps, args.cache,
                    args.refresh, allow_deepen, args.hft_max_rate)
                if status == "partial":
                    n_partial += 1  # excluded entirely — never analyzed [rev]
                    continue
                if trades:
                    histories[u["address"]] = {"trades": trades, "status": status}

            print(f"[3/6] labels (indexed lookups)…", file=sys.stderr)
            n_hft = 0
            for a in list(histories):
                h = histories[a]
                for t in h["trades"]:
                    t["_ts"] = parse_ts(t.get("timestamp"))
                # bot/market-maker exclusion, analysis side (also catches
                # fully-cached bots from earlier runs)
                if h["status"] == "hft" or is_hft_history(h["trades"], args.hft_max_rate):
                    n_hft += 1
                    del histories[a]
            n_trunc = sum(1 for h in histories.values() if h["status"] == "truncated")
            keys = sorted({t.get("marketId") or "" for h in histories.values()
                           for t in h["trades"]} - {""})
            markets = await load_markets(db, keys, args.timeout)
            n_gamma = merge_gamma_cache(markets, keys, args.gamma_cache)

            per_trader: dict[str, list[dict]] = {}
            cov_n = {"P1": [0, 0], "P2": [0, 0]}  # [labeled, total]
            for addr, h in histories.items():
                ents = []
                for t in first_buys(h["trades"], args.pmin, args.pmax):
                    period = "P1" if t["_ts"] <= split else "P2"
                    cov_n[period][1] += 1
                    mkt = markets.get(t.get("marketId") or "")
                    o = label_outcome(t.get("tokenId") or "", t.get("side") or "", mkt)
                    if o is None:
                        continue
                    cov_n[period][0] += 1
                    ents.append({"_ts": t["_ts"], "marketId": t["marketId"],
                                 "edge": o - float(t["price"]),
                                 "cat": bucket_category((mkt or {}).get("category"))})
                if ents:
                    per_trader[addr] = ents

            print(f"[4/6] qualify on P1 (<= {split.date()}) / judge on P2…", file=sys.stderr)
            def _qualify(split_dt):
                out = {}
                for addr, ents in per_trader.items():
                    q = qualify_and_judge(ents, split_dt, args.min_p1_markets,
                                          args.p1_p_min, args.n_boot, args.seed)
                    if q:
                        out[addr] = q
                return out
            qualified = _qualify(split)
            for addr, q in qualified.items():
                q["profile"] = profile_trader(histories[addr]["trades"])
                q["src"] = src_map.get(addr, "?")
                q["truncated"] = histories[addr]["status"] == "truncated"

            # primary subuniverse: VOL-sourced AND non-truncated [rev]
            primary_set = {a for a, q in qualified.items()
                           if "VOL" in q["src"] and not q["truncated"]}

            print(f"[5/6] verdicts (+{len(robust_splits)} robustness splits)…",
                  file=sys.stderr)
            cat_stats = {}
            for c in {"ALL"} | {"cat:" + cat for q in qualified.values()
                                for _, _, cat in q["p2_rows"]}:
                s = pooled_stats(qualified, c, args.n_boot, args.seed + 1)
                if s:
                    cat_stats[c] = s
            prim_stats = pooled_stats(qualified, args.primary, args.n_boot,
                                      args.seed + 2, restrict=primary_set)
            robust_means = []
            for j, rs in enumerate(robust_splits):
                rq = _qualify(rs)
                rset = {a for a in rq
                        if "VOL" in src_map.get(a, "") and histories[a]["status"] != "truncated"}
                rstat = pooled_stats(rq, args.primary, args.n_boot,
                                     args.seed + 3 + j, restrict=rset)
                robust_means.append(rstat["edge"] if rstat else float("nan"))

            print(f"[6/6] verification samples…", file=sys.stderr)
            vlines = await verify_sample(db, client, markets, histories,
                                         args.verify_res, args.verify_fills, args.seed)
    finally:
        await db.close()

    verdict, detail = primary_verdict(prim_stats, args.p_min, args.min_markets,
                                      args.min_traders, args.econ_floor, robust_means)
    _report(args, universe, histories, n_partial, n_trunc, n_hft, n_gamma, cov_n, split,
            robust_splits, per_trader, qualified, primary_set, cat_stats,
            prim_stats, robust_means, verdict, detail, vlines)
    write_json_atomic(args.out, json_safe({
            "split": split, "robustness_splits": robust_splits,
            "verdict": verdict, "detail": detail,
            "primary_stats": prim_stats, "robust_means": robust_means,
            "cat_stats": cat_stats,
            "counts": {"universe": len(universe), "partial_failed": n_partial,
                       "truncated": n_trunc},
            "qualified": {a: {k: v for k, v in q.items() if k != "p2_rows"}
                          for a, q in qualified.items()},
        }), f, indent=1)
    print(f"full results -> {args.out}")
    return 0


def _report(args, universe, histories, n_partial, n_trunc, n_hft, n_gamma, cov_n, split,
            robust_splits, per_trader, qualified, primary_set, cat_stats,
            prim_stats, robust_means, verdict, detail, vlines) -> None:
    print("\n" + "=" * 100)
    print("  COPYABLE-TRADER SEARCH — full histories, qualify on P1, judge on P2")
    print(f"  universe={len(universe)} addrs (timePeriod=ALL, {args.orders})"
          f"  histories={len(histories)}  partial-FAILED(excluded)={n_partial}"
          f"  TRUNCATED(desc-only)={n_trunc}  BOT/HFT-excluded={n_hft}"
          f"  gamma-backfilled-labels={n_gamma:,}")
    c1, c2 = cov_n["P1"], cov_n["P2"]
    print(f"  label coverage: P1 {c1[0]:,}/{c1[1]:,}"
          f" ({(c1[0]/c1[1] if c1[1] else 0):.0%})   P2 {c2[0]:,}/{c2[1]:,}"
          f" ({(c2[0]/c2[1] if c2[1] else 0):.0%})   [asymmetry = ingestion skew — eyeball it]")
    print(f"  split={split.date()} (locked calendar)  robustness={[s.date().isoformat() for s in robust_splits]}")
    print(f"  qualify: >={args.min_p1_markets} distinct P1 markets AND P1 clustered"
          f" P(edge>0)>={args.p1_p_min}  (P2 never consulted)")
    print(f"  qualified: {len(qualified)} of {len(per_trader)} labeled traders"
          f"  | primary subuniverse (VOL-sourced, non-truncated): {len(primary_set)}")
    print("=" * 100)
    print(f"  {'category':<14}{'traders':>8}{'P2 bets':>9}{'P2 mkts':>9}"
          f"{'P2 edge':>10}{'P(>0)':>8}{'upper95':>9}   (descriptive — all qualifiers)")
    print("  " + "-" * 96)
    for c in sorted(cat_stats, key=lambda k: (k != "ALL", k)):
        s = cat_stats[c]
        print(f"  {c:<14}{s['n_traders']:>8}{s['n_bets']:>9}{s['n_markets']:>9}"
              f"{s['edge']:>+10.4f}{s['p']:>8.3f}{s['upper95']:>+9.4f}")
    print("  " + "-" * 96)
    print(f"  PRE-REGISTERED PRIMARY: {args.primary} pooled P2 edge over VOL-sourced,")
    print(f"  non-truncated qualifiers (PNL-board membership conditions on P2-era wins —")
    print(f"  a collider; VOL selects on activity, not outcomes).")
    if prim_stats:
        print(f"  primary cell: edge={prim_stats['edge']:+.4f}  P={prim_stats['p']:.3f}"
              f"  upper95={prim_stats['upper95']:+.4f}  mkts={prim_stats['n_markets']}"
              f"  traders={prim_stats['n_traders']}  robustness means={robust_means}")
    print(f"  VERDICT: {verdict} — {detail}")
    if verdict == "PASS":
        print("  NEXT (not a deploy): fill-quality on exactly these addresses — precise gate")
        print("  / forward shadow — plus the per-fill OrderFilled chain audit.")
    elif verdict == "FAIL-TERMINAL":
        print("  Complete histories, clean universe, optimistic bound below costs:")
        print("  the retrospective road for the copy thesis is closed.")
    print("-" * 100)
    top = sorted(qualified.items(), key=lambda kv: -kv[1]["p1_edge"])[:args.show]
    print(f"  top {len(top)} qualifiers by P1 edge (judged on P2; * = in primary subuniverse):")
    print(f"  {'address':<14}{'src':<9}{'cat':<9}{'P1mk':>5}{'P1 edge':>9}{'P1 P':>6}"
          f"{'P2mk':>5}{'P2 edge':>9}{'P2 P':>6}{'medpx':>7}{'b/day':>7}{'days':>6}")
    for addr, q in top:
        pr = q["profile"]
        star = "*" if addr in primary_set else " "
        print(f"  {addr[:12]:<13}{star}{q['src']:<9}{q['main_cat']:<9}"
              f"{q['n_p1_mkts']:>5}{q['p1_edge']:>+9.3f}{q['p1_p']:>6.2f}"
              f"{q['p2_mkts']:>5}{q['p2_edge']:>+9.3f}{q['p2_p']:>6.2f}"
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

    ok_ts = (parse_ts(1767225600) is not None and parse_ts("2026-01-01") == t0
             and parse_ts("2026-01-01T00:00:00Z") == t0 and parse_ts("junk") is None)
    print(f"  [ts] epoch/date/iso-Z/junk : {ok_ts}"); ok &= ok_ts

    raw = [{"type": "TRADE", "conditionId": "0xA", "asset": "T1", "side": "buy",
            "price": "0.5", "size": "10", "timestamp": 1767225600,
            "transactionHash": "0xh"}] * 3 + [{"type": "REDEEM"}]
    norm = normalize_activity(raw)
    ok_norm = len(norm) == 1 and norm[0]["side"] == "BUY" and norm[0]["marketId"] == "0xA"
    print(f"  [normalize] dedupe + fields + non-trades dropped : {ok_norm}"); ok &= ok_norm

    mkt = {"resolution": "YES", "yes_token_id": "T1", "no_token_id": "T2"}
    ok_lab = (label_outcome("T1", "BUY", mkt) == 1.0
              and label_outcome("T2", "BUY", mkt) == 0.0
              and label_outcome("T1", "BUY", None) is None)
    print(f"  [label] token/unknown mapping : {ok_lab}"); ok &= ok_lab

    rng = random.Random(3)
    split = datetime(2026, 2, 1)
    def mk(n, when, mu, tag):
        return [{"_ts": when, "marketId": f"m{tag}{i}", "cat": "sports",
                 "edge": rng.gauss(mu, 0.05)} for i in range(n)]
    lucky = mk(40, datetime(2026, 1, 10), 0.10, "a") + mk(40, datetime(2026, 5, 1), 0.0, "b")
    skilled = mk(40, datetime(2026, 1, 10), 0.10, "c") + mk(40, datetime(2026, 5, 1), 0.08, "d")
    noise = mk(40, datetime(2026, 1, 10), 0.005, "e") + mk(40, datetime(2026, 5, 1), 0.0, "f")
    q_lucky = qualify_and_judge(lucky, split, 25, 0.90, 500, 7)
    q_skill = qualify_and_judge(skilled, split, 25, 0.90, 500, 7)
    q_noise = qualify_and_judge(noise, split, 25, 0.90, 500, 7)
    ok_q = (q_lucky is not None and q_lucky["p2_p"] < 0.95
            and q_skill is not None and q_skill["p2_p"] > 0.95
            and q_noise is None)  # [rev] P1 bootstrap bar rejects noise traders
    print(f"  [qualify] lucky P2 P={q_lucky['p2_p']:.2f}(<.95) skilled "
          f"P={q_skill['p2_p']:.2f}(>.95) noise=rejected-at-P1 : {ok_q}"); ok &= ok_q

    base = {"p": 0.97, "edge": 0.03, "upper95": 0.05, "n_markets": 50,
            "n_traders": 8, "n_bets": 200}
    ok_v = (primary_verdict(base, 0.95, 30, 5, 0.02, [0.01, 0.02])[0] == "PASS"
            and primary_verdict(base, 0.95, 30, 5, 0.02, [0.01, -0.01])[0] == "INCONCLUSIVE"
            and primary_verdict(None, 0.95, 30, 5, 0.02, [])[0] == "NO-DATA"
            and primary_verdict({**base, "n_traders": 2}, 0.95, 30, 5, 0.02, [])[0] == "UNDERPOWERED"
            and primary_verdict({**base, "p": 0.60, "upper95": 0.001}, 0.95, 30, 5,
                                0.02, [])[0] == "FAIL-TERMINAL"
            and primary_verdict({**base, "p": 0.60, "upper95": 0.04}, 0.95, 30, 5,
                                0.02, [])[0] == "INCONCLUSIVE")
    print(f"  [verdict] PASS/robust-veto/NO-DATA/UNDERPOWERED/TERMINAL/INCONCLUSIVE : {ok_v}")
    ok &= ok_v

    ok_j = json.loads(json.dumps(json_safe({"a": float("nan"), "b": [1.0, float("inf")],
                                            "t": t0})))["a"] is None
    print(f"  [json] NaN/inf sanitized : {ok_j}"); ok &= ok_j

    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Find copyable traders (P1-qualify/P2-judge, review-hardened)")
    ap.add_argument("--universe", type=int, default=300)
    ap.add_argument("--orders", default="PNL,VOL")
    ap.add_argument("--max-bets", type=int, default=20000, dest="max_bets",
                    help="history cap; hitting it flags the trader TRUNCATED (excluded from primary)")
    ap.add_argument("--hft-max-rate", type=float, default=200.0, dest="hft_max_rate",
                    help="exclude accounts trading more than this many times/day "
                         "(bots/market-makers, mechanically uncopyable; 0 disables)")
    ap.add_argument("--gamma-cache", default="/tmp/copyable_cache/gamma_resolutions.json",
                    dest="gamma_cache",
                    help="resolutions backfill JSON (backfill_resolutions_gamma.py); fills DB label holes")
    ap.add_argument("--deepen", choices=["all", "vol", "none"], default="all",
                    help="re-pull TRUNCATED cache entries deeper when --max-bets allows: "
                         "all (default, correct), vol (only VOL-sourced — the primary "
                         "subuniverse, cheapest way to repower it), none (cache as-is)")
    ap.add_argument("--rps", type=float, default=4.0)
    ap.add_argument("--cache", default="/tmp/copyable_cache")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--split-date", default=SPLIT_DEFAULT, dest="split_date",
                    help=f"LOCKED calendar P1/P2 split (default {SPLIT_DEFAULT})")
    ap.add_argument("--robustness-splits", default=ROBUSTNESS_DEFAULT, dest="robustness_splits",
                    help="extra splits; primary PASS needs positive pooled edge at each")
    ap.add_argument("--min-p1-markets", type=int, default=25, dest="min_p1_markets",
                    help="min DISTINCT P1 markets to qualify (default 25)")
    ap.add_argument("--p1-p-min", type=float, default=0.90, dest="p1_p_min",
                    help="min P1 clustered bootstrap P(edge>0) to qualify (default 0.90)")
    ap.add_argument("--primary", default=PRIMARY_DEFAULT)
    ap.add_argument("--p-min", type=float, default=0.95, dest="p_min")
    ap.add_argument("--min-markets", type=int, default=30, dest="min_markets")
    ap.add_argument("--min-traders", type=int, default=5, dest="min_traders")
    ap.add_argument("--econ-floor", type=float, default=0.02, dest="econ_floor",
                    help="prob-points cost floor separating FAIL-TERMINAL from INCONCLUSIVE")
    ap.add_argument("--n-boot", type=int, default=2000, dest="n_boot")
    ap.add_argument("--pmin", type=float, default=0.02)
    ap.add_argument("--pmax", type=float, default=0.98)
    ap.add_argument("--show", type=int, default=30)
    ap.add_argument("--verify-res", type=int, default=20, dest="verify_res")
    ap.add_argument("--verify-fills", type=int, default=5, dest="verify_fills")
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--out", default="/tmp/copyable_traders.json")
    ap.add_argument("--seed", type=int, default=20260709)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        raise SystemExit(_self_test())
    raise SystemExit(asyncio.run(run(args)))
