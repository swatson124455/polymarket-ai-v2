#!/usr/bin/env python3
"""R0b — FILTERED VENUE CENSUS (money ladder rung 0b, roadmap 2026-08-13 §3). READ-ONLY.

All active liquidity programs x [close<=8d entry rule; mechanism toxicity; expected
$/day = pool x share_formula at MIN size and at TARGET size]. Output: the true
addressable $/day + ranked candidates. GET-only; places/cancels/modifies NOTHING.

Share formula = replica of maker_kalshi_quoter._qualifying_score semantics
(R4 walk: ref = best bid < 1.0; walk desc accumulating to Target; score = DF^N*size;
book that cannot reach Target pays NOBODY — the W10 zero-payer floor; prospective
share at reference = our_size / (rival_qualifying_total + our_size); R3: BOTH sides
must qualify; two-sided snapshot = (share_yes + share_no)/2 x pool).
NOTE (floor caveat, R1's question): at MIN size a side qualifies only if book+us
reaches Target — sub-target presence models $0 by W10 canon. R1 tests that live.

Toxicity by MECHANISM (not series name):
  announcement       title carries a discrete-actor announcement verb
  ladder_near_strike market has a strike AND book mid in (0.10, 0.90)
  resolution_prox    close_time within 24h of read (whole-life-near-resolution and
                     about-to-close markets; D3 <1h bucket -7.6c/ct, weather-near-
                     resolution class)
Empty-book side with NO reference: needs a hard price anchor (guardrail); such
markets are flagged anchor_needed, still counted (share modeled at target size as
if we set the reference).

Output: /tmp/R0B_VENUE_CENSUS.json + summary on stdout. Progress to stderr.
"""
import datetime
import json
import sys
import time

sys.path.insert(0, "/opt/pa2-maker-kalshi-live")
import kalshi_attribution_ledger as kal          # noqa: E402

TICK = 0.01
MIN_CT = 10.0            # R1-style min probe size, contracts
CLOSE8D_S = 8 * 86400.0
PROX_S = 24 * 3600.0
ANNOUNCE_KW = ("endorse", "mention", "say", "tweet", "announce", "withdraw",
               "resign", "nominat", "confirm", "veto", "adjourn", "recess",
               "statement", "pardon", "appoint", "drop out", "dropout", "dni")


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def parse_iso(s):
    dt = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)


def qwalk(levels, target, df, our_size):
    """(rival_qualifying_total, side_qualifies_with_us, ref) — R4 walk replica.
    levels = [(price$, count)] one side, bot is DOWN and 0 resting (asserted by the
    ritual) so the fetched book is rivals-only. Qualification counts OUR depth at
    reference toward Target (venue counts resting depth; quoter contract)."""
    lv = sorted(((p, c) for p, c in levels if c > 0), key=lambda x: -x[0])
    if not lv or lv[0][0] >= 1.0:
        # empty side (or absurd book): we would set the reference ourselves
        return 0.0, our_size >= target, None
    ref = lv[0][0]
    cum = total = 0.0
    for price, size in lv:
        n = round((ref - price) / TICK)
        total += (df ** n) * size
        cum += size
        if cum >= target:
            break
    return total, (cum + our_size) >= target, ref


def side_share(levels, target, df, our_size):
    rival, qual, ref = qwalk(levels, target, df, our_size)
    if not qual:
        return 0.0, ref, rival
    return our_size / (rival + our_size), ref, rival


def parse_book(ob):
    """Canon shape (quoter :1956/:6361): payload .get('orderbook_fp'), sides
    'yes_dollars'/'no_dollars' = [[price_str, size_str]] already in DOLLARS
    (plain names return None — *_fp/*_dollars canon). Returns yes-BID and
    no-BID levels."""
    o = ob.get("orderbook_fp") or {}
    def side(key):
        out = []
        for r in o.get(key) or []:
            try:
                p, c = float(r[0]), float(r[1])
            except (TypeError, ValueError, IndexError):
                continue
            if 0 < p < 1 and c > 0:
                out.append((p, c))
        return out
    return side("yes_dollars"), side("no_dollars")


