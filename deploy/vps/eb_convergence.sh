# DIAGNOSTIC (read-only, zero PinnOdds calls): does the Polymarket price
# CONVERGE toward the sharp line pre-start, and who moves (PM or the line)?
# The pm_moved term is the price-CLV of buying the gap side at first sight.
set -e
BR=claude/esports-sharp-line-rebuild-gqy1na
rm -rf /home/ubuntu/eb-converge
git clone --quiet --depth 1 -b "$BR" https://github.com/swatson124455/polymarket-ai-v2 /home/ubuntu/eb-converge
cd /home/ubuntu/eb-converge || exit 1
echo "HEAD: $(git rev-parse --short HEAD)"
python3 -m esports_v2.scripts.pm_convergence --snapshots /home/ubuntu/eb-odds/pinnodds_snapshots.jsonl
