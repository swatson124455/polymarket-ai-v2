"""
Resolution backfill: fetch missing markets and backfill resolution for markets with trades.
Callable from IngestionScheduler (optimal flow automation) or scripts/backfill_market_resolution.py.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from structlog import get_logger
from base_engine.data.data_ingestion import _infer_category

logger = get_logger()


async def _fetch_market_by_condition_id(condition_id: str) -> Optional[Dict[str, Any]]:
    """Try CLOB API when Gamma may not support condition_id."""
    try:
        import httpx
        url = f"https://clob.polymarket.com/markets/{condition_id}"
        async with httpx.AsyncClient(timeout=15.0) as h:
            r = await h.get(url)
            if r.status_code == 200:
                return r.json()
    except Exception as _clob_err:
        logger.debug("CLOB condition_id fetch failed for %s: %s", condition_id[:20], _clob_err)
    return None


def _clob_to_market_format(clob: dict, condition_id: str) -> dict:
    """Transform CLOB API market to our market format.

    Extracts token IDs, prices, and active/closed status from the CLOB response.
    Previously hardcoded liquidity=0, volume=0, no prices — leaving markets invisible
    to any bot that gates on price or liquidity.
    """
    tokens = clob.get("tokens") or []
    yes_tid = no_tid = None
    yes_price = no_price = None
    for t in tokens:
        o = (t.get("outcome") or "").upper()
        tid = str(t.get("token_id") or "").strip()
        if not tid:
            continue
        # Extract price from token (CLOB API returns "price" per token)
        _price = t.get("price")
        if _price is not None:
            try:
                _price = float(_price)
            except (ValueError, TypeError):
                _price = None
        if o in ("YES", "YES "):
            yes_tid = tid
            yes_price = _price
        elif o in ("NO", "NO "):
            no_tid = tid
            no_price = _price
    if not yes_tid and len(tokens) >= 1:
        yes_tid = str(tokens[0].get("token_id") or "").strip()
        if yes_price is None:
            _p = tokens[0].get("price")
            if _p is not None:
                try:
                    yes_price = float(_p)
                except (ValueError, TypeError):
                    pass
    if not no_tid and len(tokens) >= 2:
        no_tid = str(tokens[1].get("token_id") or "").strip()
        if no_price is None:
            _p = tokens[1].get("price")
            if _p is not None:
                try:
                    no_price = float(_p)
                except (ValueError, TypeError):
                    pass
    closed = clob.get("closed", False)
    res = None
    if closed and tokens:
        for idx, t in enumerate(tokens):
            if t.get("winner"):
                o = (t.get("outcome") or "").upper()
                if "YES" in o or o == "YES":
                    res = "YES"
                    break
                if "NO" in o or o == "NO":
                    res = "NO"
                    break
                # S85 FIX: Non-YES/NO outcomes (team names in esports/sports).
                # First token = YES outcome, second token = NO outcome.
                res = "YES" if idx == 0 else "NO"
                break
    # Volume: CLOB may provide volume or we leave 0.0 (refreshed later by EsportsMarketService)
    vol = 0.0
    for vk in ("volume", "volumeNum", "volume_num"):
        _v = clob.get(vk)
        if _v is not None:
            try:
                vol = float(_v)
                break
            except (ValueError, TypeError):
                pass
    return {
        "id": condition_id,
        "condition_id": condition_id,
        "question": clob.get("question") or "",
        "slug": clob.get("market_slug") or "",
        "category": _infer_category(clob.get("question", "") or ""),
        "liquidity": 0.0,  # CLOB markets have no AMM liquidity by design
        "volume": vol,
        "closed": closed,  # S85 FIX: Phase 2 checks m.get("closed")
        "resolved": bool(res),
        "resolution": res,
        "yes_token_id": yes_tid,
        "no_token_id": no_tid,
        "yes_price": yes_price,
        "no_price": no_price,
        "active": not closed,  # Mark closed markets as inactive immediately
    }


def _infer_resolution_from_outcome_prices(m: dict) -> Optional[str]:
    """Infer YES/NO from outcomePrices when resolution missing."""
    op = m.get("outcomePrices")
    if isinstance(op, str):
        try:
            op = json.loads(op) if op.strip().startswith("[") else op.split(",")
        except json.JSONDecodeError:
            op = op.split(",") if "," in op else []
    if isinstance(op, (list, tuple)) and len(op) >= 2:
        p0, p1 = float(op[0] or 0), float(op[1] or 0)
        if p0 >= 0.99 and p1 <= 0.01:
            return "YES"
        if p0 <= 0.01 and p1 >= 0.99:
            return "NO"
    return None


async def run_resolution_backfill(
    db,
    client,
    *,
    missing_limit: int = 200,
    resolution_limit: int = 500,
    delay_seconds: float = 0.03,
    log_progress: bool = True,
    priority_bot: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Run resolution backfill: fetch missing markets, then backfill resolution.
    Returns dict with inserted, updated, and any error.
    """
    from sqlalchemy import text
    from base_engine.data.market_parser_v2 import MarketParserV2

    result: Dict[str, Any] = {"inserted": 0, "updated": 0, "error": None}

    if not db.session_factory:
        result["error"] = "Database not initialized"
        return result

    # Phase 1: Fetch and insert missing markets
    async with db.get_session() as session:
        missing = await session.execute(text("""
            SELECT DISTINCT market_id FROM (
                SELECT t.market_id FROM trades t WHERE t.market_id IS NOT NULL AND t.market_id != ''
                UNION
                SELECT pt.market_id FROM paper_trades pt WHERE pt.market_id IS NOT NULL AND pt.market_id != ''
            ) combined
            WHERE NOT EXISTS (
                SELECT 1 FROM markets m
                WHERE CAST(m.id AS TEXT) = market_id OR m.condition_id = market_id OR m.slug = market_id
            )
            LIMIT :lim
        """), {"lim": missing_limit})
        missing_ids = [r[0] for r in missing.fetchall() if r[0]]

    inserted = 0
    if missing_ids:
        if log_progress:
            logger.info("Resolution backfill: fetching %d missing markets", len(missing_ids))
        async with client:
            for mid in missing_ids:
                try:
                    m = None
                    try:
                        m = await client.get_market(mid, use_cache=False)
                    except Exception as _gamma_err:
                        logger.debug("Resolution backfill: Gamma fetch failed for %s: %s", str(mid)[:20], _gamma_err)
                    if m and isinstance(m, dict):
                        parsed = MarketParserV2.parse_market(m)
                        if parsed:
                            md = {
                                "id": str(m.get("id") or mid),
                                "condition_id": parsed.get("condition_id") or (mid if str(mid).startswith("0x") else ""),
                                "question": parsed.get("question") or "",
                                "slug": parsed.get("slug") or "",
                                "category": m.get("category") or _infer_category(parsed.get("question") or m.get("title") or ""),
                                "liquidity": float(m.get("liquidity") or 0),
                                "volume": float(m.get("volume") or 0),
                                "resolved": bool(m.get("closed") or m.get("resolved")),
                                "resolution": None,
                                "yes_token_id": parsed.get("yes_token_id"),
                                "no_token_id": parsed.get("no_token_id"),
                            }
                            if m.get("closed") or m.get("resolved"):
                                md["resolution"] = _infer_resolution_from_outcome_prices(m)
                            await db.bulk_insert_markets([md])
                            inserted += 1
                            if log_progress and inserted % 25 == 0:
                                logger.info("Resolution backfill: inserted %d missing markets", inserted)
                    if not m and str(mid).startswith("0x") and len(str(mid)) == 66:
                        clob = await _fetch_market_by_condition_id(mid)
                        if clob:
                            md = _clob_to_market_format(clob, mid)
                            await db.bulk_insert_markets([md])
                            inserted += 1
                except Exception as e:
                    logger.debug("Resolution backfill: failed market %s: %s", mid[:20], e)
                await asyncio.sleep(delay_seconds)
        if inserted and log_progress:
            logger.info("Resolution backfill: inserted %d missing markets", inserted)

    result["inserted"] = inserted

    # Phase 2: Backfill resolution — OUR paper trades first, then on-chain trades.
    # Paper trade markets get priority but still respect resolution_limit to avoid
    # excessive API calls (1000+ unresolved markets × 2 API calls each = too slow).
    async with db.get_session() as session:
        # 2a: Markets WE traded on — from traded_markets table
        # S92: priority_bot puts that bot's markets first via ORDER BY
        try:
            _params: dict = {"lim": resolution_limit}
            # S125: Expired-first ordering to prevent queue starvation.
            # Root cause: priority_bot gave one bot (e.g. MirrorBot, 953 markets)
            # all batch slots. Most were still-open (end-of-month), consuming the
            # batch and starving other bots (WeatherBot) whose markets were
            # already closed. Fix: order by end_date_iso ASC (expired first),
            # then first_trade_at ASC. No more priority_bot preference — the
            # 500-batch with fair ordering resolves all bots proportionally.
            _priority_order = (
                "ORDER BY CASE WHEN m.end_date_iso < NOW() THEN 0 "
                "              WHEN m.end_date_iso IS NULL THEN 1 "
                "              ELSE 2 END, "
                "m.end_date_iso ASC NULLS LAST, "
                "tm.first_trade_at ASC NULLS LAST "
            )
            pt_result = await session.execute(text(
                "SELECT tm.market_id FROM traded_markets tm "
                "LEFT JOIN markets m ON m.id = tm.market_id "
                "WHERE tm.status = 'open' OR tm.resolved = FALSE "
                + _priority_order + "LIMIT :lim"
            ), _params)
            paper_market_ids = [r[0] for r in pt_result.fetchall() if r[0]]
        except Exception:
            # Fallback if traded_markets table doesn't exist yet (pre-migration)
            pt_result = await session.execute(text("""
                SELECT DISTINCT m.id FROM markets m
                WHERE (m.resolution IS NULL OR m.resolution NOT IN ('YES', 'NO'))
                AND EXISTS (
                    SELECT 1 FROM paper_trades pt
                    WHERE CAST(pt.market_id AS TEXT) = CAST(m.id AS TEXT) OR pt.market_id = m.condition_id
                )
            """))
            paper_market_ids = [r[0] for r in pt_result.fetchall() if r[0]]
        _paper_set = set(paper_market_ids)

        # 2b: On-chain trades markets — fill remaining slots
        _remaining = max(0, resolution_limit - len(paper_market_ids))
        other_ids: list = []
        if _remaining > 0:
            ot_result = await session.execute(text("""
                SELECT DISTINCT m.id, m.end_date_iso FROM markets m
                WHERE (m.resolution IS NULL OR m.resolution NOT IN ('YES', 'NO'))
                AND EXISTS (
                    SELECT 1 FROM trades t
                    WHERE t.market_id = CAST(m.id AS TEXT) OR t.market_id = m.condition_id
                )
                ORDER BY m.end_date_iso ASC NULLS LAST
                LIMIT :lim
            """), {"lim": _remaining})
            other_ids = [r[0] for r in ot_result.fetchall() if r[0] and r[0] not in _paper_set]

        market_ids = paper_market_ids + other_ids

    if not market_ids:
        if log_progress:
            logger.debug("Resolution backfill: no markets need resolution")
        return result

    if log_progress:
        logger.info("Resolution backfill: backfilling resolution for %d markets", len(market_ids))

    updated = 0
    end_date_patched = 0
    _skipped_open = 0
    _skipped_no_res = 0
    _clob_closed = 0
    async with client:
        for mid in market_ids:
            try:
                _from_clob = False
                _is_condition_id = str(mid).startswith("0x") and len(str(mid)) == 66
                # S85 FIX: Skip Gamma entirely for condition_id markets — Gamma
                # ALWAYS returns 422 for them, wasting an API call per market.
                # Go straight to CLOB API which has correct closure/winner data.
                if _is_condition_id:
                    _clob = await _fetch_market_by_condition_id(mid)
                    if _clob and _clob.get("closed"):
                        m = _clob_to_market_format(_clob, mid)
                        _from_clob = True
                        _clob_closed += 1
                    else:
                        m = None  # Market not closed or CLOB unavailable
                        _skipped_open += 1
                        # S125-mirror: If CLOB confirms market is still open but DB has
                        # a bogus end_date_iso in the past (e.g. 2020-11-04 from CLOB
                        # import without endDateISO), clear it so this market stops
                        # consuming priority-0 backfill slots every cycle.
                        if _clob and not _clob.get("closed"):
                            try:
                                async with db.get_session() as _ed_sess:
                                    await _ed_sess.execute(
                                        text("UPDATE markets SET end_date_iso = NULL "
                                             "WHERE id = :mid AND end_date_iso < NOW() - INTERVAL '30 days'"),
                                        {"mid": mid},
                                    )
                                    await _ed_sess.commit()
                            except Exception:
                                pass  # best-effort, non-fatal
                else:
                    m = await client.get_market(mid, use_cache=False)
                if not m or not isinstance(m, dict):
                    continue

                # Opportunistically backfill end_date_iso if missing in DB.
                # Gamma API returns "endDateIso" (lowercase 'so'), "endDate".
                # CLOB API returns "endDateISO" (uppercase 'ISO').
                # Check all variants to catch all sources.
                _end_raw = (m.get("endDateISO") or m.get("endDateIso")
                            or m.get("endDate") or m.get("end_date")
                            or m.get("end_date_iso"))
                _end_dt_parsed = None
                if _end_raw:
                    try:
                        _ds = str(_end_raw)
                        _end_dt_parsed = datetime.fromisoformat(_ds.replace("Z", "+00:00"))
                        _end_dt_parsed = _end_dt_parsed.replace(tzinfo=None)
                        async with db.get_session() as _s:
                            await _s.execute(
                                text("UPDATE markets SET end_date_iso=:ed WHERE id=:mid AND end_date_iso IS NULL"),
                                {"ed": _end_dt_parsed, "mid": mid},
                            )
                            await _s.commit()
                        end_date_patched += 1
                    except Exception as _ed_err:
                        logger.debug("Resolution backfill: end_date patch failed for %s: %s", str(mid)[:20], _ed_err)

                closed = m.get("closed") or m.get("isResolved") or m.get("resolved")
                if not closed:
                    _skipped_open += 1
                    continue
                res = m.get("resolution") or m.get("outcome") or m.get("resolutionPrice")
                if res is None:
                    res = _infer_resolution_from_outcome_prices(m)
                if not res or str(res).upper() not in ("YES", "NO"):
                    _skipped_no_res += 1
                    continue
                _source = "clob_api" if _from_clob else "gamma_api"
                # Pass end_date as resolved_at; fallback to now() so resolved_at is never NULL
                _resolved_at = _end_dt_parsed or datetime.now(timezone.utc).replace(tzinfo=None)
                await db.save_market_resolution(mid, True, str(res).upper(), _source, _resolved_at)
                # Update traded_markets index + write RESOLUTION events
                try:
                    await db.mark_market_resolved(mid, str(res).upper())
                except Exception as _mark_err:
                    logger.warning("mark_market_resolved failed for %s: %s", str(mid)[:20], _mark_err)
                updated += 1
                if log_progress and updated % 50 == 0:
                    logger.info("Resolution backfill: updated %d resolutions", updated)
            except Exception as _e:
                if log_progress:
                    logger.warning("Resolution backfill: market %s error: %s", str(mid)[:20], _e, exc_info=True)
            await asyncio.sleep(delay_seconds)

    if log_progress:
        logger.info("Resolution backfill phase 2 stats: clob_closed=%d skipped_open=%d skipped_no_res=%d updated=%d",
                     _clob_closed, _skipped_open, _skipped_no_res, updated)
    if log_progress and end_date_patched:
        logger.info("Resolution backfill: patched end_date_iso for %d markets", end_date_patched)

    result["updated"] = updated

    # Phase 3: Backfill prediction_log with resolution, was_correct (always run; idempotent)
    pred_updated = 0
    try:
        pred_updated = await db.backfill_prediction_log_resolution()
        result["prediction_log_updated"] = pred_updated
        if log_progress and pred_updated > 0:
            logger.info("Prediction log resolution backfill: %d rows updated", pred_updated)
    except Exception as e:
        logger.debug("Prediction log backfill failed (non-fatal): %s", e)

    # Phase 3b: Pseudo-label fallback — use closed SELL trade P&L when real resolution unavailable.
    # Implements delayed-label bridging from ML pipeline best practices: profitable exits imply
    # the directional prediction was correct. Only updates rows still missing was_correct.
    pseudo_updated = 0
    try:
        pseudo_updated = await db.backfill_prediction_log_from_closed_trades()
        result["prediction_log_pseudo_updated"] = pseudo_updated
        if log_progress and pseudo_updated > 0:
            logger.info(
                "Prediction log pseudo-label backfill: %d rows updated from closed trades",
                pseudo_updated,
            )
    except Exception as e:
        logger.debug("Prediction log pseudo-label backfill failed (non-fatal): %s", e)

    # Phase 4: Backfill paper_trades with resolution and realized_pnl (SIMULATION_MODE hypothetical P&L)
    paper_updated = 0
    try:
        paper_updated = await db.backfill_paper_trades_resolution()
        result["paper_trades_updated"] = paper_updated
        if log_progress and paper_updated > 0:
            logger.info("Paper trades resolution backfill: %d rows updated", paper_updated)
    except Exception as e:
        logger.debug("Paper trades backfill failed (non-fatal): %s", e)

    # Phase 4b: Emit RESOLUTION events to trade_events audit trail
    # Aggregate per (market_id, bot_name, side) to avoid duplicates from multiple paper_trades.
    # Uses deterministic correlation_id + resolved_at so ON CONFLICT catches re-runs.
    # S109: Widen gate — emit when ANY market resolved (updated > 0), not just when
    # paper_trades were updated. Fixes 276 markets where condition_id mismatch caused
    # paper_updated=0 even though traded_markets was resolved.
    # S120: Skip fully-exited positions — if total EXIT size >= total ENTRY size,
    # the position was closed before resolution. P&L already captured by EXIT events.
    # Emitting a RESOLUTION would double-count the loss/gain.
    if (paper_updated > 0 or updated > 0) and hasattr(db, "insert_trade_event"):
        try:
            async with db.get_session() as _te_sess:
                from sqlalchemy import text as _te_text
                # S134: Source ENTRY size/price from trade_events (immutable)
                # instead of paper_trades (mutable UPSERT overwrites size).
                # Fixes RESOLUTION event size inflation → phantom P&L.
                _resolved = await _te_sess.execute(_te_text(
                    "SELECT e.market_id, e.bot_name, e.side, "
                    "       pt_pnl.total_pnl, "
                    "       pt_pnl.resolved_at, "
                    "       e.total_entry_size - COALESCE(x.total_exit_size, 0) "
                    "         AS remaining_size, "
                    "       CASE WHEN e.total_entry_size > 0 "
                    "            THEN e.weighted_price / e.total_entry_size "
                    "            ELSE 0.0 END AS avg_entry_price, "
                    "       COALESCE(x.exit_pnl, 0) AS exit_pnl_already "
                    "FROM ("
                    "  SELECT market_id, bot_name, side, "
                    "         SUM(size) AS total_entry_size, "
                    "         SUM(price * size) AS weighted_price "
                    "  FROM trade_events "
                    "  WHERE event_type = 'ENTRY' AND side IN ('YES', 'NO') "
                    "  GROUP BY market_id, bot_name, side"
                    ") e "
                    "JOIN ("
                    "  SELECT market_id, bot_name, side, "
                    "         SUM(realized_pnl) AS total_pnl, "
                    "         MIN(resolved_at) AS resolved_at "
                    "  FROM paper_trades "
                    "  WHERE resolution IN ('YES', 'NO') "
                    "    AND side IN ('YES', 'NO') "
                    "    AND resolved_at IS NOT NULL "
                    "  GROUP BY market_id, bot_name, side"
                    ") pt_pnl ON pt_pnl.market_id = e.market_id "
                    "        AND pt_pnl.bot_name = e.bot_name "
                    "        AND pt_pnl.side = e.side "
                    "LEFT JOIN ("
                    "  SELECT market_id, bot_name, "
                    "         SUM(size) AS total_exit_size, "
                    "         SUM(realized_pnl) AS exit_pnl "
                    "  FROM trade_events "
                    "  WHERE event_type = 'EXIT' "
                    "  GROUP BY market_id, bot_name"
                    ") x ON x.market_id = e.market_id "
                    "    AND x.bot_name = e.bot_name "
                    "WHERE NOT EXISTS ("
                    "  SELECT 1 FROM trade_events te "
                    "  WHERE te.market_id = e.market_id "
                    "    AND te.bot_name = e.bot_name "
                    "    AND te.event_type = 'RESOLUTION'"
                    ") "
                    "AND e.total_entry_size - COALESCE(x.total_exit_size, 0) > 0 "
                    "LIMIT 500"
                ))
                for row in _resolved.fetchall():
                    try:
                        _entry_price = float(row[6]) if row[6] is not None else 0.0
                        _raw_pnl = float(row[3]) if row[3] is not None else None
                        _exit_pnl = float(row[7]) if row[7] is not None else 0.0
                        _adj_pnl = (_raw_pnl - _exit_pnl) if _raw_pnl is not None else None
                        if _exit_pnl != 0:
                            logger.info("phase4b_exit_pnl_subtracted", market=str(row[0])[:20],
                                        bot=row[1], raw_pnl=round(_raw_pnl, 4), exit_pnl=round(_exit_pnl, 4))
                        await db.insert_trade_event(
                            event_type="RESOLUTION",
                            bot_name=row[1],
                            market_id=row[0],
                            side=row[2],
                            size=float(row[5]) if row[5] is not None else 0.0,
                            price=_entry_price,
                            realized_pnl=_adj_pnl,
                            correlation_id=f"resolution:{row[0]}",
                            event_time=row[4],
                        )
                    except Exception as _te_err:
                        logger.debug("Resolution backfill: trade_event emission failed for market %s: %s", str(row[0])[:20], _te_err)
        except Exception as _te_outer_err:
            logger.warning("Resolution backfill: Phase 4b trade_event emission failed: %s", _te_outer_err)

    # Phase 4b-alt: Position-based RESOLUTION events for positions not covered by Phase 4b.
    # Phase 4b joins paper_trades → trade_events, but positions.market_id may differ from
    # paper_trades.market_id (condition_id vs Gamma ID mismatch). This path queries the
    # positions table directly for closed positions on resolved markets that still lack
    # a RESOLUTION event. Subtracts already-captured EXIT P&L to avoid double-counting.
    _pos_res_emitted = 0
    if hasattr(db, "insert_trade_event"):
        try:
            _fee_rate = getattr(
                __import__("base_engine.config.settings", fromlist=["settings"]),
                "TAKER_FEE_BPS", 150,
            ) / 10000.0
            async with db.get_session() as _pr_sess:
                from sqlalchemy import text as _pr_text
                _pos_resolved = await _pr_sess.execute(_pr_text(
                    "SELECT p.market_id, p.source_bot, p.side, p.size, p.entry_price, "
                    "       m.resolution, "
                    "       COALESCE(m.resolved_at, m.end_date_iso) AS resolved_at, "
                    "       COALESCE(("
                    "         SELECT SUM(te_x.realized_pnl) FROM trade_events te_x "
                    "         WHERE te_x.market_id = p.market_id "
                    "           AND te_x.bot_name = p.source_bot "
                    "           AND te_x.event_type = 'EXIT'"
                    "       ), 0) AS exit_pnl_already "
                    "FROM positions p "
                    "JOIN markets m ON (p.market_id = CAST(m.id AS TEXT) "
                    "                   OR p.market_id = m.condition_id) "
                    "WHERE p.status = 'closed' "
                    "  AND m.resolution IN ('YES', 'NO') "
                    "  AND NOT EXISTS ("
                    "    SELECT 1 FROM trade_events te "
                    "    WHERE te.market_id = p.market_id "
                    "      AND te.bot_name = p.source_bot "
                    "      AND te.event_type = 'RESOLUTION'"
                    "  ) "
                    "ORDER BY p.id "
                    "LIMIT 500"
                ))
                for row in _pos_resolved.fetchall():
                    try:
                        _mid, _bot, _side, _size, _entry, _res, _res_at, _exit_pnl = row
                        _side_upper = str(_side).upper() if _side else ""
                        _res_upper = str(_res).upper()
                        if _side_upper == _res_upper:
                            _payout = 1.0
                        else:
                            _payout = 0.0
                        _gross_pnl = (_payout - float(_entry)) * float(_size)
                        _fee = _payout * float(_size) * _fee_rate
                        _net_pnl = _gross_pnl - _fee
                        # Subtract EXIT P&L already captured to avoid double-counting
                        _remaining_pnl = _net_pnl - float(_exit_pnl)
                        await db.insert_trade_event(
                            event_type="RESOLUTION",
                            bot_name=_bot,
                            market_id=_mid,
                            side=_side_upper,
                            size=float(_size),
                            price=float(_entry),
                            realized_pnl=round(_remaining_pnl, 4),
                            correlation_id=f"resolution:{_mid}",
                            event_time=_res_at,
                        )
                        _pos_res_emitted += 1
                    except Exception as _pr_err:
                        logger.debug(
                            "Resolution backfill 4b-alt: emission failed for %s/%s: %s",
                            str(row[0])[:20], row[1], _pr_err,
                        )
            if _pos_res_emitted and log_progress:
                logger.info(
                    "Resolution backfill Phase 4b-alt: emitted %d RESOLUTION events from positions",
                    _pos_res_emitted,
                )
            result["pos_resolution_emitted"] = _pos_res_emitted
        except Exception as _pr_outer_err:
            logger.warning("Resolution backfill: Phase 4b-alt failed: %s", _pr_outer_err)

    # Phase 4c: Backfill shadow_fills with resolution data
    shadow_updated = 0
    try:
        if hasattr(db, "backfill_shadow_resolution"):
            shadow_updated = await db.backfill_shadow_resolution()
            result["shadow_fills_updated"] = shadow_updated
            if log_progress and shadow_updated > 0:
                logger.info("Shadow fills resolution backfill: %d rows updated", shadow_updated)
    except Exception as e:
        logger.debug("Shadow fills backfill failed (non-fatal): %s", e)

    # Phase 5: Backfill positions with unrealized_pnl from resolution data
    # CRITICAL: Fixes CLV, win rate, and Total P&L for resolution-based exits
    pos_updated = 0
    try:
        pos_updated = await db.backfill_positions_resolution()
        result["positions_pnl_updated"] = pos_updated
        if log_progress and pos_updated > 0:
            logger.info("Positions P&L resolution backfill: %d rows updated", pos_updated)
    except Exception as e:
        logger.debug("Positions P&L backfill failed (non-fatal): %s", e)

    # Phase 6: Score resolved paper trades via PerformanceTracker (prediction accuracy feedback loop)
    perf_scored = 0
    try:
        performance_tracker = kwargs.get("performance_tracker")
        if performance_tracker and paper_updated > 0:
            from sqlalchemy import text as _text
            async with db.get_session() as session:
                # L7 FIX: JOIN markets to get resolved_at / end_date_iso for proper exit_time.
                # Without this, hold_time_hours is always 0 and time-to-resolution is unusable.
                rows = await session.execute(_text("""
                    SELECT pt.order_id, pt.bot_name, pt.market_id, pt.price, pt.size,
                           pt.side, pt.created_at, pt.resolution, pt.realized_pnl,
                           COALESCE(m.resolved_at, m.end_date_iso) as market_end_time
                    FROM paper_trades pt
                    LEFT JOIN markets m ON (pt.market_id = CAST(m.id AS TEXT) OR pt.market_id = m.condition_id)
                    WHERE pt.resolution IS NOT NULL AND pt.realized_pnl IS NOT NULL
                      AND pt.side IN ('YES', 'NO')
                    ORDER BY pt.created_at DESC
                    LIMIT :lim
                """), {"lim": paper_updated + 50})
                for row in rows.fetchall():
                    try:
                        entry_price = float(row[3] or 0)
                        pnl = float(row[8] or 0)
                        exit_price = entry_price + (pnl / float(row[4] or 1)) if float(row[4] or 0) > 0 else entry_price
                        # L7: Use market resolution/end time as exit_time (not created_at)
                        exit_time = row[9] if row[9] is not None else row[6]
                        await performance_tracker.record_trade_outcome(
                            trade_id=str(row[0] or ""),
                            bot_name=str(row[1] or "unknown"),
                            market_id=str(row[2] or ""),
                            entry_price=entry_price,
                            exit_price=exit_price,
                            entry_time=row[6],
                            exit_time=exit_time,
                            profit=pnl,
                        )
                        perf_scored += 1
                    except Exception as _perf_err:
                        logger.debug("Resolution backfill: perf scoring failed for market %s: %s", str(row[0])[:20], _perf_err)
            result["performance_scored"] = perf_scored
            if log_progress and perf_scored > 0:
                logger.info("PerformanceTracker: scored %d resolved paper trades", perf_scored)
    except Exception as e:
        logger.debug("PerformanceTracker scoring failed (non-fatal): %s", e)

    # Phase 7: Trigger online learning for newly resolved positions
    # Feeds resolved trades to learning engine immediately (don't wait for 6h scheduler)
    if pos_updated > 0:
        try:
            learning_engine = kwargs.get("learning_engine")
            if learning_engine and hasattr(learning_engine, "learn_from_trades"):
                since = datetime.now(timezone.utc) - timedelta(hours=1)
                recent_trades = await db.get_trades_since(since)
                if recent_trades:
                    await learning_engine.learn_from_trades(recent_trades)
                    result["online_learning_trades"] = len(recent_trades)
                    if log_progress:
                        logger.info("Online learning: fed %d resolved trades immediately", len(recent_trades))
        except Exception as e:
            logger.debug("Online learning in backfill failed (non-fatal): %s", e)

    if log_progress and (inserted > 0 or updated > 0 or pred_updated > 0 or paper_updated > 0 or pos_updated > 0):
        logger.info(
            "Resolution backfill complete: %d inserted, %d updated, %d prediction_log, %d paper_trades, %d positions_pnl",
            inserted, updated, pred_updated, paper_updated, pos_updated,
        )

    # Phase 8: Audit trade_events for impossible states (read-only, non-fatal)
    try:
        from base_engine.data.trade_event_audit import audit_trade_events
        audit_result = await audit_trade_events(db)
        result["audit"] = audit_result
    except Exception as e:
        logger.debug("Trade event audit failed (non-fatal): %s", e)

    return result
