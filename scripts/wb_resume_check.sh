#!/bin/bash
# wb_resume_check.sh — self-deriving WeatherBot session resume-integrity check.
#
# Replaces the hand-typed 7-step resume checklist (S223 thread). Everything
# "expected" is derived from the repo itself:
#   - the pinned branch and headline-fix SHAs come from docs/WB_HANDOFF_MANIFEST.json
#   - "up to date" means HEAD == origin/<pinned branch> after a fetch (no hardcoded SHA)
#   - code fingerprints come from the manifest (updated in the same commit as the code)
#
# Usage:   bash scripts/wb_resume_check.sh
# Exit:    0 = all PASS (warnings allowed) · 1 = at least one FAIL
#
# NOT covered (needs VPS access, run from a machine with the deploy key):
#   ssh ubuntu@18.201.216.0 "systemctl is-active polymarket-weather; \
#     readlink -f /opt/polymarket-ai-v2-weather | xargs basename; \
#     sudo systemctl show polymarket-weather -p NRestarts --value; \
#     grep -c S223 /opt/polymarket-ai-v2-weather/bots/weather_bot.py"
set -uo pipefail

FAILS=0
WARNS=0

pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; FAILS=$((FAILS + 1)); }
warn() { echo "WARN  $1"; WARNS=$((WARNS + 1)); }

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [ -z "$ROOT" ]; then
  echo "FAIL  not inside a git repository"
  exit 1
fi
cd "$ROOT"

MANIFEST="docs/WB_HANDOFF_MANIFEST.json"
if [ ! -f "$MANIFEST" ]; then
  echo "FAIL  manifest $MANIFEST missing — cannot derive expected state"
  exit 1
fi

mget() { python3 -c "import json,sys; m=json.load(open('$MANIFEST')); print(m$1)"; }

EXPECT_REPO="$(mget "['repo']")"
EXPECT_BRANCH="$(mget "['branch']")"

# --- 1. Right repo, not a worktree, right branch --------------------------
REMOTE_URL="$(git remote get-url origin 2>/dev/null)"
case "$REMOTE_URL" in
  *"$EXPECT_REPO"*) pass "remote origin -> $EXPECT_REPO" ;;
  *)                fail "remote origin is '$REMOTE_URL', expected to contain '$EXPECT_REPO'" ;;
esac

