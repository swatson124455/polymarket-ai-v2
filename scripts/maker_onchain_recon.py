"""Maker on-chain ledger reconciliation.

The engine's in-memory inventory has never been checked against anything
external. This is that check. Read-only: it never writes engine state and
never submits a transaction.

WHY THIS IS NOT "compare y/n to the chain balance"
--------------------------------------------------
The engine keeps a NET ECONOMIC view; the chain keeps a GROSS TOKEN view.
Three engine mechanics break a naive comparison, and each would fire a loud
false alarm on a perfectly healthy engine:

1. PAIR NETTING. `merge_pairs` nets min(y, n) out of inventory because a
   YES+NO pair is worth exactly $1 (maker_live_engine.py:950). The engine
   says so itself: "the on-chain merge is a pilot mechanic â€” until executed
   the pairs sit as locked collateral". No merge transaction is ever sent,
   so those tokens are STILL ON CHAIN while state shows y=n=0.
2. SETTLEMENT ZEROING. `try_settle` zeroes y/n at resolution
   (maker_live_engine.py:1084). The winning tokens remain on chain until a
   redemption transaction is sent, which this engine also never sends.
3. SHARED WALLET. If the wallet is shared with another bot, its holdings are
   foreign inventory the Maker ledger knows nothing about.

So the bridge is the LEDGERS, event-sourced. Every mutation of y/n/spent in
the engine is ledgered â€” verified by enumerating the mutation sites
(match_fills_paper -> paper snapshot row; _apply_live_trades -> live delta
row; try_settle -> settlements row) â€” so a replay of all three reproduces
state exactly, and a replay of fill legs alone reproduces the chain:

  net[market]  = deltas + snapshots + settlements   -> must equal engine state
  gross[token] = fill legs only, no netting/settle  -> what the chain holds

REPORTING CONTRACT (the part that gates real capital)
-----------------------------------------------------
Every check reports PASS / DRIFT / SKIP / WARN, and a check that could not
run is NEVER reported as a check that passed. Exit code is the worst status:

  0 = every applicable check ran and passed
  2 = DRIFT â€” inventory is not where the engine thinks it is
  3 = surplus only, on a shared wallet (ambiguous, not certifiable as ours)
  4 = a check was SKIPPED or a source disagreed â€” CANNOT CERTIFY
  1 = error

rc=0 is the ONLY go-signal. rc=4 means the tool did not check what you think
it checked. This distinction exists because an adversarial review found six
ways a real loss previously exited 0.
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request

CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"          # ConditionalTokens
SEL_BALANCEOF1155 = "0x00fdd58e"                            # balanceOf(address,uint256)
SHARE_DECIMALS = 6                                          # verified live 2026-07-20
DEFAULT_RPCS = ("https://polygon-bor-rpc.publicnode.com,"
                "https://polygon.drpc.org,"
                "https://1rpc.io/matic")
DATA_API = "https://data-api.polymarket.com"
HDRS = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
RPC_CHUNK = 100                     # eth_calls per JSON-RPC batch
EPS = 1e-9
DUST_SHARES = 0.01                  # share-count noise floor
DUST_USD = 0.01                     # dollar noise floor
API_PAGE = 500
API_MAX_PAGES = 20
ABSURD_MULTIPLE = 1e6               # decimals-mismatch tripwire

PASS, DRIFT, SKIP, WARN = "PASS", "DRIFT", "SKIP", "WARN"
# SEVERITY rank, not exit code: DRIFT (a definite fault) must dominate SKIP
# (an unknown), because a halt consumer keying on rc==2 must still see the
# largest possible loss. Ranking on the raw exit numbers would let a skipped
# check mask real drift behind rc=4.
_RANK = {PASS: 0, WARN: 1, SKIP: 1, DRIFT: 2}
_RANK_RC = {0: 0, 1: 4, 2: 2}


class Report:
    """Uniform check accounting. A check must be recorded to be reported, and
    the exit code is derived from the recorded statuses â€” so 'printed CLEAN
    but never ran' is not expressible."""

    def __init__(self):
        self.checks = []
        self.surplus_only = False

    def add(self, name, status, headline, detail=None):
        self.checks.append({"check": name, "status": status,
                            "headline": headline, "detail": detail or []})
        return status

    def rc(self):
        rank = 0
        for c in self.checks:
            rank = max(rank, _RANK.get(c["status"], 1))
        rc = _RANK_RC[rank]
        if rc == 0 and self.surplus_only:
            return 3
        return rc

    def render(self):
        out = []
        for c in self.checks:
            out.append(f"\n{c['check']}")
            out.append(f"  {c['status']}: {c['headline']}")
            for line in c["detail"][:10]:
                out.append(f"    {line}")
            if len(c["detail"]) > 10:
                out.append(f"    â€¦ {len(c['detail']) - 10} more")
        return "\n".join(out)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ ledger replay â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _is_delta_row(row):
    """Live fill rows are per-fill DELTAS: {tid, mkt, leg, px, sz, role}.

    Paper rows are post-fill SNAPSHOTS: {mkt, n, y, nn, spent}. Anomaly rows
    (no_id / no_timestamp / no_our_leg / unparsed / unmatched / trade_exc) are
    neither and must never be replayed â€” they are audit records of things the
    engine REFUSED to apply.
    """
    return (isinstance(row, dict) and row.get("leg") in ("yes", "no")
            and row.get("mkt") is not None
            and row.get("px") is not None and row.get("sz") is not None)


