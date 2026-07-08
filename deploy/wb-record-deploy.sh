#!/usr/bin/env bash
# wb-record-deploy.sh — record what was just deployed, so a KEYLESS (cloud) session
# can verify deploy parity without SSH access to the VPS.
#
# WHY: wb-release-cut.sh runs ON the VPS from a tarball that EXCLUDES .git, so it cannot
# know the git SHA it deployed. The SHA is only knowable here, on the operator's local
# checkout at deploy time. This writes deploy/LAST_DEPLOY.json from local git; commit it
# and cloud sessions can then answer "is the branch I'm on what's actually deployed?"
#
# Usage (run from repo root, on the machine you deployed FROM, right after wb-release-cut.sh):
#   bash deploy/wb-record-deploy.sh <STAMP> [<S223_MARKER_COUNT>]
#   git add deploy/LAST_DEPLOY.json && git commit -m "chore(wb): record deploy <STAMP>" && git push
#
# STAMP is the release stamp you passed to wb-release-cut.sh. The optional second arg is the
# marker count wb-release-cut.sh printed; if omitted it is derived from the local tree.
set -euo pipefail

STAMP="${1:?usage: bash deploy/wb-record-deploy.sh STAMP [S223_MARKER_COUNT]}"
MARKERS="${2:-}"

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

SHA="$(git rev-parse HEAD)"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
RECORDED_AT="$(date -u +%FT%TZ)"
[ -n "$MARKERS" ] || MARKERS="$(grep -c "S223" bots/weather_bot.py || echo 0)"

DIRTY="clean"
if ! git diff --quiet || ! git diff --cached --quiet; then
  DIRTY="DIRTY-at-record-time"
  echo "WARNING: working tree is dirty — the recorded SHA ($SHA) may not exactly match the tarball you shipped." >&2
fi

cat > deploy/LAST_DEPLOY.json <<JSON
{
  "release_stamp": "$STAMP",
  "git_sha": "$SHA",
  "git_branch": "$BRANCH",
  "s223_markers_weather_bot": $MARKERS,
  "tree_state_at_record": "$DIRTY",
  "recorded_at": "$RECORDED_AT",
  "recorded_by": "deploy/wb-record-deploy.sh",
  "note": "Written locally at deploy time. git_sha is what was tarred+shipped. Commit this file so keyless sessions can verify parity via scripts/wb_resume_check.sh."
}
JSON

echo "wrote deploy/LAST_DEPLOY.json:"
cat deploy/LAST_DEPLOY.json
echo
echo "Next: git add deploy/LAST_DEPLOY.json && git commit -m 'chore(wb): record deploy $STAMP' && git push -u origin $BRANCH"