case "$ROOT" in
  */.claude/worktrees/*) fail "toplevel '$ROOT' is an agent WORKTREE, not the main repo" ;;
  *)                     pass "toplevel is the main repo ($ROOT)" ;;
esac

CURRENT="$(git rev-parse --abbrev-ref HEAD)"
if [ "$CURRENT" = "$EXPECT_BRANCH" ]; then
  pass "on pinned branch '$EXPECT_BRANCH'"
else
  fail "on branch '$CURRENT', pinned branch is '$EXPECT_BRANCH' — run: git fetch origin && git checkout $EXPECT_BRANCH && bash scripts/wb_resume_check.sh"
fi

# --- 2. Up to date with remote (derived, no hardcoded SHA) -----------------
if git fetch origin "$EXPECT_BRANCH" --quiet 2>/dev/null; then
  AHEAD="$(git rev-list --count "origin/$EXPECT_BRANCH..HEAD" 2>/dev/null || echo '?')"
  BEHIND="$(git rev-list --count "HEAD..origin/$EXPECT_BRANCH" 2>/dev/null || echo '?')"
  if [ "$BEHIND" = "0" ] && [ "$AHEAD" = "0" ]; then
    pass "HEAD == origin/$EXPECT_BRANCH ($(git log --oneline -1))"
  elif [ "$BEHIND" != "0" ]; then
    fail "HEAD is $BEHIND commit(s) BEHIND origin/$EXPECT_BRANCH — run: git pull origin $EXPECT_BRANCH"
  else
    warn "HEAD is $AHEAD commit(s) AHEAD of origin/$EXPECT_BRANCH (unpushed work — push before ending the session)"
  fi
else
  fail "could not fetch origin/$EXPECT_BRANCH"
fi

# --- 3. Headline fix commits are in this history ---------------------------
for SHA in $(python3 -c "import json; print(' '.join(json.load(open('$MANIFEST'))['headline_commits'].keys()))"); do
  DESC="$(mget "['headline_commits']['$SHA']")"
  if git cat-file -e "$SHA^{commit}" 2>/dev/null && git merge-base --is-ancestor "$SHA" HEAD 2>/dev/null; then
    pass "commit $SHA in history — $DESC"
  else
    fail "commit $SHA MISSING from history — $DESC"
  fi
done

# --- 4. Required docs present ----------------------------------------------
for DOC in $(python3 -c "import json; print(' '.join(json.load(open('$MANIFEST'))['required_docs']))"); do
  if [ -f "$DOC" ]; then
    pass "doc present: $DOC"
  else
    fail "doc MISSING: $DOC"
  fi
done

# --- 5. Code fingerprints match the manifest --------------------------------
N_FP="$(python3 -c "import json; print(len(json.load(open('$MANIFEST'))['fingerprints']))")"
i=0
while [ "$i" -lt "$N_FP" ]; do
  FP_FILE="$(mget "['fingerprints'][$i]['file']")"
  FP_PAT="$(mget "['fingerprints'][$i]['pattern']")"
  FP_EXPECT="$(mget "['fingerprints'][$i]['expect']")"
  if [ ! -f "$FP_FILE" ]; then
    fail "fingerprint file MISSING: $FP_FILE"
  else
    GOT="$(grep -cF -- "$FP_PAT" "$FP_FILE" || true)"
    if [ "$GOT" = "$FP_EXPECT" ]; then
      pass "fingerprint '$FP_PAT' x$GOT in $FP_FILE"
    else
      fail "fingerprint '$FP_PAT' in $FP_FILE: got $GOT, manifest expects $FP_EXPECT (stale manifest or missing fix — update manifest in the SAME commit as code changes)"
    fi
  fi
  i=$((i + 1))
done

# --- 6. Deploy parity (informational — never a hard FAIL) -------------------
# Keyless sessions can't SSH the VPS; this compares HEAD to the committed deploy
# record (deploy/LAST_DEPLOY.json), which the next deploy populates via
# deploy/wb-record-deploy.sh. UNKNOWN/ahead are expected mid-session, so WARN only.
DEPLOY_REC="deploy/LAST_DEPLOY.json"
if [ -f "$DEPLOY_REC" ]; then
  DEP_SHA="$(python3 -c "import json; v=json.load(open('$DEPLOY_REC')).get('git_sha'); print(v if v else '')" 2>/dev/null)"
  if [ -z "$DEP_SHA" ]; then
    warn "deploy parity UNKNOWN — $DEPLOY_REC is a placeholder (no deploy recorded via deploy/wb-record-deploy.sh yet)"
  else
    DEP_STAMP="$(python3 -c "import json; print(json.load(open('$DEPLOY_REC')).get('release_stamp') or '?')" 2>/dev/null)"
    if [ "$DEP_SHA" = "$(git rev-parse HEAD)" ]; then
      pass "deploy parity: HEAD == last deployed SHA (release $DEP_STAMP)"
    elif git merge-base --is-ancestor "$DEP_SHA" HEAD 2>/dev/null; then
      warn "deploy parity: HEAD is $(git rev-list --count "$DEP_SHA..HEAD") commit(s) AHEAD of last deploy $DEP_STAMP ($DEP_SHA) — undeployed work on the branch"
    else
      warn "deploy parity: recorded deploy SHA $DEP_SHA (release $DEP_STAMP) is NOT in this branch history — investigate before trusting the live box"
    fi
  fi
else
  warn "deploy parity UNKNOWN — no $DEPLOY_REC (deploy-record mechanism not yet in this branch)"
fi

# --- Summary -----------------------------------------------------------------
echo "----------------------------------------------------------------"
if [ "$FAILS" -eq 0 ]; then
  echo "RESULT: ALL PASS (${WARNS} warning(s)) — session state is intact."
  echo "Next: read docs/WEATHER_STATUS.md (canonical: OPEN DECISIONS + WHAT IS LIVE)."
  echo "Live-VPS health is NOT covered here — see header for the ssh one-liner."
  exit 0
else
  echo "RESULT: $FAILS FAILURE(S), $WARNS warning(s) — STOP, do no other work, report to operator."
  exit 1
fi