def _is_snapshot_row(row):
    return (isinstance(row, dict) and row.get("mkt") is not None
            and "leg" not in row and "y" in row and "nn" in row)


def _is_settle_row(row):
    return (isinstance(row, dict) and row.get("mkt") is not None
            and row.get("payout") is not None)


def _is_prune_row(row):
    """S8: the engine's hourly prune deletes inert settled/husk entries from
    state and archives {mkt, resid_spent, acc, tok_y, tok_n} here. The replay
    must consume these or every pruned market reads as ledger_without_state
    DRIFT forever (review S8-F1)."""
    return (isinstance(row, dict) and row.get("act") == "prune"
            and row.get("mkt") is not None)


def ledger_paths(base):
    """Ledger files, with mid-rotation duplicates removed.

    The engine rotates by writing `<f>.gz` then `os.remove(<f>)`, swallowing
    any failure (maker_live_engine.py:1455). If the remove fails, BOTH files
    exist permanently â€” and globbing both would replay that day twice,
    doubling `gross` and reporting a 100%-short chain on a healthy engine.
    The .gz is the complete copy, so it wins.
    """
    found, dupes = {}, []
    for pat in ("fills-*.jsonl", "fills-*.jsonl.gz",
                "settlements-*.jsonl", "settlements-*.jsonl.gz"):
        for p in glob.glob(os.path.join(base, pat)):
            stem = p[:-3] if p.endswith(".gz") else p
            if stem in found:
                dupes.append(os.path.basename(stem))
                if p.endswith(".gz"):
                    found[stem] = p
            else:
                found[stem] = p
    return sorted(found.values()), sorted(set(dupes))


def load_events(base):
    """Every ledgered inventory event, in replay order.

    Only the fields needed for replay are retained â€” a live ledger grows
    without bound and holding whole rows was an OOM risk on a shared VPS.

    Ordering is (timestamp, file, line). Ledger timestamps are whole seconds,
    so a settlement and a fill inside the SAME second are not orderable from
    the data; those markets are flagged ambiguous rather than called drift.
    """
    events = []
    paths, dupes = ledger_paths(base)
    for fi, p in enumerate(paths):
        opener = gzip.open if p.endswith(".gz") else open
        try:
            with opener(p, "rt") as fh:
                for li, line in enumerate(fh):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    t = row.get("t")
                    try:
                        t = float(t)
                    except (TypeError, ValueError):
                        t = 0.0
                    if _is_prune_row(row):
                        ev = ("prune", str(row["mkt"]), row.get("resid_spent"),
                              row.get("tok_y"), row.get("tok_n"))
                    elif _is_delta_row(row):
                        ev = ("delta", str(row["mkt"]), row.get("leg"),
                              row.get("px"), row.get("sz"))
                    elif _is_settle_row(row):
                        ev = ("settle", str(row["mkt"]), row.get("payout"),
                              None, None)
                    elif _is_snapshot_row(row):
                        ev = ("snapshot", str(row["mkt"]), row.get("y"),
                              row.get("nn"), row.get("spent"))
                    else:
                        ev = ("skip", None, None, None, None)
                    events.append((t, fi, li) + ev)
        except (OSError, EOFError, gzip.BadGzipFile) as e:
            # EOFError = a .gz truncated by a crashed rotation. Per-file warn,
            # never a traceback that kills the whole reconciliation.
            print(f"  warn: cannot read {p}: {type(e).__name__}: {e}",
                  file=sys.stderr)
    events.sort(key=lambda e: (e[0], e[1], e[2]))
    return events, paths, dupes


