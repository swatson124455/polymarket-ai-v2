BR=claude/esports-sharp-line-rebuild-36c8u9-7m96gg
echo "=== 0) python + tooling check ==="
PY=python3
if ! python3 -c 'import requests' 2>/dev/null; then
  if [ -x /opt/polymarket-ai-v2-esports/venv/bin/python3 ] && /opt/polymarket-ai-v2-esports/venv/bin/python3 -c 'import requests' 2>/dev/null; then
    PY=/opt/polymarket-ai-v2-esports/venv/bin/python3
  else
    echo "NOTE: no 'requests' available — sharp-line metrics will run; the CLOB-orientation edge step may fail"
  fi
fi
echo "using PY=$PY"
echo "=== 1) fresh repo clone (read-only backtest workspace) ==="
rm -rf /home/ubuntu/eb-backtest
if command -v git >/dev/null 2>&1; then
  git clone --quiet --depth 1 -b "$BR" https://github.com/swatson124455/polymarket-ai-v2 /home/ubuntu/eb-backtest
else
  curl -fsSL "https://codeload.github.com/swatson124455/polymarket-ai-v2/tar.gz/refs/heads/$BR" -o /tmp/ebrepo.tgz
  mkdir -p /home/ubuntu/eb-backtest && tar -xzf /tmp/ebrepo.tgz -C /home/ubuntu/eb-backtest --strip-components=1
fi
cd /home/ubuntu/eb-backtest || { echo "clone failed"; exit 1; }
echo "repo at $(pwd), HEAD: $(git rev-parse --short HEAD 2>/dev/null || echo tarball)"
echo "=== 2) fetch results window from PandaScore (key from shared env) ==="
export PANDASCORE_API_KEY="$(grep -m1 '^PANDASCORE_API_KEY=' /opt/pa2-shared/.env | cut -d= -f2-)"
if [ -z "$PANDASCORE_API_KEY" ]; then echo "ERROR: PANDASCORE_API_KEY not found in /opt/pa2-shared/.env"; exit 1; fi
$PY -m esports_v2.scripts.fetch_results_window --days-back 4 --games valorant,cs2,lol,dota2 --out /home/ubuntu/eb-odds/results_window.jsonl
echo "--- sample of fetched results ---"
head -5 /home/ubuntu/eb-odds/results_window.jsonl
wc -l /home/ubuntu/eb-odds/results_window.jsonl
echo "=== 3) run the REAL eval driver (reduce -> join -> metrics -> edge) ==="
$PY -m esports_v2.scripts.eval_sharp_line --snapshots /home/ubuntu/eb-odds/pinnodds_snapshots.jsonl --bulk /home/ubuntu/eb-odds/results_window.jsonl --cs2 /nonexistent.json --de-vig simple
