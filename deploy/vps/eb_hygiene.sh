# Read-only hygiene audit of the snapshot store + companion files.
# Zero PinnOdds calls; safe to run anytime.
set -e
BR=claude/esports-sharp-line-rebuild-gqy1na
rm -rf /home/ubuntu/eb-hygiene
git clone --quiet --depth 1 -b "$BR" https://github.com/swatson124455/polymarket-ai-v2 /home/ubuntu/eb-hygiene
cd /home/ubuntu/eb-hygiene || exit 1
echo "HEAD: $(git rev-parse --short HEAD)"
echo "=== files in the data dir ==="
ls -la /home/ubuntu/eb-odds/
echo "=== aliases.json parses ==="
python3 -m json.tool /home/ubuntu/eb-odds/aliases.json > /dev/null && echo OK
echo "=== snapshot store audit ==="
python3 -m esports_v2.scripts.verify_snapshot_hygiene --snapshots /home/ubuntu/eb-odds/pinnodds_snapshots.jsonl
