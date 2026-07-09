#!/usr/bin/env python3
"""READ-ONLY live check: does the bot's stored YES token point at the RIGHT team?

Step-3 preflight measurement (EB_SHARP_LINE_PLUMBING.md). It changes NOTHING —
pure SELECTs + read-only CLOB GETs. Run it on the VPS (prod DB + internet), paste
the whole output back to the EB session.

THE QUESTION IT ANSWERS
-----------------------
For shape-2 esports markets (outcomes are the two TEAM NAMES, e.g. team-vs-team
match winners), the DB stores a `yes_token_id` / `no_token_id` but NO team label.
The bot's YES detector (esports_bot.py:2580-2591) looks for an `outcome` field
that the DB path never populates, so it silently falls back to positional order
(`tokens[0]` = YES). The sharp-line signal inverts if that guess is wrong.

This script fetches the AUTHORITATIVE label from the live CLOB (the token's real
`outcome` string) and compares:
  - authoritative: the team name the *stored yes_token_id* actually resolves on
  - positional:    the team name *CLOB tokens[0]* resolves on (the bot's fallback)
and tallies how often they DISAGREE — i.e. the live sign-flip exposure. That
number decides whether Step 3 is urgent and confirms yes_token_id is trustworthy.

    python scripts/esports_orientation_live_check.py        # up to 60 shape-2 mkts
    python scripts/esports_orientation_live_check.py 120     # override cap
"""
import asyncio
import sys

from dotenv import load_dotenv

from base_engine.data.database import Database

load_dotenv()

_GAME_RX = r"\y(LoL|CS2|CSGO|Dota|Valorant|Val)\y|Counter-Strike|League of Legends|Mobile Legends|Overwatch|Rainbow Six"
_CLOB_URL = "https://clob.polymarket.com/markets/{cid}"

_YES_NO = {"YES", "NO", "1", "0", "TRUE", "FALSE"}


async def _peek_clob(cid: str):
    """Return list[(outcome, token_id)] for the live CLOB market, or [] on error."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=12.0) as c:
            r = await c.get(_CLOB_URL.format(cid=cid))
            if r.status_code != 200:
                return None, f"<clob {r.status_code}>"
            d = r.json()
    except Exception as e:
        return None, f"<clob error: {type(e).__name__}>"
    toks = [
        (str(t.get("outcome")), str(t.get("token_id", "")))
        for t in (d.get("tokens") or [])
    ]
    return toks, None


async def check(cap: int):
    db = Database()
    await db.init()

    async with db.get_session() as s:
        from sqlalchemy import text

        colr = await s.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='markets'"
        ))
        cols = {row[0] for row in colr.fetchall()}
        need = {"condition_id", "question", "yes_token_id", "no_token_id"}
        missing = need - cols
        if missing:
            print(f"!! markets table missing columns {sorted(missing)} — cannot run this check here.")
            await db.close()
            return

        order = "ORDER BY volume DESC NULLS LAST" if "volume" in cols else ""
        sql = (
            "SELECT condition_id, question, yes_token_id, no_token_id FROM markets "
            "WHERE (question ~* :rx" + (" OR category ILIKE 'esports'" if "category" in cols else "") + ") "
            "AND condition_id IS NOT NULL AND condition_id <> '' "
            "AND yes_token_id IS NOT NULL AND no_token_id IS NOT NULL "
            f"{order} LIMIT 800"
        )
        rows = [dict(zip(("condition_id", "question", "yes_token_id", "no_token_id"), r))
                for r in (await s.execute(text(sql), {"rx": _GAME_RX})).fetchall()]

    print(f"=== scanned {len(rows)} esports markets with yes/no token ids ===")

    shape2 = agree = flip = missing_tok = clob_err = 0
    checked = 0
    print("\n=== SHAPE-2 (team-name outcome) ORIENTATION CHECK — authoritative vs positional ===")
    for m in rows:
        if checked >= cap:
            break
        cid = str(m["condition_id"])
        toks, err = await _peek_clob(cid)
        if toks is None:
            clob_err += 1
            continue
        labels = {str(o).strip().upper() for o, _ in toks}
        # shape-1 (Yes/No) markets are not what this check is about — skip them.
        if not toks or labels <= _YES_NO:
            continue
        shape2 += 1
        checked += 1

        by_id = {tid: out for out, tid in toks}
        yes_tid = str(m["yes_token_id"])
        auth_yes = by_id.get(yes_tid)           # team the STORED yes_token resolves on
        positional_yes = toks[0][0]             # team the bot's fallback (tokens[0]) picks

        if auth_yes is None:
            missing_tok += 1
            status = "?? stored yes_token_id NOT among CLOB tokens"
        elif str(auth_yes).strip().upper() == str(positional_yes).strip().upper():
            agree += 1
            status = "agree"
        else:
            flip += 1
            status = "**FLIP** (positional fallback picks the WRONG team)"

        print(f"\n  cid={cid[:12]} | {str(m['question'])[:70]}")
        print(f"    CLOB outcomes: {[o for o, _ in toks]}")
        print(f"    stored yes_token -> authoritative YES team: {auth_yes!r}")
        print(f"    positional tokens[0] YES team:              {positional_yes!r}   [{status}]")

    print("\n=== TALLY ===")
    print(f"  shape-2 markets checked:       {shape2}")
    print(f"  agree  (fallback accidentally right): {agree}")
    print(f"  FLIP   (fallback wrong team):         {flip}   <-- live sign-flip exposure")
    print(f"  stored yes_token not in CLOB tokens:  {missing_tok}")
    print(f"  clob fetch errors:                    {clob_err}")
    print("\n  Interpretation: any FLIP > 0 means the current positional fallback would")
    print("  feed inverted orientation to the sharp signal -> Step 3 (plumb the")
    print("  authoritative CLOB label) is required, not optional. If stored-yes-token-")
    print("  not-in-CLOB > 0, yes_token_id itself is unreliable and the fix must key on")
    print("  the CLOB token_id<->outcome map, not the DB's yes/no columns.")

    await db.close() if hasattr(db, "close") else None


if __name__ == "__main__":
    _cap = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    asyncio.run(check(_cap))