def replay_events(events):
    """Event-source the ledgers into BOTH views.

    NET must reproduce engine state exactly. The delta arithmetic mirrors
    maker_live_engine.py:2101-2119 operation-for-operation, including
    round(x, 4) at every step: `spent` feeds the event cap, portfolio_net and
    the day-loss floor, and the engine's own ship-gate caught a one-tick
    divergence from re-ordering these very lines. A differential fuzz against
    the engine's real function keeps it honest.

    GROSS is what the chain should hold: fill legs summed, NO netting and NO
    settlement, because the engine sends neither a merge nor a redemption
    transaction. Paper snapshots are absolute states carrying no per-fill
    history, so gross is UNKNOWABLE for any market that took a paper fill â€”
    those go in `paper_tainted` and are excluded from the chain comparison
    explicitly, never silently compared against a wrong expectation.
    """
    gross, net = {}, {}
    paper_tainted, ambiguous = set(), set()
    last_t, last_settle_t = {}, {}
    prune_drift, prune_tokens = [], set()
    stats = {"delta": 0, "snapshot": 0, "settle": 0, "skip": 0, "prune": 0}

    for t, _fi, _li, kind, key, a, b, c in events:
        stats[kind] = stats.get(kind, 0) + 1
        if kind == "skip":
            continue
        if kind == "prune":
            # The engine forgot this entry ON PURPOSE. Verify the archived
            # residual matches the replayed net, then drop the key so the
            # state-vs-ledger union check does not flag a healthy prune.
            # gross/chain expectations are NOT dropped — the chain still
            # holds the tokens; their ids ride along for chain coverage.
            cur = net.get(key)
            try:
                resid = float(a) if a is not None else 0.0
            except (TypeError, ValueError):
                resid = 0.0
            ok = ((cur is None and abs(resid) <= DUST_USD) or
                  (cur is not None
                   and abs(cur["spent"] - resid) <= DUST_USD
                   and abs(cur["y"]) <= DUST_SHARES
                   and abs(cur["n"]) <= DUST_SHARES))
            if not ok:
                prune_drift.append({"market": key, "kind": "prune_mismatch",
                                    "ledger": cur,
                                    "resid_spent": resid})
            net.pop(key, None)
            for tok in (b, c):
                if tok:
                    prune_tokens.add(str(tok))
            last_t[key] = t
            continue

        d = net.setdefault(key, {"y": 0.0, "n": 0.0, "spent": 0.0, "merged": 0.0})

        if kind == "delta":
            try:
                px, sz = float(b), float(c)
            except (TypeError, ValueError):
                stats["skip"] += 1
                continue
            leg = a
            if last_settle_t.get(key) == t:
                ambiguous.add(key)      # settle then fill, same second
            g = gross.setdefault(key, {"yes": 0.0, "no": 0.0})
            g[leg] += sz                    # chain has no 4dp quantisation
            if leg == "yes":
                d["y"] = round(d["y"] + sz, 4)
            else:
                d["n"] = round(d["n"] + sz, 4)
            d["spent"] = round(d["spent"] + px * sz, 4)
            pairs = min(d["y"], d["n"])
            if pairs > EPS:
                d["y"] = round(d["y"] - pairs, 4)
                d["n"] = round(d["n"] - pairs, 4)
                d["spent"] = round(d["spent"] - pairs, 4)
                d["merged"] = round(d["merged"] + pairs, 4)

        elif kind == "snapshot":
            try:
                d["y"], d["n"], d["spent"] = float(a or 0.0), float(b or 0.0), \
                    float(c or 0.0)
            except (TypeError, ValueError):
                stats["skip"] += 1
                continue
            paper_tainted.add(key)

        elif kind == "settle":
            try:
                payout = float(a)
            except (TypeError, ValueError):
                stats["skip"] += 1
                continue
            if last_t.get(key) == t:
                ambiguous.add(key)          # fill then settle, same second
            last_settle_t[key] = t
            d["spent"] = round(d["spent"] - payout, 4)
            d["y"] = d["n"] = 0.0

        last_t[key] = t

    return gross, net, {"paper_tainted": paper_tainted,
                        "ambiguous": ambiguous, "stats": stats,
                        "prune_drift": prune_drift,
                        "prune_tokens": prune_tokens}


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ chain reads â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def valid_address(a):
    a = (a or "").strip()
    if not a.lower().startswith("0x") or len(a) != 42:
        return None
    try:
        int(a[2:], 16)
    except ValueError:
        return None
    return a


