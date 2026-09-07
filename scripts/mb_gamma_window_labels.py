#!/usr/bin/env python3
"""Capture-era gamma window crawl — labels for OUTSIDE-DB tokens.

WHY (handoff build item 4, 2026-09-07; ROI review A3): ~80k sweep tokens
sit OUTSIDE the ingestion DB, so shadow_label_supplement's markets-table
join can never resolve them — board-wide label coverage was 36.3% and
survivorship-flagged rows (0x3382c5c6c6 at 33%) are judged on a minority
of their markets.

WHY THIS SHAPE (all MEASURED 2026-09-07, session probes on the VPS):
  * gamma /markets?clob_token_ids=<tok> is INERT — 0 rows even for a
    market gamma's own listing just returned (round-trip test); the
    comma-joined form 422s. Same class as the documented silent-ignore
    of ?condition_ids= (backfill_resolutions_gamma.py module doc).
  * page size caps at 100 rows regardless of limit; offset 422s between
    1000 and 3000 — a blind crawl of the ~4.3M-id space is out.
  * end_date_min/end_date_max filters WORK (probed 08-26 window).
  So: crawl closed markets whose endDate falls in the CAPTURE ERA (every
  sweep token was traded after capture start 2026-08-26, so its market
  was open then), in adaptive date windows — a window that still returns
  full pages at the offset ceiling is split in half, down to a floor
  that is DISCLOSED if it ever overflows (never silently truncated).

REUSE, NOT RE-IMPLEMENTATION (canon rule):
  gamma row -> cache entry   backfill_resolutions_gamma.map_gamma_market
                             (closed + definitive >=0.99/<=0.01 only;
                             ambiguous/split/open skipped and counted)
  cache merge                shadow_label_supplement.merge_entry
                             (existing YES/NO entries NEVER overwritten;
                             disagreement = loud CONFLICT for a human)

Output: merged into the same gamma_resolutions.json every grader reads
(supplement_outcomes / res_at_map pick the new labels up, no code change).
Entries are stamped source=gamma_window_supplement.

SAFETY: dry-run by default (--write required); timestamped backup +
atomic replace + post-write readback-shrink guard (same protocol as
shadow_label_supplement); every stage asserts non-empty input; caps and
skips are printed, never silent. READ-ONLY against gamma (GETs, paced).

    python scripts/mb_gamma_window_labels.py --tokens <sweep_tokens.jsonl> \
        [--start 2026-08-20T00:00:00Z] [--write]
    ... --self-test    # offline: window split + coverage logic, no network
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backfill_resolutions_gamma as bg  # noqa: E402  (map_gamma_market)
import shadow_label_supplement as sls  # noqa: E402  (merge_entry, guards)

GAMMA = "https://gamma-api.polymarket.com"
CACHE = "/opt/pa2-shared/mb_copyable_data/copyable_cache/gamma_resolutions.json"
PAGE = 100          # measured server cap (limit>100 still returns 100)
MAX_OFFSET = 1000   # measured: 1000 works, 3000 = 422; ceiling per window
MIN_WINDOW_S = 60.0  # a window this small that still overflows is DISCLOSED
CAPTURE_ERA_START = "2026-08-20T00:00:00Z"  # capture began 2026-08-26; a
#                     margin for markets that closed shortly before it


def parse_iso_z(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def iso_z(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def load_target_tokens(paths: list[str]) -> set[str]:
    toks: set[str] = set()
    for p in paths:
        with open(p) as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("token_id"):
                    toks.add(str(r["token_id"]))
    if not toks:
        raise sls.LabelJobError(
            f"0 target tokens read from {paths} — refusing to report "
            f"'nothing to do' on an empty input")
    return toks


def coverage(tokens: set[str], cache: dict) -> tuple[int, int]:
    """(n_tokens, n_labeled-by-cache) — the number this job exists to move."""
    lab = set(sls.cache_labels(cache))
    return len(tokens), len(tokens & lab)


def split_window(a: float, b: float) -> tuple[tuple[float, float],
                                              tuple[float, float]]:
    mid = a + (b - a) / 2.0
    return (a, mid), (mid, b)


def crawl_plan_step(page_len: int, offset: int,
                    window_s: float) -> str:
    """Pure decision for one fetched page: 'next-page' | 'window-done' |
    'split' | 'overflow-disclose'. A full page at the offset ceiling means
    the window holds more rows than pagination can reach — split it, or
    disclose loudly when it is already at the floor."""
    if page_len < PAGE:
        return "window-done"
    if offset + PAGE <= MAX_OFFSET:
        return "next-page"
    if window_s / 2.0 >= MIN_WINDOW_S:
        return "split"
    return "overflow-disclose"


def fetch_page(a: float, b: float, offset: int, timeout_s: float) -> list:
    qs = urllib.parse.urlencode({
        "limit": PAGE, "offset": offset, "closed": "true",
        "end_date_min": iso_z(a), "end_date_max": iso_z(b)})
    req = urllib.request.Request(f"{GAMMA}/markets?{qs}",
                                 headers={"User-Agent": "mb-window-labels"})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        rows = json.load(r)
    if not isinstance(rows, list):
        raise sls.LabelJobError(f"non-list gamma payload at offset {offset} "
                                f"window {iso_z(a)}..{iso_z(b)}")
    return rows


def run(args) -> int:
    with open(args.cache) as f:
        cache = json.load(f)
    if not isinstance(cache, dict) or not cache:
        raise sls.LabelJobError(f"cache empty/not an object: {args.cache}")
    targets = load_target_tokens(args.tokens)
    n_t, n_lab0 = coverage(targets, cache)
    print(f"[window] targets={n_t} labeled-before={n_lab0} "
          f"({100.0 * n_lab0 / n_t:.1f}%)")
    t_start, t_end = parse_iso_z(args.start), parse_iso_z(args.end)
    assert t_end > t_start, "empty crawl window"
    stack = [(t_start, t_end)]
    pages = rows_seen = mapped = skipped_open = 0
    new = kept = conflict = overflow = 0
    conflicts: list[str] = []
    t0 = time.monotonic()
    while stack:
        a, b = stack.pop()
        offset = 0
        while True:
            page = fetch_page(a, b, offset, args.timeout)
            pages += 1
            rows_seen += len(page)
            for m in page:
                e = bg.map_gamma_market(m)
                if e is None:
                    skipped_open += 1
                    continue
                cid = str(m.get("conditionId") or "")
                if not cid:
                    skipped_open += 1
                    continue
                mapped += 1
                e["source"] = "gamma_window_supplement"
                r = sls.merge_entry(cache, cid, e)
                if r == "new":
                    new += 1
                elif r == "kept":
                    kept += 1
                else:
                    conflict += 1
                    conflicts.append(cid)
            if args.rps > 0:
                time.sleep(max(0.0, 1.0 / args.rps))
            step = crawl_plan_step(len(page), offset, b - a)
            if step == "next-page":
                offset += PAGE
                continue
            if step == "split":
                stack.extend(split_window(a, b))
            elif step == "overflow-disclose":
                overflow += 1
                print(f"  OVERFLOW: window {iso_z(a)}..{iso_z(b)} still "
                      f"full at offset {MAX_OFFSET} and at the "
                      f"{MIN_WINDOW_S:.0f}s floor — rows beyond the "
                      f"ceiling NOT fetched (disclosed, never silent)")
            break
        if pages % 50 == 0:
            print(f"  ...{pages} pages, {rows_seen} rows, new={new}, "
                  f"{time.monotonic() - t0:.0f}s, {len(stack)} windows left")
    print(f"[window] pages={pages} rows={rows_seen} "
          f"definitive={mapped} open/ambiguous-skipped={skipped_open} "
          f"overflow-windows={overflow}")
    print(f"[window] merge: new={new} already-known={kept} "
          f"CONFLICT={conflict}")
    for c in conflicts[:20]:
        print(f"  CONFLICT (cache kept, NOT overwritten): {c}")
    if len(conflicts) > 20:
        print(f"  ... {len(conflicts) - 20} more conflicts")
    assert rows_seen, "0 rows over the whole crawl — query shape broken, " \
                      "not 'no news' (empty-set false-pass class)"
    n_t2, n_lab1 = coverage(targets, cache)
    print(f"[window] target coverage: {n_lab0}/{n_t} -> {n_lab1}/{n_t2} "
          f"({100.0 * n_lab1 / n_t2:.1f}%) "
          f"{'(would-be, DRY RUN)' if not args.write else ''}")
    if not args.write:
        print("\nDRY RUN — cache NOT modified. Re-run with --write to apply.")
        return 0
    if new == 0:
        print("\nnothing new to write — cache untouched")
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    bak = f"{args.cache}.pre-window-supplement-{stamp}"
    if not os.path.exists(bak):
        shutil.copy2(args.cache, bak)
    tmp = args.cache + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f)
    os.replace(tmp, args.cache)
    with open(args.cache) as f:
        back = json.load(f)
    if len(back) < len(cache):
        raise sls.LabelJobError("post-write readback SHRANK the cache — "
                                f"restore from {bak} immediately")
    print(f"\nWROTE {args.cache}: {len(back)} keys (+{new}); backup {bak}")
    return 0


def _self_test() -> int:
    print("SELF-TEST — mb_gamma_window_labels (offline)\n")
    ok = True
    # [plan] short page ends the window regardless of offset
    ok1 = (crawl_plan_step(99, 0, 3600) == "window-done"
           and crawl_plan_step(0, 900, 3600) == "window-done")
    print(f"  [plan] short page -> window-done : {ok1}")
    ok &= ok1
    # [plan] full page below ceiling pages on; at ceiling splits
    ok2 = (crawl_plan_step(PAGE, 0, 3600) == "next-page"
           and crawl_plan_step(PAGE, MAX_OFFSET - PAGE, 3600) == "next-page"
           and crawl_plan_step(PAGE, MAX_OFFSET, 3600) == "split")
    print(f"  [plan] pages to ceiling then splits : {ok2}")
    ok &= ok2
    # [plan] at the window floor, overflow is DISCLOSED not split forever
    ok3 = crawl_plan_step(PAGE, MAX_OFFSET, MIN_WINDOW_S) == \
        "overflow-disclose"
    print(f"  [plan] floor window overflow -> disclose : {ok3}")
    ok &= ok3
    # [split] halves cover exactly, no gap/overlap
    (a1, m1), (m2, b1) = split_window(0.0, 100.0)
    ok4 = a1 == 0.0 and b1 == 100.0 and m1 == m2 == 50.0
    print(f"  [split] exact halves : {ok4}")
    ok &= ok4
    # [cov] counts labeled targets via BOTH legs of resolved entries
    cache = {"c1": {"resolution": "YES", "yes_token_id": "y1",
                    "no_token_id": "n1"},
             "c2": {"resolution": None, "yes_token_id": "y2",
                    "no_token_id": "n2"}}
    ok5 = (coverage({"y1", "n1", "y2", "zz"}, cache) == (4, 2))
    print(f"  [cov] resolved-only, both legs : {ok5}")
    ok &= ok5
    # [reuse] merge semantics come from shadow_label_supplement — no local
    # merge/mapping re-implementation in this module
    import inspect
    src = inspect.getsource(sys.modules[__name__])
    ok6 = all(f"def {n}(" not in src
              for n in ("merge_entry", "map_gamma_market", "cache_labels"))
    print(f"  [reuse] no re-implementation of merge/mapping : {ok6}")
    ok &= ok6
    # [tokens] empty target list raises, never 'nothing to do'
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.jsonl")
        open(p, "w").write('{"token_id": "t1"}\nnot json\n{"x": 1}\n')
        ok7 = load_target_tokens([p]) == {"t1"}
        open(p, "w").write("\n")
        try:
            load_target_tokens([p])
            ok7 = False
        except sls.LabelJobError:
            pass
    print(f"  [tokens] dedupe + empty-input guard : {ok7}")
    ok &= ok7
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Capture-era gamma window crawl -> resolution labels "
                    "for outside-DB tokens")
    ap.add_argument("--tokens", nargs="+",
                    default=["/opt/pa2-shared/mb_copyable_data/backtest/"
                             "sweep_tokens.jsonl"],
                    help="jsonl file(s) of {'token_id': ...} targets "
                         "(coverage is reported against these)")
    ap.add_argument("--cache", default=CACHE)
    ap.add_argument("--start", default=CAPTURE_ERA_START,
                    help="crawl endDate window start (capture-era default)")
    ap.add_argument("--end", default=iso_z(time.time()),
                    help="crawl endDate window end (default now)")
    ap.add_argument("--write", action="store_true",
                    help="apply the merge (default: dry run)")
    ap.add_argument("--rps", type=float, default=5.0,
                    help="request pacing (sleep 1/rps between pages)")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        raise SystemExit(_self_test())
    try:
        raise SystemExit(run(args))
    except sls.LabelJobError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        raise SystemExit(2)
