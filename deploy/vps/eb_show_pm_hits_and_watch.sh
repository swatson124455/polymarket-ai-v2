echo "=== PM-captured rows (proof of capture) ==="
python3 - <<'PY'
import json
from collections import defaultdict
g = defaultdict(lambda: {"n":0,"first":None,"last":None,"pf":None,"pl":None,"cid":None})
with open("/home/ubuntu/eb-odds/pinnodds_snapshots.jsonl", encoding="utf-8") as f:
    for line in f:
        try: d = json.loads(line)
        except Exception: continue
        if not d.get("condition_id"): continue
        k = (d.get("home"), d.get("away"), str(d.get("starts"))[:16])
        e = g[k]; e["n"] += 1
        if e["first"] is None: e["first"] = d.get("captured_at"); e["pf"] = d.get("market_price")
        e["last"] = d.get("captured_at"); e["pl"] = d.get("market_price"); e["cid"] = d.get("condition_id")
print(f"distinct PM-captured matchups: {len(g)}")
for (h,a,st), e in sorted(g.items()):
    print(f"  {h} vs {a}  starts={st}  snaps={e['n']}  PM price {e['pf']} -> {e['pl']}  cid={str(e['cid'])[:14]}")
    print(f"     captured {str(e['first'])[:19]} .. {str(e['last'])[:19]}")
PY
echo "=== install PM-first-hit watcher (idempotent; preserves other cron lines) ==="
cat > /home/ubuntu/eb-odds/pm_hit_watch.sh <<'SH'
#!/bin/bash
SNAP=/home/ubuntu/eb-odds/pinnodds_snapshots.jsonl
MARK=/home/ubuntu/eb-odds/PM_FIRST_HIT.txt
[ -f "$MARK" ] && exit 0
HIT=$(grep -m1 '"condition_id": "0x' "$SNAP" 2>/dev/null)
if [ -n "$HIT" ]; then
  { echo "FIRST_PM_HIT $(date -u +%FT%TZ)";
    echo "total_pm_rows=$(grep -c '"condition_id": "0x' "$SNAP")";
    echo "$HIT"; } > "$MARK"
fi
SH
chmod +x /home/ubuntu/eb-odds/pm_hit_watch.sh
crontab -l 2>/dev/null > /tmp/ct_ebw || true
grep -qF 'pm_hit_watch.sh' /tmp/ct_ebw || echo '7 * * * * /home/ubuntu/eb-odds/pm_hit_watch.sh >> /home/ubuntu/eb-odds/pm_hit_watch.log 2>&1' >> /tmp/ct_ebw
crontab /tmp/ct_ebw
/home/ubuntu/eb-odds/pm_hit_watch.sh
echo "--- crontab now ---"; crontab -l
echo "--- marker ---"; cat /home/ubuntu/eb-odds/PM_FIRST_HIT.txt 2>/dev/null || echo "(no marker; unexpected if PM rows exist)"
