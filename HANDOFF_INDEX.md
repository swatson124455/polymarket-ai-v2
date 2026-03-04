# Polymarket AI V2 — Handoff Index

**Read order for a new agent:**

| # | File | What |
|---|------|------|
| 1 | **NEW_AGENT_SUMMARY.md** | **Start here.** Complete handoff: architecture, critical concepts (side semantics!), key files, config, known issues, what to work on next. Updated 2026-02-18. |
| 2 | **HANDOFF_2026_02_20.md** | Latest session (GROUND TRUTH for status): Step 0 + Tier 1 complete, Tier 2 pending. Recommendation audit, longshot bias, alpha decay, DDM/EDDM, ADWIN, fee sim, adverse selection, sqrt impact, prompt cache, auto-alerts. |
| 3 | **HANDOFF_2026_02_19.md** | Previous session: training feedback loop, migration 017 status, model accuracy issue, position management fixes. |
| 4 | **MEMORY.md** (at `~/.claude/projects/.../memory/MEMORY.md`) | Claude agent persistent memory. Environment, DB, mock patterns, all audit fixes, training data facts. Ground truth for system status. |

**Archived (2026-02-19):** 60+ stale docs, setup scripts, one-time migration scripts, and legacy diagnostic scripts moved to `archive/`. Subdirectories: `old_docs/`, `setup_scripts/`, `one_time_scripts/`, `diag_scripts/`, `legacy_status_docs/`, `artifacts/`, `scripts/`.

**Active docs/** (kept): CONCEPTS.md, LIVE_RUN_CHECKLIST.md, PAPER_TRADING_MODE.md, DATA_PIPELINES.md, PHASES_AND_REBUTTALS_MASTER.md, architecture/, deployment/.

**Active scripts/** (kept): Core ingestion, migration, training, diagnostic, and maintenance scripts (~40 files). Legacy diag scripts archived to `archive/diag_scripts/`.

**Schema:** `python scripts/run_migrations.py` (migrations 001-018, note: 017-018 blocked by Supabase pooler — see HANDOFF_2026_02_19.md).

**Implementation plan:** Originally 47 items across 5 tiers from master audit (2026-02-20). **⚠️ PLAN FILE OVERWRITTEN** — `shimmying-greeting-kay.md` was reused for the warning-fix session and no longer contains the 47-item plan. Authoritative status: **Step 0 (5 items) + Tier 1 (9 items) = COMPLETE**. **Tier 2 (12 items) = PENDING** (see HANDOFF_2026_02_20.md for full Tier 2 list). Tiers 3-5 not started.

**Tests:** 321 passing (24 files). Run: `python -m pytest tests/unit/ -v --no-cov --tb=short`

**Critical concept**: YES and NO are both BUY operations in Polymarket. See NEW_AGENT_SUMMARY.md Section 2.
