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
from base_engine.data.resolution_observation import record_resolution_observation

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
    if closed and tokens and len(tokens) >= 2:
        # 2026-05-26: derive resolution from numeric token prices FIRST.
        # The winner flag has been observed to be missing or wrong for some
        # markets (chain-verification on 9-of-361 EB-family RESOLUTION rows
        # found bidirectional mismatches at write time). Token prices [1,0]
        # or [0,1] reliably reflect UMA on-chain settlement. Winner-flag
        # iteration kept as fallback only when prices are ambiguous.
        try:
            _p0 = float(tokens[0].get("price") or 0)
            _p1 = float(tokens[1].get("price") or 0)
            if _p0 >= 0.99 and _p1 <= 0.01:
                res = "YES"
            elif _p0 <= 0.01 and _p1 >= 0.99:
                res = "NO"
        except (ValueError, TypeError):
            pass
        # Fallback: winner flag (legacy path, kept for cases where token.price
        # is not populated on the CLOB response yet).
        if res is None:
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
        # Root fix: CLOB returns the end-date under snake_case "end_date_iso".
        # Include it (with camel fallbacks) so CLOB-sourced markets aren't stored
        # with a NULL end-date, which strands them in resolution_backfill's
        # NULLS-LAST queue. bulk_insert_markets parses the ISO string.
        "end_date_iso": clob.get("end_date_iso") or clob.get("endDateISO") or clob.get("endDate"),
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
                                # Root fix: carry the end-date (any source spelling) so this
                                # missing-market insert doesn't store a NULL end-date and
                                # then become invisible to Phase-2 (NULLS-LAST) resolution.
                                "end_date_iso": (m.get("end_date_iso") or m.get("endDateISO")
                                                 or m.get("endDateIso") or m.get("endDate") or m.get("end_date")),
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
            # S151: skip recently-checked markets so new ones get batch slots
            try:
                from config.settings import Settings as _settings_cls
                _recheck_h = getattr(_settings_cls, "RESOLUTION_RECHECK_INTERVAL_HOURS", 6)
            except Exception:
                _recheck_h = 6
            _recheck_filter = ""
            if _recheck_h > 0:
                _recheck_filter = (
                    "AND (tm.last_checked_at IS NULL "
                    "OR tm.last_checked_at < NOW() - INTERVAL '1 hour' * :recheck_h) "
                )
                _params["recheck_h"] = _recheck_h
            _priority_order = (
                "ORDER BY CASE WHEN m.end_date_iso < NOW() THEN 0 "
                "              WHEN m.end_date_iso IS NULL THEN 1 "
                "              ELSE 2 END, "
                "tm.last_checked_at ASC NULLS FIRST, "
                "m.end_date_iso ASC NULLS LAST, "
                "tm.first_trade_at ASC NULLS LAST "
            )
            pt_result = await session.execute(text(
                "SELECT tm.market_id FROM traded_markets tm "
                "LEFT JOIN markets m ON m.id = tm.market_id "
                "WHERE (tm.status = 'open' OR tm.resolved = FALSE) "
                + _recheck_filter
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
    # S157: Record check result with exponential backoff for failures.
    # Replaces always-stamp _touch_checked() bandaid.
    async def _record_check_result(market_id: str, success: bool = False, permanent: bool = False) -> None:
        try:
            async with db.get_session() as _lc:
                if success:
                    await _lc.execute(text("""
                        UPDATE traded_markets
                        SET last_checked_at = NOW(), check_fail_count = 0, resolution_status = 'resolved'
                        WHERE market_id = :mid
                    """), {"mid": market_id})
                elif permanent:
                    await _lc.execute(text("""
                        UPDATE traded_markets
                        SET last_checked_at = NOW() + INTERVAL '30 days',
                            check_fail_count = COALESCE(check_fail_count, 0) + 1,
                            resolution_status = 'dead_letter'
                        WHERE market_id = :mid
                    """), {"mid": market_id})
                else:
                    # Transient failure — exponential backoff: 1h→3h→9h→27h→7d cap
                    # Promote after 5 consecutive failures (count incremented to 5 here)
                    await _lc.execute(text("""
                        UPDATE traded_markets
                        SET last_checked_at = NOW() + make_interval(
                            hours => LEAST(power(3, LEAST(COALESCE(check_fail_count, 0), 4)), 168)::int
                        ),
                        check_fail_count = COALESCE(check_fail_count, 0) + 1,
                        resolution_status = CASE
                            WHEN COALESCE(check_fail_count, 0) + 1 >= 5 THEN 'dead_letter'
                            ELSE resolution_status
                        END
                        WHERE market_id = :mid
                    """), {"mid": market_id})
                await _lc.commit()
        except Exception as e:
            logger.warning("record_check_result_failed", market_id=market_id, error=str(e))

    async with client:
        for mid in market_ids:
            _api_checked = False  # S151: True if we got a valid API response
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
                        _api_checked = True  # CLOB responded (market just not closed)
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
                            except Exception as _ed_err:
                                logger.warning("clear_stale_end_date failed: mid=%s err=%s", mid, _ed_err)
                else:
                    m = await client.get_market(mid, use_cache=False)
                if not m or not isinstance(m, dict):
                    # S153: Always stamp last_checked_at after attempting API check,
                    # even on failure. Without this, failed markets stay NULL and
                    # re-queue every cycle via NULLS FIRST, starving the queue.
                    # They'll be retried after RESOLUTION_RECHECK_INTERVAL_HOURS (6h).
                    await _record_check_result(mid)
                    continue
                _api_checked = True  # Got a valid market dict from API

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
                    await _record_check_result(mid)
                    continue
                # 2026-05-26: outcome_prices (numeric, reliable) is now PRIMARY.
                # Text fields (resolution / outcome / resolutionPrice) are
                # fallback only. Polymarket gamma-api has been observed to
                # return null or stale-"Pending" text for some closed/settled
                # markets — outcome_prices numeric array reliably reflects
                # UMA on-chain settlement. Chain-verification on 9-of-361
                # EB-family RESOLUTION rows showed bidirectional mismatches
                # caused by trusting the text fields.
                res = _infer_resolution_from_outcome_prices(m)
                if res is None:
                    _text_res = m.get("resolution") or m.get("outcome") or m.get("resolutionPrice")
                    # Reject stale-text values that Polymarket sometimes leaves
                    # on settled markets (e.g. "Pending - market scheduled for
                    # May 20, 2026 at 06:00:00 UTC" on 0xb184cfef89).
                    if isinstance(_text_res, str):
                        _trimmed = _text_res.strip().lower()
                        if _trimmed.startswith("pending") or _trimmed in ("", "tbd", "n/a", "none", "null", "scheduled"):
                            _text_res = None
                    res = _text_res
                if not res or str(res).upper() not in ("YES", "NO"):
                    _skipped_no_res += 1
                    await _record_check_result(mid)
                    continue
                _source = "clob_api" if _from_clob else "gamma_api"
                # Always NOW() — end_date_iso is scheduled close, not resolution moment.
                _resolved_at = record_resolution_observation(
                    datetime.now(timezone.utc).replace(tzinfo=None),
                    market_id=str(mid),
                    scheduled_close=_end_dt_parsed,
                    source="resolution_backfill",
                )
                await db.save_market_resolution(mid, True, str(res).upper(), _source, _resolved_at)
                # Update traded_markets index + write RESOLUTION events
                try:
                    await db.mark_market_resolved(mid, str(res).upper())
                except Exception as _mark_err:
                    logger.warning("mark_market_resolved failed for %s: %s", str(mid)[:20], _mark_err)
                await _record_check_result(mid, success=True)
                updated += 1
                if log_progress and updated % 50 == 0:
                    logger.info("Resolution backfill: updated %d resolutions", updated)
            except Exception as _e:
                if log_progress:
                    logger.warning("Resolution backfill: market %s error: %s", str(mid)[:20], _e, exc_info=True)
                # S153: Stamp last_checked_at on error so broken markets don't
                # re-queue every cycle. They'll be retried after recheck interval.
                await _record_check_result(mid)
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

    # Phase 3c (S172 7B Phase A3): Backfill mirror_rejected_signals resolution.
    mrs_updated = 0
    try:
        mrs_updated = await db.backfill_mirror_rejected_signals_resolution()
        result["mirror_rejected_signals_updated"] = mrs_updated
        if log_progress and mrs_updated > 0:
            logger.info("Mirror rejected signals resolution backfill: %d rows updated", mrs_updated)
    except Exception as e:
        logger.debug("Mirror rejected signals backfill failed (non-fatal): %s", e)

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

    # Phase 4b: Emit RESOLUTION events to trade_events audit trail.
    # Lifted into Database.backfill_trade_events_resolution() — same SQL,
    # same NOT EXISTS guards, but now reachable from 3 paths (full backfill,
    # mini scheduler, on_resolution event) and runs unconditionally instead
    # of being gated by paper_updated/updated. Includes Phase 4b-alt
    # (positions-driven) in one call.
    try:
        _te_emitted = await db.backfill_trade_events_resolution()
        result["trade_events_resolution_emitted"] = _te_emitted
        if log_progress and _te_emitted > 0:
            logger.info("Resolution backfill: %d RESOLUTION events emitted to trade_events", _te_emitted)
    except Exception as _te_err:
        logger.warning("Resolution backfill: trade_events emission failed: %s", _te_err)

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
