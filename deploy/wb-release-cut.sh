#!/usr/bin/env bash
# WB splinter release cut — runs ON the VPS, invoked as:
#   ssh ubuntu@VPS bash -s "$STAMP" < deploy/wb-release-cut.sh
# Expects /tmp/wb-$STAMP.tar.gz already uploaded (scp'd by the caller).
#
# What it does (matches the manual runbook exactly):
#   extract → reuse current release's venv (no new deps) → chown →
#   flip /opt/polymarket-ai-v2-weather symlink → restart polymarket-weather →
#   verify active + S222 marker present.
#
# Rollback (printed below): flip the symlink back to the previous release
# and restart. Releases are never deleted by this script.
set -euo pipefail

STAMP="${1:?usage: bash -s STAMP (with /tmp/wb-STAMP.tar.gz uploaded)}"
TARBALL="/tmp/wb-${STAMP}.tar.gz"
RELEASE="/opt/pa2-weather-releases/${STAMP}"
LINK="/opt/polymarket-ai-v2-weather"

[ -f "$TARBALL" ] || { echo "ERROR: $TARBALL not found"; exit 1; }

OLD=$(readlink -f "$LINK")
echo "previous release: $OLD"
[ -d "$OLD/venv" ] || { echo "ERROR: no venv in current release ($OLD) to reuse"; exit 1; }

sudo mkdir -p "$RELEASE"
sudo tar xzf "$TARBALL" -C "$RELEASE"
# Reuse venv — valid while requirements are unchanged. If a release adds deps,
# rebuild instead: python -m venv + pip install -r requirements.txt.
sudo cp -a "$OLD/venv" "$RELEASE/venv"
sudo chown -R polymarket:polymarket "$RELEASE"

sudo ln -sfn "$RELEASE" "$LINK"
sudo systemctl restart polymarket-weather
rm -f "$TARBALL"

sleep 5
STATUS=$(sudo systemctl is-active polymarket-weather || true)
S222=$(grep -c "S222" "$LINK/bots/weather_bot.py" || true)
echo "service: $STATUS | S222 markers in weather_bot.py: $S222"
echo "ROLLBACK: sudo ln -sfn $OLD $LINK && sudo systemctl restart polymarket-weather"
sudo journalctl -u polymarket-weather -n 8 --no-pager
[ "$STATUS" = "active" ] || { echo "ERROR: service not active after restart"; exit 1; }