def main():
    t0 = now_utc()
    d = kal.get(f"{kal.P}/incentive_programs?status=active&limit=10000")
    progs = d.get("incentive_programs") or []
    leftover_cursor = d.get("next_cursor") or d.get("cursor") or ""
    if leftover_cursor:
        print(f"ALARM: programs read NOT exhausted (cursor={leftover_cursor!r})",
              file=sys.stderr)
    progs = [p for p in progs
             if (p.get("incentive_type") or "liquidity") == "liquidity"
             and p.get("market_ticker")]
    read_ts = now_utc().isoformat()
    total_pool = sum((p.get("period_reward") or 0) / 10000.0 for p in progs)

    tickers = sorted({p["market_ticker"] for p in progs})
    meta = {}
    for i in range(0, len(tickers), 90):
        chunk = tickers[i:i + 90]
        dd = kal.get(f"{kal.P}/markets?limit=1000&tickers=" + ",".join(chunk))
        for m in dd.get("markets") or []:
            meta[m["ticker"]] = m
        print(f"meta {i + len(chunk)}/{len(tickers)}", file=sys.stderr)

    now = now_utc()
    rows, drops = [], {"no_meta": 0, "not_active": 0, "close_gt_8d": 0,
                       "no_close_time": 0, "toxic_announcement": 0,
                       "toxic_ladder_near_strike": 0, "toxic_resolution_prox": 0}
    for p in progs:
        t = p["market_ticker"]
        pool = (p.get("period_reward") or 0) / 10000.0
        m = meta.get(t)
        if not m:
            drops["no_meta"] += 1
            continue
        if (m.get("status") or "").lower() not in ("active", "open"):
            drops["not_active"] += 1
            continue
        ct = m.get("close_time")
        if not ct:
            drops["no_close_time"] += 1
            continue
        tt_close = (parse_iso(ct) - now).total_seconds()
        if tt_close > CLOSE8D_S:
            drops["close_gt_8d"] += 1
            continue
        title = ((m.get("title") or "") + " " + (m.get("subtitle") or "")).lower()
        yb = float(m.get("yes_bid_dollars") or 0) or (m.get("yes_bid") or 0) / 100.0
        ya = float(m.get("yes_ask_dollars") or 0) or (m.get("yes_ask") or 0) / 100.0
        mid = (yb + ya) / 2.0 if (yb and ya) else (yb or ya or None)
        tox = []
        if any(k in title for k in ANNOUNCE_KW):
            tox.append("announcement")
        strike = m.get("floor_strike") if m.get("floor_strike") is not None else m.get("cap_strike")
        if strike is not None and mid is not None and 0.10 < mid < 0.90:
            tox.append("ladder_near_strike")
        if 0 <= tt_close < PROX_S:
            tox.append("resolution_prox")
        if tox:
            for r in tox:
                drops[f"toxic_{r}"] += 1
            continue
        rows.append({"ticker": t, "pool": pool, "close": ct,
                     "days_to_close": round(tt_close / 86400.0, 2),
                     "target": float(p.get("target_size_fp") or 1000),
                     "df": (p.get("discount_factor_bps") or 5000) / 10000.0,
                     "mid": mid, "title": (m.get("title") or "")[:80]})

    print(f"survivors for book reads: {len(rows)} "
          f"(~{len(rows) * 0.6 / 60:.0f} min)", file=sys.stderr)
    for i, r in enumerate(rows):
        try:
            ob = kal.get(f"{kal.P}/markets/{r['ticker']}/orderbook")
            ylv, nlv = parse_book(ob)
        except Exception as e:
            r["book_error"] = str(e)[:80]
            r["usd_day_target"] = r["usd_day_min"] = None
            continue
        tgt, df = r["target"], r["df"]
        sy_t, ry, rvy = side_share(ylv, tgt, df, tgt)
        sn_t, rn, rvn = side_share(nlv, tgt, df, tgt)
        sy_m, _, _ = side_share(ylv, tgt, df, MIN_CT)
        sn_m, _, _ = side_share(nlv, tgt, df, MIN_CT)
        r["usd_day_target"] = round(r["pool"] * (sy_t + sn_t) / 2.0, 2)
        r["usd_day_min"] = round(r["pool"] * (sy_m + sn_m) / 2.0, 2)
        r["book"] = {"yes_ref": ry, "no_ref": rn,
                     "yes_rival_q": round(rvy, 1), "no_rival_q": round(rvn, 1),
                     "yes_levels": len(ylv), "no_levels": len(nlv)}
        r["anchor_needed"] = (ry is None) or (rn is None)
        r["capital_target_usd"] = round(
            tgt * ((r["mid"] or 0.5)) + tgt * (1 - (r["mid"] or 0.5)), 2)
        if i % 50 == 0:
            print(f"books {i}/{len(rows)}", file=sys.stderr)

    scored = [r for r in rows if r.get("usd_day_target") is not None]
    scored.sort(key=lambda r: -(r["usd_day_target"] or 0))
    addr_all = round(sum(r["usd_day_target"] or 0 for r in scored), 2)
    addr_12 = round(sum(r["usd_day_target"] or 0 for r in scored[:12]), 2)
    addr_min_all = round(sum(r["usd_day_min"] or 0 for r in scored), 2)
    out = {"generated": read_ts, "t_start": t0.isoformat(),
           "n_programs_active_liquidity": len(progs),
           "venue_pool_usd_day": round(total_pool, 2),
           "drops": drops, "n_survivors": len(rows),
           "n_book_errors": sum(1 for r in rows if r.get("book_error")),
           "addressable_usd_day_at_target_ALL": addr_all,
           "addressable_usd_day_at_target_TOP12": addr_12,
           "addressable_usd_day_at_min_ALL": addr_min_all,
           "min_ct_modeled": MIN_CT,
           "floor_caveat": "min-size $/day rows assume the book ALREADY qualifies "
                           "on rival depth; sub-target books model $0 per W10 canon "
                           "— R1 tests that floor live.",
           "candidates": scored}
    with open("/tmp/R0B_VENUE_CENSUS.json", "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(json.dumps({k: v for k, v in out.items() if k != "candidates"}, indent=1))
    print("TOP 20 BY $/DAY AT TARGET:")
    for r in scored[:20]:
        print(" %-38s pool %7.2f  tgt$ %8.2f  min$ %6.2f  d2c %5.2f  anchor %s  %s"
              % (r["ticker"], r["pool"], r["usd_day_target"] or -1,
                 r["usd_day_min"] or -1, r["days_to_close"],
                 "Y" if r.get("anchor_needed") else "n", r["title"][:40]))


if __name__ == "__main__":
    main()
