#!/usr/bin/env python3
"""Read-only probe: what SHAPE are live esports Polymarket markets?

Answers the one open question blocking the sharp-line orientation resolver
(EB_SHARP_LINE_PLUMBING.md, next-action #1): are esports markets
  (1) "Will <team> win?" YES/NO binaries  — outcomes literally "Yes"/"No", or
  (2) team-name-outcome markets           — outcomes are the two team names?
The answer decides how resolve_yes_is_team_a() parses which team YES resolves on.

Changes NOTHING. Reads the `markets` table (same columns the market service
uses) and peeks the live CLOB for the raw token `outcome` labels — the CLOB is
the ground truth, the DB view only stores yes/no token ids.

Run on the VPS with the env loaded (DATABASE_URL):
    python scripts/esports_market_shape_probe.py           # 20 DB rows, 8 CLOB peeks
    python scripts/esports_market_shape_probe.py 40 12      # override limits

Paste the whole output back to the EB session. Nothing here is secret.
"""
import asyncio
import sys

from dotenv import load_dotenv

from base_engine.data.database import Database

load_dotenv()

_GAME_RX = r"\y(LoL|CS2|CSGO|Dota|Valorant|Val)\y|Counter-Strike|League of Legends|Mobile Legends|Overwatch|Rainbow Six"
_CLOB_URL = "https://clob.polymarket.com/markets/{cid}"


async def _peek_clob(cid: str):
    """Fetch the live CLOB market and return (question, neg_risk, [(outcome, tok8)])
    or ('<error ...>', None, []). Read-only GET."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=12.0) as c:
            r = await c.get(_CLOB_URL.format(cid=cid))
            if r.status_code != 200:
                return (f"<clob {r.status_code}>", None, [])
            d = r.json()
    except Exception as e:  # network/parse — report, don't crash the probe
        return (f"<clob error: {type(e).__name__}>", None, [])

    outcomes = [
        (str(t.get("outcome")), str(t.get("token_id", ""))[:8])
        for t in (d.get("tokens") or [])
    ]
    return (d.get("question"), d.get("neg_risk"), outcomes)


async def probe(db_limit: int, clob_limit: int):
    db = Database()
    await db.init()

    rows = []
    async with db.get_session() as s:
        from sqlalchemy import text

        # Schema-adaptive: the `markets` table shape drifts across deploys (some
        # DBs predate yes_token_id/no_token_id). Query ONLY columns that exist —
        # orientation ground truth is the CLOB peek below, not these columns.
        colr = await s.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'markets'"
        ))
        cols = {row[0] for row in colr.fetchall()}
        print(f"=== `markets` columns present: {sorted(cols)} ===")
        if "condition_id" not in cols or "question" not in cols:
            print("!! markets lacks condition_id/question — wrong DB or empty schema. Stop.")
            await db.close()
            return

        want = [c for c in ("condition_id", "question", "category", "volume") if c in cols]
        has_cat = "category" in cols
        has_vol = "volume" in cols
        where = "question ~* :rx" + (" OR category ILIKE 'esports'" if has_cat else "")
        order = "ORDER BY volume DESC NULLS LAST" if has_vol else ""
        sql = (
            f"SELECT {', '.join(want)} FROM markets "
            f"WHERE ({where}) AND condition_id IS NOT NULL AND condition_id <> '' "
            f"{order} LIMIT :lim"
        )
        r = await s.execute(text(sql), {"rx": _GAME_RX, "lim": db_limit})
        rows = [dict(zip(want, row)) for row in r.fetchall()]

    print(f"\n=== DB `markets`: {len(rows)} esports-ish rows ===")
    for m in rows:
        vol = m.get("volume")
        vol_s = f"{float(vol):>10.0f}" if vol is not None else "        NA"
        cat = str(m.get("category", ""))[:9]
        print(f"  vol={vol_s} cat={cat:>9} cid={str(m.get('condition_id'))[:14]} "
              f"| {str(m.get('question'))[:74]}")

    # CLOB ground-truth peek on the top-N by volume.
    peek_cids = [str(m["condition_id"]) for m in rows[:clob_limit]]
    print(f"\n=== LIVE CLOB outcome labels (top {len(peek_cids)}) — the ground truth ===")
    yesno = 0
    other = 0
    for cid in peek_cids:
        q, neg, outcomes = await _peek_clob(cid)
        labels = [o[0] for o in outcomes]
        norm = {str(l).strip().upper() for l in labels}
        if norm and norm <= {"YES", "NO"}:
            shape = "YES/NO (shape 1)"
            yesno += 1
        elif outcomes:
            shape = "TEAM-NAME / OTHER (shape 2)"
            other += 1
        else:
            shape = "?"
        print(f"\n  cid={cid[:14]}  neg_risk={neg}  -> {shape}")
        print(f"    Q: {str(q)[:90]}")
        print(f"    outcomes: {outcomes}")

    print(f"\n=== SHAPE TALLY (CLOB peeks): YES/NO={yesno}  team-name/other={other} ===")
    print("    shape 1 dominant -> resolver parses the question's SUBJECT team")
    print("    shape 2 present  -> resolver must also map YES-token outcome name -> team")

    await db.close() if hasattr(db, "close") else None


if __name__ == "__main__":
    _db_lim = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    _clob_lim = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    asyncio.run(probe(_db_lim, _clob_lim))
