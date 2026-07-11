# DIAGNOSTIC (read-only, zero PinnOdds calls): sharp-vs-PM gap distribution
# over every PM-priced match in the snapshot log. Answers "do mispricings the
# edge rule could bet even EXIST at capture time?" No outcomes, no P&L.
set -e
BR=claude/esports-sharp-line-rebuild-gqy1na
rm -rf /home/ubuntu/eb-edgedist
git clone --quiet --depth 1 -b "$BR" https://github.com/swatson124455/polymarket-ai-v2 /home/ubuntu/eb-edgedist
cd /home/ubuntu/eb-edgedist || exit 1
echo "HEAD: $(git rev-parse --short HEAD)"
python3 -m esports_v2.scripts.edge_distribution --snapshots /home/ubuntu/eb-odds/pinnodds_snapshots.jsonl