class Chain:
    def __init__(self, rpcs, timeout=25, deadline=None):
        self.rpcs = [r.strip() for r in rpcs.split(",") if r.strip()]
        self.timeout = timeout
        self.deadline = deadline
        self.calls = 0

    def _post(self, rpc, payload):
        req = urllib.request.Request(rpc, data=json.dumps(payload).encode(),
                                     headers=HDRS)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())

    def _batch(self, payload):
        """Try each RPC in turn. A batch that returns short, errored, or
        undecodable is a FAILURE of that RPC â€” never zeros. Silently reading
        a failed call as a zero balance is exactly how a reconciler invents a
        'positions vanished' alarm.

        Result-shape validation lives HERE, inside the failover loop: a
        wrong-chain or wrong-contract RPC returns "0x", and decoding that
        downstream would kill the run instead of failing over.
        """
        last = None
        for rpc in self.rpcs:
            if self.deadline is not None and time.time() > self.deadline:
                raise RuntimeError("deadline exceeded during chain read")
            try:
                out = self._post(rpc, payload)
            except (urllib.error.URLError, TimeoutError, OSError,
                    json.JSONDecodeError) as e:
                last = f"{rpc}: {type(e).__name__}: {e}"
                continue
            if not isinstance(out, list) or len(out) != len(payload):
                last = f"{rpc}: malformed batch response"
                continue
            if any(not isinstance(o, dict) or "result" not in o for o in out):
                err = next((o.get("error") for o in out
                            if isinstance(o, dict) and "result" not in o), None)
                last = f"{rpc}: rpc error {err}"
                continue
            bad = [o for o in out if not (isinstance(o.get("result"), str)
                                          and len(o["result"]) == 66
                                          and o["result"].startswith("0x"))]
            if bad:
                # "0x" = no contract code at CTF on this chain
                last = (f"{rpc}: undecodable result {bad[0].get('result')!r} "
                        f"(wrong chain or wrong contract?)")
                continue
            self.calls += len(payload)
            return out
        raise RuntimeError(f"all RPCs failed: {last}")

    def balances(self, wallet, token_ids, progress=False):
        """token_id -> share balance. Matched back by JSON-RPC id, never by
        position: an out-of-order batch zipped positionally would assign
        balances to the wrong tokens â€” silent, total corruption."""
        addr = valid_address(wallet)
        if not addr:
            raise RuntimeError(f"invalid wallet address: {wallet!r}")
        w = addr[2:].lower().rjust(64, "0")
        ids = [str(t) for t in token_ids]
        out = {}
        for i in range(0, len(ids), RPC_CHUNK):
            chunk = ids[i:i + RPC_CHUNK]
            payload = []
            for j, tok in enumerate(chunk):
                payload.append({
                    "jsonrpc": "2.0", "id": j, "method": "eth_call",
                    "params": [{"to": CTF,
                                "data": SEL_BALANCEOF1155 + w
                                + format(int(tok), "064x")}, "latest"]})
            resp = self._batch(payload)
            by_id = {o["id"]: o["result"] for o in resp}
            for j, tok in enumerate(chunk):
                raw = by_id.get(j)
                if raw is None:
                    raise RuntimeError(f"missing id {j} in batch response")
                out[tok] = int(raw, 16) / (10 ** SHARE_DECIMALS)
            if progress:
                print(f"    chain: {min(i + RPC_CHUNK, len(ids))}/{len(ids)} tokens",
                      file=sys.stderr)
        return out


