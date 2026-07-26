#!/usr/bin/env python3
"""Chase the y_book_df==0 rows that claim y_qual==True. Should be impossible."""
import json
rows = [json.loads(l) for l in open("quotes_frozen.jsonl")]
bad = [r for r in rows if r.get("y_qual") and (r.get("y_book_df") or 0) <= 0]
print("N_QUAL_WITH_ZERO_BOOKDF", len(bad))
for r in bad[:5]:
    print(json.dumps({k: r[k] for k in ("ticker","target","df","y_ref","y_book_df",
                                        "y_cum_ct","y_qual","y_lowq","y_share")}))
badn = [r for r in rows if r.get("n_qual") and (r.get("n_book_df") or 0) <= 0]
print("N_SIDE_QUAL_WITH_ZERO_BOOKDF", len(badn))
for r in badn[:5]:
    print(json.dumps({k: r[k] for k in ("ticker","target","df","n_ref","n_book_df",
                                        "n_cum_ct","n_qual","n_lowq","n_share")}))
# how far does the qualifying walk reach in ticks? ref - lowq
import collections
d = collections.Counter()
for r in rows:
    if r.get("y_qual") and r.get("y_ref") is not None and r.get("y_lowq") is not None:
        d[round((r["y_ref"] - r["y_lowq"]) / 0.01)] += 1
print("YES walk depth in ticks (ref-lowq) -> count:", sorted(d.items())[:15])
tot = sum(d.values())
cum = 0
for k in sorted(d):
    cum += d[k]
    if k <= 4:
        print(f"  walk_depth<={k} ticks: {cum}/{tot} = {100.0*cum/tot:.1f}%")
