echo "=== 1) candidate PANDASCORE env lines (MASKED — no key material printed) ==="
for f in /opt/pa2-shared/.env /opt/polymarket-ai-v2-esports/.env; do
  [ -f "$f" ] || continue
  echo "--- $f"
  grep -nE '^(export )?PANDASCORE' "$f" | python3 -c '
import sys
for line in sys.stdin:
    line = line.rstrip("\n")
    loc, _, rest = line.partition(":")
    name, _, val = rest.partition("=")
    tail = val[-2:].encode().hex() if val else ""
    print(f"  line {loc}: {name}=  len={len(val)}  first3={val[:3]!r}  last2hex={tail}")
'
done
echo "=== 2) probe sanitized key against /videogames (1 request) ==="
KEY=$(grep -m1 -E '^(export )?PANDASCORE_API_KEY=' /opt/pa2-shared/.env | sed 's/^export //' | cut -d= -f2- | tr -d '"\r' | sed "s/^'//; s/'\$//" | xargs)
echo "sanitized key length: ${#KEY}"
CODE=$(curl -s -o /tmp/ps_probe.json -w '%{http_code}' -H "Authorization: Bearer $KEY" 'https://api.pandascore.co/videogames?per_page=1')
echo "Bearer-header probe status: $CODE"
if [ "$CODE" != "200" ]; then
  echo "--- response body (first 200 bytes) ---"; head -c 200 /tmp/ps_probe.json; echo
  CODE2=$(curl -s -o /dev/null -w '%{http_code}' "https://api.pandascore.co/videogames?per_page=1&token=$KEY")
  echo "query-param token probe status: $CODE2"
fi
if [ "$CODE" = "200" ]; then
  echo "=== 3) key OK — fetching results window + running the eval ==="
  export PANDASCORE_API_KEY="$KEY"
  cd /home/ubuntu/eb-backtest || exit 1
  python3 -m esports_v2.scripts.fetch_results_window --days-back 4 --games valorant,cs2,lol,dota2 --out /home/ubuntu/eb-odds/results_window.jsonl
  echo "--- fetched rows ---"; wc -l /home/ubuntu/eb-odds/results_window.jsonl; head -3 /home/ubuntu/eb-odds/results_window.jsonl
  python3 -m esports_v2.scripts.eval_sharp_line --snapshots /home/ubuntu/eb-odds/pinnodds_snapshots.jsonl --bulk /home/ubuntu/eb-odds/results_window.jsonl --cs2 /nonexistent.json --de-vig simple
else
  echo "=== 3) SKIPPED — key rejected. The PandaScore key in /opt/pa2-shared/.env is invalid or expired."
  echo "    Fix: log into pandascore.co, copy the current token, update the PANDASCORE_API_KEY line."
fi