def data_api_positions(wallet, timeout=25, deadline=None):
    """INDEPENDENT cross-check source. Polymarket's indexer, not the chain â€”
    it can lag or net differently (observed live 2026-07-20: one wallet read
    60 here vs 65 on chain). Never merged into the chain view; disagreement
    is reported, not resolved. Paged: a truncated read would manufacture
    false disagreements."""
    out, offset, truncated = {}, 0, True
    for _ in range(API_MAX_PAGES):
        if deadline is not None and time.time() > deadline:
            raise RuntimeError("deadline exceeded during data-api read")
        url = (f"{DATA_API}/positions?user={wallet}&limit={API_PAGE}"
               f"&offset={offset}&sizeThreshold=0")
        req = urllib.request.Request(url, headers={"User-Agent": HDRS["User-Agent"]})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            rows = json.loads(r.read())
        if not isinstance(rows, list) or not rows:
            truncated = False
            break
        for p in rows:
            if p.get("asset") is not None:
                out[str(p["asset"])] = float(p.get("size") or 0.0)
        if len(rows) < API_PAGE:
            truncated = False
            break
        offset += len(rows)
    return out, truncated


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ reconciliation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def check_state_vs_ledger(state, net, ambiguous, paper_tainted=frozenset()):
    """Engine state must equal the ledgers replayed with the engine's own
    semantics. Iterates the UNION of state and ledger markets: a market
    present in the ledger but missing from state (a `.bak` recovery that
    predates the ledger â€” a designed-for condition, maker_live_engine.py:2137)
    is exactly the case a state-only loop reports clean."""
    drift, amb = [], []
    for key in sorted(set(state) | set(net)):
        if key == "meta":
            continue
        st = state.get(key)
        if st is not None and not isinstance(st, dict):
            # a corrupted entry must be LOUD, not skipped: partial state.json
            # corruption is why the engine has a recovery ladder at all
            drift.append({"market": key, "kind": "state_entry_not_a_dict",
                          "state_type": type(st).__name__,
                          "ledger": net.get(key)})
            continue
        exp = net.get(key)

        if exp is None:
            st = st or {}
            if (abs(float(st.get("y", 0.0))) > DUST_SHARES
                    or abs(float(st.get("n", 0.0))) > DUST_SHARES
                    or abs(float(st.get("spent", 0.0))) > DUST_USD):
                # `spent` matters as much as shares: merge/settle both zero
                # y/n and leave realized dollars behind, so post-merge is the
                # single most common shape here
                drift.append({"market": key, "kind": "state_without_ledger",
                              "y": st.get("y", 0.0), "n": st.get("n", 0.0),
                              "spent": st.get("spent", 0.0)})
            continue

        if st is None:
            drift.append({"market": key, "kind": "ledger_without_state",
                          "ledger": exp})
            continue

        dy = abs(float(st.get("y", 0.0)) - exp["y"])
        dn = abs(float(st.get("n", 0.0)) - exp["n"])
        ds = abs(float(st.get("spent", 0.0)) - exp["spent"])
        # `merged` is only comparable on markets with no paper history: paper
        # snapshot rows carry y/nn/spent but never merged, so the replay
        # cannot know the paper-era merges the engine already booked
        dm = 0.0
        if key not in paper_tainted:
            dm = abs(float(st.get("merged", 0.0)) - exp["merged"])
        if (dy > DUST_SHARES or dn > DUST_SHARES or ds > DUST_USD
                or dm > DUST_SHARES):
            row = {"market": key, "kind": "state_ledger_mismatch",
                   "state": {"y": st.get("y", 0.0), "n": st.get("n", 0.0),
                             "spent": st.get("spent", 0.0),
                             "merged": st.get("merged", 0.0)},
                   "ledger": exp,
                   "delta": {"y": round(dy, 4), "n": round(dn, 4),
                             "spent": round(ds, 4), "merged": round(dm, 4)}}
            (amb if key in ambiguous else drift).append(row)
    return drift, amb


def check_chain_vs_gross(state, gross, chain, baseline):
    """Chain must equal gross ledger + foreign holdings.

    Returns (shortfall, surplus, skipped, compared). SHORTFALL is unambiguous
    fault in every configuration: tokens the ledger says we bought are not
    there. SURPLUS is fault on a dedicated wallet (tokens we never booked) but
    merely ambiguous on a shared one, so it is only downgraded when a baseline
    for that token actually exists.

    Paper history does NOT exclude a market. Paper fills bought nothing on
    chain and contribute nothing to `gross` (only delta rows do), so for a
    market that traded paper and then live, `gross` is already exactly the
    live legs â€” i.e. exactly right. Excluding these was over-conservative to
    the point of breaking the contract: the pilot path is paper-then-live in
    ONE base dir, so every such market would be excluded forever and rc=0 â€”
    documented as the only go-signal â€” would be unreachable.
    """
    shortfall, surplus, skipped = [], [], []
    compared = 0
    for key in sorted(set(state) | set(gross)):
        if key == "meta":
            continue
        st = state.get(key)
        if not isinstance(st, dict):
            skipped.append({"market": key, "why": "no usable state entry"})
            continue
        g = gross.get(key) or {"yes": 0.0, "no": 0.0}
        for leg, tokfield in (("yes", "tok_y"), ("no", "tok_n")):
            tok = st.get(tokfield)
            if not tok:
                if g[leg] > DUST_SHARES:
                    skipped.append({"market": key,
                                    "why": f"{leg} fills but no {tokfield}"})
                continue
            tok = str(tok)
            if tok not in chain:
                skipped.append({"market": key, "why": f"{leg} token not read"})
                continue
            compared += 1
            base = float(baseline.get(tok, 0.0))
            expected = round(g[leg] + base, 4)
            actual = chain[tok]
            delta = round(actual - expected, 4)
            row = {"market": key, "leg": leg, "token": tok,
                   "ledger_gross": round(g[leg], 4), "foreign_baseline": base,
                   "expected": expected, "chain": actual, "delta": delta}
            row["direction"] = "short" if delta < 0 else "over"
            if delta < -DUST_SHARES:
                # unambiguous in every configuration: the other bot trading
                # cannot REMOVE tokens the Maker ledger says we bought
                shortfall.append(row)
            elif delta > DUST_SHARES:
                if base > 0:
                    # shared wallet, known foreign holder: the other bot may
                    # simply have bought more since the baseline
                    surplus.append(row)
                else:
                    # no foreign baseline for this token: we hold tokens we
                    # never booked â€” a missed fill or an unbooked transfer
                    shortfall.append(row)
    return shortfall, surplus, skipped, compared


