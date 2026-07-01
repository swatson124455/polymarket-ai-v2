# Kickoff prompt for a fresh session

Paste the block below into a new session on `swatson124455/polymarket-ai-v2`.

---
You are continuing the **esports_silo** rebuild. Work on branch
`claude/blissful-davinci-twt397` (HEAD `dd425f1`).

**BEFORE DOING ANYTHING**, read these three files in full — they override default behavior:
`esports_silo/COMMANDMENTS.md`, `esports_silo/HANDOFF.md`, `esports_silo/PLAN.md`.

Operating discipline (non-negotiable):
- **Verify every claim on data. No assumptions. No fabrication.** Tag each fact
  `VERIFIED` / `DOC-SOURCED` / `UNVERIFIED`. The prior effort logged 186+ errors from
  confident-before-verified — do not repeat it.
- **Commandments:** (1) P&L is not evidence, (2) de-vig does not exist, (3) surgical cut
  (minimal self-contained extraction; label adaptations as DEVIATION, never "port"),
  (4) quarantine by default — anything unverified on real data is excluded until proven clean.
- **You are in a silo:** no VPS / DB / API / data-file / network access (`curl` → 403).
  Anything that needs the box, you *write and hand to the operator to run* — never claim you ran it.

**First task:** build `esports_silo/scripts/verify_data_quality.py` — a **read-only**
data-quality battery: null-rate, duplicate `match_id`, temporal integrity (look-ahead),
label integrity (winner resolvability), cross-source winner **agreement** (truth check),
and quarantine-leak checks. It is the Commandment-4 master gate: **no carried data may be
used until it passes on the operator's box.** Make it runnable there; report honestly here.

**Then** follow `PLAN.md`'s build list and critical path.

Do not open a PR unless asked. Commit to the branch with clear messages.
---
