# Maker lane research artifacts (preserved 2026-07-16)

One-shot research scripts + their measured outputs from the Maker feasibility
sessions of 2026-07-13..16 (originally in the session-b3b85ed5 scratchpad;
committed here so the evidence base survives scratchpad cleanup). These are
read-only/GET-only research one-shots, NOT production code — nothing here
trades or is imported by any bot.

- mm_real_cohort.py      REAL-maker cohort measurement (69 wallets, 30d income
                         from REWARD/MAKER_REBATE payment records, chain
                         spot-verify). The 07-18 `maker-sim-readout` task
                         references this as the refresh pattern.
- mm_maker_backtest.py   30d policy replay over 1,590 resolved markets
                         (naive/gated x stale/fast; strictly-through fills;
                         pools excluded = floor).
- mm_maker_econ.py/_econ2.py + mm_maker_econ_out/_refined.json
                         Per-maker reward-economics measurement (official
                         S-formula, both-token books).
- mm_sweep.py/mm_sweep2.py + mm_sweep_out.json + mm_markets_raw.json + mm_cids.txt
                         2,081-market live sweep (reward density, capacity,
                         toxicity inputs).
- mm_analyze.py + mm_analysis.json
                         Post-fill drift / toxicity analysis on the sweep.
- mm_final_rows.json     Final summary rows quoted in memory.

Numbers derived from these are recorded (with dates + caveats) in the memory
file project_mm_feasibility_study.md; where they disagree, memory is the
curated record and these are the raw evidence.
