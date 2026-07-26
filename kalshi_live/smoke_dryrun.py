"""SMOKE TEST — drive the REAL pipeline over REAL live books, with writes simulated.

Reads are genuine (books, positions, balance, resting orders). Writes go nowhere: TRADING_MODE
is dry_run, so create/cancel are simulated. Capital cap is raised so we see what the algorithm
WOULD place if un-parked — the whole point, since at cap=$1 every accumulating order is skipped
and the interesting half of the pipeline never executes.

Writes its plan/state into a TEMP dir so live bookkeeping is untouched.
"""
import os, sys, json, tempfile, collections
os.environ["KALSHI_TRADING_MODE"] = "dry_run"
sys.path.insert(0, "/opt/pa2-maker-kalshi-live")
import maker_kalshi_quoter as q

TMP = tempfile.mkdtemp(prefix="kalshi_smoke_")
q.DATA_DIR = TMP
q.STATE_FILE = os.path.join(TMP, "quoter_state.json")
q.STOP_FILE = os.path.join(TMP, "STOP")
q.SCORE_PATH = os.path.join(TMP, "scores.json")
q.LOCK_FILE = os.path.join(TMP, "quoter.lock")   # own lock: never contend with the live timer

CAP = float(os.environ.get("SMOKE_CAP", "250"))
q.MAX_TOTAL_CAPITAL = CAP
print("SMOKE: mode=%s cap=$%.0f footprint_top=%d horizon=%.0fd gate=%d grace=%d allowlist=%d series"
      % (q.TRADING_MODE if hasattr(q, "TRADING_MODE") else "dry_run", CAP, q.FOOTPRINT_TOP,
         q.MAX_DAYS_TO_CLOSE, q.PRESENCE_GATE, q.DROP_GRACE, len(q.SERIES_ALLOW)))
print("        data dir (temp): %s\n" % TMP)

try:
    q.run_once()
except Exception as e:
    import traceback; traceback.print_exc()
    print("\nSMOKE FAILED: run_once raised %r" % (e,)); sys.exit(1)

rows = []
for f in os.listdir(TMP):
    if f.startswith("plans-"):
        for l in open(os.path.join(TMP, f)):
            if l.strip(): rows.append(json.loads(l))
if not rows:
    print("SMOKE FAILED: no plan row emitted"); sys.exit(1)
p = rows[-1]
keys = ["footprint","gated_out","quoted_markets","creates","cancels","create_skipped","create_fail",
        "quote_fail","capped_markets","presence_skipped_markets","presence_skipped_late_entry",
        "presence_skipped_execution_only","grace_retained","est_capital_usd","committed_usd",
        "unqualifiable","empty_books","reads"]
print("PLAN:")
for k in keys:
    if p.get(k) is not None: print("   %-32s %s" % (k, p[k]))

tel = []
for f in os.listdir(TMP):
    if f.startswith("quotes-"):
        for l in open(os.path.join(TMP, f)):
            if l.strip(): tel.append(json.loads(l))
print("\nTELEMETRY: %d market rows" % len(tel))
if tel:
    would = [r for r in tel if (r.get("y_ct") or 0) > 0 or (r.get("n_ct") or 0) > 0]
    print("   markets we WOULD quote: %d" % len(would))
    caps = sorted((r.get("capture_usd_day") or 0) for r in tel)
    print("   modelled capture $/day: min %.4f  median %.4f  max %.4f"
          % (caps[0], caps[len(caps)//2], caps[-1]))
    g = collections.Counter()
    for r in tel:
        for k in (r.get("gates") or {}): g[k] += 1
    print("   gate reasons: %s" % dict(g))
    print("\n   TOP 8 BY MODELLED CAPTURE (what the algo would prioritise):")
    for r in sorted(tel, key=lambda x: -(x.get("capture_usd_day") or 0))[:8]:
        print("     %-38s pool=$%-7.0f cap=$%-8.4f y_ct=%-5s n_ct=%-5s"
              % (r["ticker"][:38], r.get("usd_day") or 0, r.get("capture_usd_day") or 0,
                 r.get("y_ct"), r.get("n_ct")))
print("\nSMOKE OK — pipeline completed end to end, no exception, plan emitted.")
