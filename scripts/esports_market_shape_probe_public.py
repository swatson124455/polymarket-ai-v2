#!/usr/bin/env python3
"""Read-only probe (DB-FREE variant): what SHAPE are live esports Polymarket markets?

Same question as scripts/esports_market_shape_probe.py — are esports markets
  (1) "Will <team> win?" YES/NO binaries  — outcomes literally "Yes"/"No", or
  (2) team-name-outcome markets           — outcomes are the two team names?
— but this variant discovers markets from the **public Gamma API** instead of the
prod `markets` table, so it needs NO VPS and NO DATABASE_URL. The only requirement
is outbound HTTPS to gamma-api.polymarket.com + clob.polymarket.com.

Why this exists: the DB-backed probe can't run from a cloud/sandboxed session (no
VPS, no prod DB). Once Polymarket egress is allowed, THIS script runs anywhere:
    python scripts/esports_market_shape_probe_public.py            # scan ~2000 mkts, 8 CLOB peeks
    python scripts/esports_market_shape_probe_public.py 4000 12    # wider scan, more peeks

Changes NOTHING (read-only GETs). Paste the whole output back / commit it to
EB_MARKET_SHAPE_RESULTS.md. Nothing here is secret.

Stdlib-only (urllib) so it has zero pip deps in either environment. Respects the
HTTPS_PROXY env var; if TLS verification fails behind the agent proxy, set
SSL_CERT_FILE=/root/.ccr/ca-bundle.crt (this script also auto-loads that bundle if
present).
"""
import json
import os
import re
import ssl
import sys
import urllib.request

# Same esports matcher the DB probe uses (Postgres \y word-boundary -> Python \b).
_GAME_RX = re.compile(
    r"\b(LoL|CS2|CSGO|Dota|Valorant|Val)\b"
    r"|Counter-Strike|League of Legends|Mobile Legends|Overwatch|Rainbow Six",
    re.IGNORECASE,
)
_GAMMA_URL = "https://gamma-api.polymarket.com/markets"
_CLOB_URL = "https://clob.polymarket.com/markets/{cid}"
_ESPORTS_TAG_ID = 64  # gamma tag slug "esports" (verified 2026-07-08)
_GAMMA_PAGE = 100     # gamma caps `limit` at 100 regardless of what you ask

_CA_BUNDLE = "/root/.ccr/ca-bundle.crt"


def _ssl_ctx():
    ctx = ssl.create_default_context()
    if os.path.exists(_CA_BUNDLE):
        try:
            ctx.load_verify_locations(_CA_BUNDLE)
        except Exception:
            pass
    return ctx


