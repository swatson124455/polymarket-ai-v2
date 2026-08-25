#!/usr/bin/env bash
# MB evidence -> S3 (operator rec 7, 2026-08-25). DORMANT until the operator
# provisions credentials; then it self-activates on the nightly cron.
# OPERATOR SETUP (one time, ~3 min):
#   1. AWS console -> S3 -> create bucket (suggest: mb-evidence-<acct>)
#   2. IAM user with PutObject/ListBucket on that bucket only
#   3. On the VPS as root, create /root/.mb_backup_s3 (chmod 600):
#        export AWS_ACCESS_KEY_ID=...
#        export AWS_SECRET_ACCESS_KEY=...
#        export MB_S3_BUCKET=s3://<bucket>/mb_evidence
#   4. apt-get install -y awscli   (not currently installed)
# The nightly backup cron then calls this script; without the creds file it
# exits 0 silently (the Windows pull remains the off-box leg either way).
set -uo pipefail
CRED=/root/.mb_backup_s3
[ -f "$CRED" ] || exit 0
command -v aws >/dev/null || { echo "[$(date -u +%FT%TZ)] s3: creds present but awscli missing"; exit 1; }
. "$CRED"
SRC=/opt/pa2-backups/mb_evidence
aws s3 sync "$SRC" "$MB_S3_BUCKET" --exclude "*" --include "mb_evidence_*.tar.gz" --include "*_20*.dump" --include "*_20*.txt" \
  && echo "[$(date -u +%FT%TZ)] s3 sync OK" || echo "[$(date -u +%FT%TZ)] s3 sync FAILED"
