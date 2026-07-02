# Kickoff prompt for a fresh session

Paste the block below into a new session on `swatson124455/polymarket-ai-v2`.

---
You are continuing the **esports_silo** rebuild. Work on branch
`claude/blissful-davinci-twt397-n94qo6` (current tip — see `git log --oneline`; don't rely on a
pinned SHA).

**BEFORE DOING ANYTHING**, read these three files in full — they override default behavior:
`esports_silo/COMMANDMENTS.md`, `esports_silo/HANDOFF.md`, `esports_silo/PLAN.md`.

Operating discipline (non-negotiable):
- **Verify every claim on data. No assumptions. No fabrication.** Tag each fact
  `VERIFIED` / `DOC-SOURCED` / `UNVERIFIED`. The prior effort logged 186+ errors from
  confident-before-verified — do not repeat it.
- **Commandments:** (1) P&L is not evidence, (2) de-vig does not exist, (3) surgical cut
  (minimal self-contained extraction; label adaptations as DEVIATION, never "port"),
  (4) quarantine by default — anything unverified on real data is excluded until proven clean.
- **You are in a silo:** no VPS / DB / API / network access (`curl` → 403). The only in-repo
  data is `data/esports_matches_bulk.jsonl` and `data/cs2/pandascore_cs2.json` (commit `369606b`),
  locally readable — everything else lives on the operator's box. Anything that needs the box,
  you *write and hand to the operator to run* — never claim you ran it.

**First task:** `esports_silo/scripts/verify_data_quality.py` — the Commandment-4 read-only
master gate — is **BUILT** (null-rate, dup `match_id`, look-ahead, winner-resolvability,
cross-source winner agreement, quarantine-leak; `--jsonl` + DB modes). Do **not** rebuild it.
Per **D2 GATE-THEN-BUILD**: the next step is the operator running it on the box and clearing data
from quarantine. **Build nothing new until that gate passes.**

**Then** — only after the gate clears — follow `PLAN.md`'s build list and critical path.

Do not open a PR unless asked. Commit to the branch with clear messages.
---