def _get_json(url):
    """Read-only GET -> parsed JSON, or ('<error ...>', None) sentinel tuple."""
    req = urllib.request.Request(url, headers={"User-Agent": "eb-shape-probe/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20, context=_ssl_ctx()) as r:
            if r.status != 200:
                return {"__error__": f"http {r.status}"}
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # network/parse/policy — report, don't crash
        return {"__error__": f"{type(e).__name__}: {str(e)[:80]}"}


def _outcomes_from_gamma(m):
    """Gamma stores `outcomes` as a JSON-encoded string (sometimes a list)."""
    raw = m.get("outcomes")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    return [str(x) for x in raw] if isinstance(raw, list) else []


def _outcomes_from_clob(cid):
    """CLOB ground truth: [(outcome, tok8), ...] or [] on error."""
    d = _get_json(_CLOB_URL.format(cid=cid))
    if not isinstance(d, dict) or d.get("__error__"):
        err = d.get("__error__") if isinstance(d, dict) else "?"
        return None, None, f"<clob {err}>"
    outs = [
        (str(t.get("outcome")), str(t.get("token_id", ""))[:8])
        for t in (d.get("tokens") or [])
    ]
    return d.get("question"), d.get("neg_risk"), outs


def _classify(labels):
    norm = {str(l).strip().upper() for l in labels}
    if norm and norm <= {"YES", "NO"}:
        return "YES/NO (shape 1)"
    if labels:
        return "TEAM-NAME / OTHER (shape 2)"
    return "?"


def probe(scan_cap, clob_limit):
    print(f"=== DB-FREE esports market-shape probe (Gamma discovery) ===")
    print(f"    scan_cap={scan_cap}  clob_peeks={clob_limit}  proxy={os.environ.get('HTTPS_PROXY','(none)')}")

    found = []
    offset = 0
    scanned = 0
    while scanned < scan_cap:
        # Primary discovery = the esports TAG (id 64). Question-regex misses
        # head-to-head titles that name only the teams ("T1 vs Gen.G"), so the
        # tag is the authoritative universe; regex is a secondary label only.
        url = (
            f"{_GAMMA_URL}?tag_id={_ESPORTS_TAG_ID}&closed=false&active=true"
            f"&archived=false&limit={_GAMMA_PAGE}&offset={offset}"
        )
        data = _get_json(url)
        if isinstance(data, dict) and data.get("__error__"):
            print(f"\n!! Gamma fetch failed at offset={offset}: {data['__error__']}")
            if offset == 0:
                print("   (offset 0 failed -> egress to gamma-api.polymarket.com is still blocked, "
                      "or TLS/CA misconfigured. Nothing scanned.)")
            break
        if not isinstance(data, list) or not data:
            break
        found.extend(data)
        scanned += len(data)
        offset += _GAMMA_PAGE
        if len(data) < _GAMMA_PAGE:
            break

    print(f"\n=== Gamma: {len(found)} esports-tag markets (tag_id={_ESPORTS_TAG_ID}, active, not closed) ===")
    for m in found:
        vol = m.get("volumeNum") or m.get("volume")
        vol_s = f"{float(vol):>10.0f}" if vol not in (None, "") else "        NA"
        outs = _outcomes_from_gamma(m)
        print(f"  vol={vol_s} neg={str(m.get('negRisk')):>5} "
              f"cid={str(m.get('conditionId'))[:14]} shape={_classify(outs):<26} "
              f"| {str(m.get('question'))[:66]}")
        print(f"        gamma_outcomes={outs}")

    # CLOB ground-truth cross-check on the top-N by volume.
    peek = found[:clob_limit]
    print(f"\n=== LIVE CLOB outcome labels (top {len(peek)}) — the ground truth ===")
    yesno = other = 0
    for m in peek:
        cid = str(m.get("conditionId"))
        q, neg, outs = _outcomes_from_clob(cid)
        if outs is None:
            print(f"\n  cid={cid[:14]}  -> {q}")
            continue
        labels = [o[0] for o in outs]
        shape = _classify(labels)
        if shape.startswith("YES/NO"):
            yesno += 1
        elif outs:
            other += 1
        print(f"\n  cid={cid[:14]}  neg_risk={neg}  -> {shape}")
        print(f"    Q: {str(q)[:90]}")
        print(f"    outcomes: {outs}")

    print(f"\n=== SHAPE TALLY (CLOB peeks): YES/NO={yesno}  team-name/other={other} ===")
    print("    shape 1 dominant -> resolver parses the question's SUBJECT team")
    print("    shape 2 present  -> resolver must also map YES-token outcome name -> team")

    # Gamma-wide shape tally (all found, not just the CLOB-peeked top-N).
    g_yesno = g_other = g_unknown = 0
    for m in found:
        s = _classify(_outcomes_from_gamma(m))
        if s.startswith("YES/NO"):
            g_yesno += 1
        elif s.startswith("TEAM-NAME"):
            g_other += 1
        else:
            g_unknown += 1
    print(f"\n=== GAMMA-WIDE SHAPE TALLY (all {len(found)}): "
          f"YES/NO={g_yesno}  team-name/other={g_other}  unknown={g_unknown} ===")

    # Phrasing patterns — the input step-2 (parser hardening) actually needs.
    pats = [
        ("will_X_win",     re.compile(r"^will .+\bwin\b", re.I)),
        ("will_X_beat_Y",  re.compile(r"^will .+\b(beat|defeat)\b", re.I)),
        ("X_vs_Y",         re.compile(r"\bvs\.?\b", re.I)),
        ("map_round_prop", re.compile(r"\b(map|round|game|kills?|first blood|handicap|score)\b", re.I)),
    ]
    print("\n=== QUESTION PHRASING PATTERNS (esports-tag questions) ===")
    counts = {name: 0 for name, _ in pats}
    unmatched = []
    for m in found:
        q = str(m.get("question", ""))
        hit = False
        for name, rx in pats:
            if rx.search(q):
                counts[name] += 1
                hit = True
        if not hit:
            unmatched.append(q)
    for name, _ in pats:
        print(f"    {name:16} {counts[name]}")
    print(f"    unmatched-by-any {len(unmatched)}")
    for q in unmatched[:25]:
        print(f"      · {q[:88]}")

    if not found and scanned:
        print("\n    NOTE: 0 esports-tag markets returned. Either none are live right now, or the")
        print("    tag id drifted — re-verify via /tags/slug/esports.")


if __name__ == "__main__":
    _scan = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    _clob = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    probe(_scan, _clob)
