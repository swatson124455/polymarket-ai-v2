#!/usr/bin/env bash
# MB TAILABLE PROGRAM - POST-MERGE STEPS FOR THE THREE FILES THAT LIVE
# OUTSIDE THE READOUT CLONE (docs/MB_TAILABLE_PLAN.md, 2026-09-08).
#
# The readout clone (/opt/pa2-shared/mb_readout) deploys itself: it
# `reset --hard`s to master at 12:30Z. Three things it depends on live
# OUTSIDE it on purpose (a watchdog whose definition can be narrowed by
# the thing it watches is not a watchdog) and therefore need a hand step:
#   1. /opt/pa2-shared/mb_chain_stages.json  - add stages 11 (tailable list)
#      and 12 (candidate pipeline)
#   2. /opt/pa2-shared/mb_stall_watch.json   - the 'dive' job watches the
#      shared fill cache (every dive writes it), not the promo dir
#   3. polymarket's crontab                  - the serial dive runner at
#      13:00Z
# All three are IDEMPOTENT here. Run as root (sudo) AFTER the merged code
# is in the clone - i.e. after the 12:30Z refresh that picks it up, or a
# forced refresh - because the [chain] watchdog would otherwise report
# tailable=MISSING / pipeline=MISSING against the OLD cron on the next
# 11:40Z run. Nothing here touches the clone, the DB or any service.
#
#   sudo bash /opt/pa2-shared/mb_readout/deploy/mb_tailable_post_merge.sh
#   sudo bash ... --check      # report only, change nothing
set -uo pipefail
CHECK=0; [ "${1:-}" = "--check" ] && CHECK=1
STAGES=/opt/pa2-shared/mb_chain_stages.json
STALL=/opt/pa2-shared/mb_stall_watch.json
CLONE=/opt/pa2-shared/mb_readout
RUNNER=$CLONE/scripts/vps_jobs/tailable_dive_runner.sh
RLOG=/opt/pa2-shared/mb_copyable_data/deep_dive/tailable_dive_runner.log
CRON_LINE="0 13 * * * bash $RUNNER >> $RLOG 2>&1"
TS() { date -u +%FT%TZ; }
rc=0

echo "[$(TS)] clone HEAD: $(git -C "$CLONE" log -1 --format='%h %s' 2>/dev/null || echo '?')"
for f in scripts/mb_tailable_list.py scripts/mb_candidate_pipeline.py scripts/vps_jobs/tailable_dive_runner.sh; do
  if [ -f "$CLONE/$f" ]; then echo "  present: $f"; else echo "  !! MISSING in clone: $f - merge not refreshed yet; STOP"; rc=2; fi
done
[ $rc -eq 0 ] || exit $rc

# 1. chain stages (JSON edit via python; keeps everything else verbatim)
python3 - "$STAGES" "$CHECK" <<'EOF'
import json, os, sys
p, check = sys.argv[1], sys.argv[2] == "1"
d = json.load(open(p))
keys = [s["key"] for s in d["stages"]]
want = [("tailable", "tailable list"), ("pipeline", "candidate pipeline")]
missing = [(k, m) for k, m in want if k not in keys]
if not missing:
    print("  stages: OK (tailable + pipeline present; %d stages)" % len(keys))
elif check:
    print("  stages: WOULD ADD", missing)
else:
    for k, m in missing:
        d["stages"].append({"key": k, "marker": m})
    tmp = p + ".tmp"
    json.dump(d, open(tmp, "w"), indent=2)
    os.replace(tmp, p)
    print("  stages: ADDED", missing, "->", len(d["stages"]), "stages")
EOF

# 2. stall watch 'dive' job -> shared fill cache
python3 - "$STALL" "$CHECK" <<'EOF'
import json, os, sys
p, check = sys.argv[1], sys.argv[2] == "1"
d = json.load(open(p))
want = "/opt/pa2-shared/mb_copyable_data/copyable_cache/chain_fills"
job = next((j for j in d["jobs"] if j.get("name") == "dive"), None)
if job is None:
    print("  stall: !! no 'dive' job found - leaving the file alone"); sys.exit(0)
if job.get("log") == want:
    print("  stall: OK ('dive' watches the fill cache)")
elif check:
    print("  stall: WOULD CHANGE 'dive' log", job.get("log"), "->", want)
else:
    job["log"] = want
    job["_note"] = ("2026-09-08 (plan P4): progress = the shared fill cache every "
                    "dive writes; pipeline runner dives (deep_dive_pipeline/) are "
                    "watched by the same job")
    tmp = p + ".tmp"
    json.dump(d, open(tmp, "w"), indent=2)
    os.replace(tmp, p)
    print("  stall: CHANGED 'dive' log ->", want)
EOF

# 2b. output dirs under the root-owned mb_copyable_data (found live 2026-09-09
#     01:18Z: the runner FATALed on mkdir deep_dive_pipeline)
for d in /opt/pa2-shared/mb_copyable_data/tailable /opt/pa2-shared/mb_copyable_data/deep_dive_pipeline; do
  if [ -d "$d" ] && [ "$(stat -c %U "$d")" = polymarket ]; then echo "  dir: OK $d"
  elif [ $CHECK -eq 1 ]; then echo "  dir: WOULD CREATE/CHOWN $d"
  else mkdir -p "$d" && chown polymarket:polymarket "$d" && echo "  dir: CREATED/CHOWNED $d"; fi
done

# 3. runner cron (polymarket)
if crontab -u polymarket -l 2>/dev/null | grep -qF "tailable_dive_runner.sh"; then
  echo "  cron: OK (runner line present)"
elif [ $CHECK -eq 1 ]; then
  echo "  cron: WOULD ADD: $CRON_LINE"
else
  ( crontab -u polymarket -l 2>/dev/null; echo "$CRON_LINE" ) | crontab -u polymarket -
  echo "  cron: ADDED runner line (13:00Z daily, as polymarket)"
fi

# ownership sanity: the clone must stay polymarket-owned (a root run of the
# readout cron re-contaminates .git and the 12:30Z refresh then fails)
n_root=$(find "$CLONE/.git" ! -user polymarket 2>/dev/null | wc -l)
echo "  clone .git entries not owned by polymarket: $n_root $( [ "$n_root" = 0 ] && echo OK || echo '!! chown -R polymarket:polymarket needed')"
echo "[$(TS)] done (check=$CHECK). Next 11:40Z [chain] line must show 12 stages incl. tailable=OK pipeline=OK."
