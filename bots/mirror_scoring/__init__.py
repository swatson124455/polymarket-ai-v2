"""MirrorBot v3 trader-scoring engine (MB-scoped, shadow-only).

Scores watchlist candidates on what MB can actually capture:
first mirrorable entry per condition_id, at MB latency, under MB exits,
net of costs — with cluster-robust statistics, exact binomial bounds,
out-of-sample persistence gating, and BH-FDR selection.

HARD CONSTRAINT: this package has NO order authority. It reads the DB and
writes reports. Wiring any output of this package into order placement
requires passing the validation kill-criteria in validation.py first
(see docs in that module) plus operator signoff.
"""

from bots.mirror_scoring.config import ScoringConfig

__all__ = ["ScoringConfig"]
