# WB REBUILD — SESSION-OPENING PROMPT (paste this into the new session)

> Companion to `WB_REBUILD_KICKOFF.md` (the brief). This file is the short
> prompt the operator pastes to START the session. Written 2026-07-23.

```
WB SESSION — REBUILD. Scope: WeatherBot only.

READ FIRST: WB_REBUILD_KICKOFF.md at the repo root. That file is your brief —
data inventory, the findings that survive, the option space, and the traps.
WB_S235_KICKOFF_PROMPT.md is SUPERSEDED; do not execute its QUEUE.

TREE (get this wrong and you write to another bot's branch):
Work ONLY in .claude/worktrees/wb-whiteboard, branch
claude/new-whiteboard-session-9b23tq. The main checkout is on SB's branch. Use
git -C <worktree> for every git op and absolute worktree paths for everything
else. Verify `git branch --show-current` before any repo write.

STANDING RULES: never quote P&L — communicate via calibration/Brier only
(CLAUDE.md #11). WB-ALWAYS-GLOBAL: never add US-only filters. One fix per commit.
RULE ONE-A: never touch MB. Never blacklist a city or disable a side to make a
number look better — fix the model, sizing, or gates.

THE MANDATE
The old plan is scrapped. Do not resume it, do not improve it. Answer one
question exhaustively, from the evidence:

  Is there any configuration in which WeatherBot has positive expected edge —
  and if so, which one, on what evidence?

Every option in kickoff §4 must be either advanced with evidence or killed with
a written reason. An option dismissed without a reason is not dismissed. This is
deep research: go wide before you go deep, and say what you did not examine.

"There is no positive-edge configuration" is a PERMITTED and valuable
conclusion. Do not manufacture an edge in order to have something to build. We
are in paper mode — nothing is bleeding, so nothing forces a bad build.

WHAT NOT TO DO
- Do not delete or prune any data. All of it is preserved and verified; the
  collectors (pws_mesh, nat_mesh, mesh_debias, Maker feed) keep running.
- Do not deploy. Deploys are operator-gated, full stop.
- Do not change the calibrator before ~08-07. You MAY measure and design against
  it — the hands-off restricts changing it, not studying it.
- Do not re-derive kickoff §3. Contradicting those findings needs better
  evidence, not a fresh opinion.

START HERE
Kickoff §4.A — measure the UNTRADED universe. Every finding we have is
conditional on markets the bot self-selected by disagreeing with price. Whether
weather markets are soft where we DON'T trade is untested, and the answer
reorders every other option. First sub-task: find out whether we already store a
market-price source for markets we never entered (Gamma/CLOB history,
weather_forecasts, traded_markets). If we don't, say so early — it changes scope.

DELIVERABLE
A ranked recommendation with kill reasons, not a survey. For each option: the
thesis, the test that would falsify it, the data required, and a go/kill verdict
with the evidence. Flag explicitly what you could not verify and give me the
commands to verify it myself.

MEASUREMENT DISCIPLINE (kickoff §5 — each of these produced a wrong conclusion
in a single prior session): dedup markets or every n is ~28x inflated;
calibration_check does NOT dedup its own per-side x lead-time section; run a
concentration check BEFORE presenting any number; watch for base-rate traps
(92.7% "accuracy" was climatology); treat |BSS| > 2 as an artifact; verify the
probability frame empirically rather than assuming it.

Before you start: confirm you have read the kickoff and state, in your own
words, what you believe the mandate is and what your first three actions will
be. If anything in the brief is ambiguous, ask me then — not after you have
spent the session on the wrong question.
```

## Why it is shaped this way

- **Play-back before work.** The dominant failure mode for a rebuild session is
  researching the wrong question confidently. Forcing a restatement of the
  mandate + first three actions surfaces a misread in one exchange instead of
  one session.
- **"None" is permitted, stated twice** (here and kickoff §0). The strongest
  pull on a rebuild session is to justify itself by finding an edge. Paper mode
  means there is no cost to concluding the honest answer.
- **§4.A is pinned as the first task** because every finding we hold is
  conditional on self-selected markets; measuring the untraded universe reorders
  the whole option ranking, and it may be cheap or impossible depending on
  whether an untraded price source exists.
- **The traps are inlined, not just linked.** Each one produced a materially
  wrong conclusion in S234; a fresh session with no scar tissue will repeat them.