def main(argv=None):
    ap = argparse.ArgumentParser(description="Maker on-chain ledger reconciliation")
    ap.add_argument("--base", default="/opt/pa2-maker-live")
    ap.add_argument("--wallet", default=os.environ.get("MAKER_FUNDER", ""),
                    help="proxy/funder address holding the tokens (NOT the signer EOA)")
    ap.add_argument("--baseline", default="")
    ap.add_argument("--write-baseline", default="")
    ap.add_argument("--rpcs", default=os.environ.get("MAKER_POLYGON_RPCS", DEFAULT_RPCS))
    ap.add_argument("--no-chain", action="store_true",
                    help="ledger-vs-state only; exits 4 (cannot certify), never 0")
    ap.add_argument("--cross-check", action="store_true")
    ap.add_argument("--timeout-s", type=float, default=900.0)
    ap.add_argument("--json", default="")
    args = ap.parse_args(argv)

    if args.write_baseline and args.no_chain:
        print("FATAL: --write-baseline requires a chain read; --no-chain "
              "would write nothing and return the same code as success.",
              file=sys.stderr)
        return 1
    if args.write_baseline and not valid_address(args.wallet):
        print(f"FATAL: --write-baseline needs a valid --wallet "
              f"(got {args.wallet!r}).", file=sys.stderr)
        return 1

    t0 = time.time()
    deadline = t0 + args.timeout_s
    rep = Report()

    state_path = os.path.join(args.base, "state.json")
    try:
        with open(state_path) as fh:
            state = json.load(fh)
        if not isinstance(state, dict):
            raise ValueError("state.json is not an object")
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"FATAL: cannot read {state_path}: {e}", file=sys.stderr)
        return 1

    events, paths, dupes = load_events(args.base)
    gross, net, meta = replay_events(events)
    stats = meta["stats"]

    print(f"maker on-chain reconciliation  "
          f"({time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})")
    print(f"  base            {args.base}")
    print(f"  ledgers         {len(paths)} file(s), {len(events)} rows "
          f"(delta={stats['delta']} snapshot={stats['snapshot']} "
          f"settle={stats['settle']} skip={stats['skip']})")
    print(f"  markets         state={sum(1 for k in state if k != 'meta')} "
          f"ledger={len(net)} paper_tainted={len(meta['paper_tainted'])}")
    if dupes:
        print(f"  WARN            {len(dupes)} ledger day(s) present as BOTH "
              f".jsonl and .gz (crashed rotation); using .gz: {dupes[:3]}")

    report = {"t": int(t0), "base": args.base, "wallet": args.wallet,
              "events": len(events), "stats": stats,
              "paper_tainted": len(meta["paper_tainted"])}

    # â”€â”€ check 1: state vs ledgers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    drift_a, amb_a = check_state_vs_ledger(state, net, meta["ambiguous"],
                                           meta["paper_tainted"])
    # S8-F1: a prune row disagreeing with the replayed net is the same
    # severity class as any other state/ledger disagreement
    drift_a = drift_a + meta.get("prune_drift", [])
    report["state_vs_ledger"] = {"drift": drift_a, "ambiguous": amb_a}
    if not net:
        rep.add("STATE vs LEDGER", SKIP,
                "no inventory events in any ledger â€” nothing to reconcile "
                "state against")
    elif drift_a:
        rep.add("STATE vs LEDGER", DRIFT,
                f"{len(drift_a)} market(s) disagree with the ledgers",
                [f"{d['market']}: {d['kind']} {d.get('delta') or ''}"
                 for d in drift_a])
    elif not net:
        rep.add("STATE vs LEDGER", SKIP,
                "no inventory events in any ledger ï¿½ nothing to reconcile "
                "state against")
    elif amb_a:
        rep.add("STATE vs LEDGER", WARN,
                f"{len(amb_a)} market(s) had a fill and a settlement in the "
                f"same second â€” replay order is not recoverable from the data",
                [f"{d['market']}: {d['delta']}" for d in amb_a])
    else:
        rep.add("STATE vs LEDGER", PASS,
                f"{len(net)} market(s) reconcile exactly")

    # â”€â”€ check 2: chain vs gross â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    balances, tokens = {}, []
    if args.no_chain:
        rep.add("CHAIN vs LEDGER", SKIP, "--no-chain: the chain was not read")
    elif not valid_address(args.wallet):
        # a skipped check must NEVER share an exit code with a passed one:
        # cron does not inherit an interactive shell, so an unset MAKER_FUNDER
        # is the most likely way this tool silently checks nothing
        rep.add("CHAIN vs LEDGER", SKIP,
                f"no valid wallet address (got {args.wallet!r}) â€” "
                f"pass --wallet or set MAKER_FUNDER")
    else:
        for key, s in state.items():
            if key == "meta" or not isinstance(s, dict):
                continue
            for f in ("tok_y", "tok_n"):
                if s.get(f):
                    tokens.append(str(s[f]))
        # S8-F1: pruned markets left state but their tokens may still hold
        # chain balances (live mode: real, unredeemed) — archived ids keep
        # them inside chain coverage
        tokens.extend(str(t2) for t2 in meta.get("prune_tokens", ()))
        tokens = sorted(set(tokens))
        if not tokens:
            # a capture run that writes nothing must NOT look like a capture
            # run that succeeded â€” both used to return 4 with no warning
            if args.write_baseline:
                print("\nFATAL: --write-baseline needs tokens to read, but "
                      "state.json carries no tok_y/tok_n. No baseline written.",
                      file=sys.stderr)
                return 1
            rep.add("CHAIN vs LEDGER", SKIP,
                    "state carries no tok_y/tok_n â€” 0 tokens to read")
        else:
            chain = Chain(args.rpcs, deadline=deadline)
            try:
                balances = chain.balances(args.wallet, tokens, progress=True)
            except (RuntimeError, ValueError) as e:
                print(f"\nFATAL: chain read failed: {e}", file=sys.stderr)
                return 1
            nonzero = {t: b for t, b in balances.items() if b > EPS}
            print(f"\n  chain read      {len(tokens)} tokens via {chain.calls} "
                  f"eth_calls; {len(nonzero)} non-zero")

            baseline = {}
            if args.baseline:
                try:
                    with open(args.baseline) as fh:
                        baseline = json.load(fh)
                    if not isinstance(baseline, dict):
                        raise ValueError("baseline is not an object")
                except (OSError, json.JSONDecodeError, ValueError) as e:
                    print(f"FATAL: cannot read baseline {args.baseline}: {e}",
                          file=sys.stderr)
                    return 1

            # decimals tripwire. The scale reference must include the BASELINE:
            # with a captured baseline and no Maker fills yet, total_gross is 0,
            # and a 1e12 inflation from a wrong SHARE_DECIMALS would land in the
            # `base > 0` arm as a friendly rc=3 "surplus" â€” the tripwire
            # disabled in exactly the state it was written for.
            scale = max(sum(g["yes"] + g["no"] for g in gross.values()),
                        sum(float(v) for v in baseline.values()) if baseline else 0.0)
            biggest = max(balances.values()) if balances else 0.0
            if scale > 0 and biggest > ABSURD_MULTIPLE * scale:
                print(f"\nFATAL: chain balance {biggest} is absurd vs ledger/"
                      f"baseline scale {scale} â€” SHARE_DECIMALS likely wrong",
                      file=sys.stderr)
                return 1

            if args.write_baseline:
                if drift_a:
                    print("\nREFUSED: state and ledger already disagree â€” a "
                          "baseline captured now would bake the corruption in",
                          file=sys.stderr)
                    return 1
                if stats["delta"] or gross:
                    print("\nREFUSED: the ledger already contains Maker fills â€” "
                          "a baseline must be captured BEFORE Maker trades, or "
                          "it absorbs our own inventory and Invariant B is "
                          "vacuous forever", file=sys.stderr)
                    return 1
                with open(args.write_baseline, "w") as fh:
                    json.dump(nonzero, fh, indent=1)
                print(f"  baseline written: {args.write_baseline} "
                      f"({len(nonzero)} tokens)")
                return 4        # a capture run is not a passing recon

            short, surp, skipped, compared = check_chain_vs_gross(
                state, gross, balances, baseline)
            report["chain_vs_gross"] = {"faults": short, "surplus": surp,
                                        "skipped": skipped,
                                        "tokens_checked": len(tokens),
                                        "compared": compared}
            if short:
                ns = sum(1 for d in short if d["direction"] == "short")
                no = len(short) - ns
                bits = []
                if ns:
                    bits.append(f"{ns} SHORT (ledger says we hold them; the "
                                f"chain does not)")
                if no:
                    bits.append(f"{no} UNBOOKED (on chain, no ledger entry and "
                                f"no foreign baseline)")
                rep.add("CHAIN vs LEDGER", DRIFT, "; ".join(bits),
                        [f"{d['market']}/{d['leg']} [{d['direction']}]: expected "
                         f"{d['expected']} chain {d['chain']} delta {d['delta']:+}"
                         for d in short])
            elif surp:
                rep.surplus_only = True
                rep.add("CHAIN vs LEDGER", PASS,
                        f"no shortfall; {len(surp)} token(s) hold MORE than the "
                        f"Maker ledger explains (shared wallet: foreign "
                        f"inventory, not certifiable as ours)",
                        [f"{d['market']}/{d['leg']}: expected {d['expected']} "
                         f"chain {d['chain']} delta {d['delta']:+}" for d in surp])
            elif compared <= 0:
                rep.add("CHAIN vs LEDGER", SKIP,
                        f"0 of {len(tokens)} tokens were actually compared "
                        f"({len(skipped)} skipped)")
            else:
                rep.add("CHAIN vs LEDGER", PASS,
                        f"{compared} token(s) match the ledger exactly")
            if skipped:
                rep.add("CHAIN COVERAGE", WARN,
                        f"{len(skipped)} market/leg(s) could not be compared "
                        f"(of {len(tokens)} tokens read)",
                        [f"{s['market']}: {s['why']}" for s in skipped])

    # â”€â”€ check 3: independent source â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if args.cross_check and not balances:
        rep.add("CROSS-CHECK", SKIP,
                "no chain balances were read, so there is nothing to "
                "cross-check against")
    elif args.cross_check:
        try:
            api, api_trunc = data_api_positions(args.wallet,
                                                deadline=deadline)
        except (urllib.error.URLError, TimeoutError, OSError,
                json.JSONDecodeError, RuntimeError) as e:
            rep.add("CROSS-CHECK", WARN, f"data-api unavailable: {e}")
        else:
            # `balances` covers every token in our universe INCLUDING the
            # zero ones, so a token the indexer reports and the chain reads as
            # 0 is already compared here. Tokens outside our universe are
            # foreign inventory and none of our business.
            diffs = []
            for tok in balances:
                c = balances.get(tok, 0.0)
                a = api.get(tok, 0.0)
                if abs(c - a) > DUST_SHARES:
                    diffs.append({"token": tok, "chain": c, "data_api": a})
            compared = sum(1 for t in balances if balances[t] > EPS or api.get(t))
            report["cross_check"] = {"data_api_tokens": len(api),
                                     "compared": compared, "diffs": diffs}
            if not compared:
                rep.add("CROSS-CHECK", SKIP,
                        f"nothing to compare â€” 0 of our {len(tokens)} tokens "
                        f"are non-zero in either source (the wallet's "
                        f"{len(api)} data-api positions are all outside the "
                        f"Maker universe)")
            elif diffs:
                rep.add("CROSS-CHECK", WARN,
                        f"{len(diffs)} of {compared} token(s) disagree between "
                        f"chain and indexer; chain is authoritative",
                        [f"{d['token'][:22]}â€¦ chain={d['chain']} "
                         f"data_api={d['data_api']}" for d in diffs])
            elif api_trunc:
                rep.add("CROSS-CHECK", WARN,
                        f"sources agree across {compared} token(s), but the "
                        f"data-api read hit the {API_MAX_PAGES}-page cap — the "
                        f"indexer view is truncated, so agreement is partial")
            else:
                rep.add("CROSS-CHECK", PASS,
                        f"both sources agree across {compared} token(s)")

    print(rep.render())
    rc = rep.rc()
    report["checks"] = rep.checks
    report["rc"] = rc
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=1, default=str)
        print(f"\n  report: {args.json}")

    verdict = {0: "PASS â€” every applicable check ran and passed",
               2: "DRIFT â€” inventory is not where the engine thinks it is",
               3: "SURPLUS ONLY â€” no shortfall, but unexplained foreign tokens",
               4: "CANNOT CERTIFY â€” a check was skipped or a source disagreed"}
    print(f"\nrc={rc}  {verdict.get(rc, '')}  ({time.time() - t0:.1f}s)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
