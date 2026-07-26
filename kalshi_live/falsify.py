"""FALSIFICATION SUITE — the model cannot be PROVEN without payment, but it can be DISPROVEN now.
Every check runs against live telemetry rows. Any FAIL means the model is broken."""
import glob, json, os, sys, collections
D="/opt/pa2-maker-kalshi-live"
rows=[]
for f in sorted(glob.glob(os.path.join(D,"quotes-*.jsonl"))):
    for l in open(f):
        l=l.strip()
        if l:
            try: rows.append(json.loads(l))
            except Exception: pass
print(f"telemetry rows: {len(rows)}  cycles: {len({r.get('cyc') for r in rows})}  "
      f"markets: {len({r.get('ticker') for r in rows})}\n")
if not rows: sys.exit("no rows yet")
fails=collections.Counter(); checked=collections.Counter()
def chk(name, cond, row=None):
    checked[name]+=1
    if not cond:
        fails[name]+=1
        if fails[name]==1 and row: print(f"  first failure [{name}]: {json.dumps(row)[:220]}")
for r in rows:
    pool=r.get("usd_day") or 0.0
    for tag in ("y","n"):
        share=r.get(f"{tag}_share"); qual=r.get(f"{tag}_qual")
        bdf=r.get(f"{tag}_book_df"); cum=r.get(f"{tag}_cum_ct")
        ref=r.get(f"{tag}_ref"); lowq=r.get(f"{tag}_lowq")
        ct=r.get(f"{tag}_ct"); sc=r.get(f"{tag}_score")
        chk("share in [0,1]", share is None or 0.0<=share<=1.0, r)
        chk("DF total <= raw contracts", bdf is None or cum is None or bdf<=cum+0.06, r)   # cum was emitted at 1dp (+-0.05), book_df at 2dp (+-0.005)
        chk("lowest qualifying <= reference", lowq is None or ref is None or lowq<=ref+1e-9, r)
        chk("qualifies => cum >= target", not qual or (cum or 0)>=(r.get("target") or 0)-1e-6, r)
        chk("no intent => zero score", not (ct==0) or (sc or 0)==0.0, r)
        chk("no intent => zero share", not (ct==0) or (share or 0)==0.0, r)
        chk("score <= our contracts", sc is None or ct is None or sc<=ct+1e-6, r)
    cap=r.get("capture_usd_day")
    chk("capture <= pool", cap is None or cap<=pool+1e-6, r)
    chk("capture >= 0", cap is None or cap>=0.0, r)
    chk("R3: one-sided => capture 0",
        (r.get("y_qual") and r.get("n_qual")) or (cap or 0)==0.0, r)
    ry,rn=r.get("y_ref"),r.get("n_ref")
    chk("book not crossed (ref_y+ref_n<1)", ry is None or rn is None or ry+rn<1.0+1e-9, r)
    ys,ns=r.get("y_share") or 0.0, r.get("n_share") or 0.0
    if r.get("y_qual") and r.get("n_qual") and pool>0:
        chk("capture == mean(share)*pool", abs((cap or 0)-((ys+ns)/2.0)*pool)<0.01, r)
print(f"{'CHECK':38s} {'n':>7s} {'fails':>7s}  verdict")
bad=0
for k in sorted(checked):
    f=fails[k]; bad+= (f>0)
    print(f"{k:38s} {checked[k]:7d} {f:7d}  {'FAIL <<<' if f else 'pass'}")
print(f"\n{len(checked)-bad}/{len(checked)} checks pass. "
      + ("MODEL NOT FALSIFIED by these tests." if not bad else "MODEL FALSIFIED — see FAIL rows."))
