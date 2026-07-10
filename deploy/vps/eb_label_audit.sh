BR=claude/esports-sharp-line-rebuild-gqy1na
echo "=== 1) refresh repo clone (picks up the id-mapped winner fix) ==="
rm -rf /home/ubuntu/eb-backtest
git clone --quiet --depth 1 -b "$BR" https://github.com/swatson124455/polymarket-ai-v2 /home/ubuntu/eb-backtest
cd /home/ubuntu/eb-backtest || exit 1
echo "HEAD: $(git rev-parse --short HEAD)"
echo "=== 2) re-fetch results window (fixed fetcher) ==="
export PANDASCORE_API_KEY="$(grep -m1 '^PANDASCORE_API_KEY=' /opt/pa2-shared/.env | cut -d= -f2- | tr -d '"\r')"
python3 -m esports_v2.scripts.fetch_results_window --days-back 4 --games valorant,cs2,lol,dota2,r6 --out /home/ubuntu/eb-odds/results_window.jsonl
echo "=== 3) PER-MATCH LABEL DUMP (verify winners against real-world results) ==="
python3 -m esports_v2.scripts.dump_joined --snapshots /home/ubuntu/eb-odds/pinnodds_snapshots.jsonl --bulk /home/ubuntu/eb-odds/results_window.jsonl
echo "=== 4) metrics on the re-fetched labels ==="
python3 -m esports_v2.scripts.eval_sharp_line --snapshots /home/ubuntu/eb-odds/pinnodds_snapshots.jsonl --bulk /home/ubuntu/eb-odds/results_window.jsonl --cs2 /nonexistent.json --de-vig simple
