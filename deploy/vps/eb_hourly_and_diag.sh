echo "=== 1) crontab BEFORE ==="
crontab -l 2>/dev/null
crontab -l 2>/dev/null | grep -v 'collect_pinnodds_standalone.py' > /tmp/ct_eb2 || true
echo '0 * * * * /usr/bin/python3 /home/ubuntu/eb-odds/collect_pinnodds_standalone.py >> /home/ubuntu/eb-odds/collect.log 2>&1' >> /tmp/ct_eb2
crontab /tmp/ct_eb2
echo "=== 2) crontab AFTER (collector now hourly; other lines preserved) ==="
crontab -l
echo "=== 3) FULL-FILE mismatch diagnostic (uses the DEPLOYED matcher; Gamma only, zero PinnOdds quota) ==="
python3 - <<'PY'
import importlib.util, json
from datetime import datetime
spec = importlib.util.spec_from_file_location("col", "/home/ubuntu/eb-odds/collect_pinnodds_standalone.py")
col = importlib.util.module_from_spec(spec); spec.loader.exec_module(col)
refs = col.pm_index()
print(f"PM match-winner markets live now: {len(refs)}")
rows = {}; pm_rows = 0; total = 0
with open("/home/ubuntu/eb-odds/pinnodds_snapshots.jsonl", encoding="utf-8") as f:
    for line in f:
        try: d = json.loads(line)
        except Exception: continue
        total += 1
        if d.get("condition_id"): pm_rows += 1
        k = (d.get("home"), d.get("away"), str(d.get("starts"))[:10])
        rows[k] = d
print(f"snapshot rows={total}  distinct matchups={len(rows)}  rows_with_pm_fields={pm_rows}")
hits = []
for (h,a,day), d in sorted(rows.items()):
    r = col.match_ref(h, a, d.get("starts"), refs)
    if r: hits.append((h,a,day,r))
print(f"\nA) expanded-matcher hits across ALL collected matchups: {len(hits)}")
for h,a,day,r in hits:
    print(f"   PINN {h} vs {a} ({day}) -> PM {r[4]} vs {r[5]} ({r[6]}) price={r[3]} cid={r[0][:12]}")
print("\nB) date-free both-team alignments (teams match; exposes any DATE/window bug):")
n=0
for (h,a,day), d in sorted(rows.items()):
    for r in refs:
        if col.match2(h,a,r[4],r[5]) is not None:
            n+=1; print(f"   PINN {h} vs {a} ({day})  <->  PM {r[4]} vs {r[5]} (PM day {r[6]})")
if n==0: print("   (none — no PM market shares BOTH teams with any collected matchup, on any date)")
print("\nC) one-side team overlaps within +/-3 days (name-form near-miss candidates):")
n=0
def dd(a,b):
    try: return abs((datetime.strptime(a,"%Y-%m-%d")-datetime.strptime(b,"%Y-%m-%d")).days)
    except Exception: return 99
for (h,a,day), d in sorted(rows.items()):
    for r in refs:
        if dd(day,r[6])<=3 and (col.same_team(h,r[4]) or col.same_team(h,r[5]) or col.same_team(a,r[4]) or col.same_team(a,r[5])):
            n+=1; print(f"   PINN {h} vs {a} ({day})  ~  PM {r[4]} vs {r[5]} ({r[6]})")
if n==0: print("   (none)")
print("\nD) upcoming PM match winners (what PinnOdds must list for a hit):")
for r in sorted(refs, key=lambda x:x[6]):
    if r[6] >= "2026-07-10": print(f"   {r[6]}  {r[4]} vs {r[5]}  price={r[3]}")
PY
