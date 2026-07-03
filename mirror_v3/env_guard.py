"""Boot-time environment guard: unset is a boot ERROR, never a default.

The v2 poison this exists to kill (verified 2026-07-02, docs/m0_db_results):
the deployed MB ran LIVE at 100% canary with the paper->live auto-advance
armed purely by ABSENT env keys falling through to code defaults
(CANARY_AUTO_ADVANCE unset -> settings default true; TRADING_PHASE unset ->
"paper" while SIMULATION_MODE=false). Nobody chose that state.

v3 rule: the safety trio + DB URL must be EXPLICITLY present in the process
env. Values are then validated:
  SIMULATION_MODE     must be truthy (paper). Running live additionally
                      requires MIRROR3_LIVE_ACK to equal the exact ack phrase —
                      a deliberate operator action that cannot happen by drift.
  CANARY_AUTO_ADVANCE must be falsy. No exceptions — auto capital promotion
                      is banned in v3 regardless of mode.
  CANARY_STAGE        must be '0' in paper. (Live stages are only reachable
                      with the ack, and still never auto-advance.)

Call assert_safe_env() FIRST in every v3 entrypoint, before any import that
could construct engines or touch the DB.
"""

from __future__ import annotations

import os
from typing import Mapping

REQUIRED_EXPLICIT = (
    "SIMULATION_MODE",
    "CANARY_AUTO_ADVANCE",
    "CANARY_STAGE",
    "DATABASE_URL",
)

LIVE_ACK_KEY = "MIRROR3_LIVE_ACK"
LIVE_ACK_PHRASE = "I-UNDERSTAND-THIS-TRADES-REAL-MONEY"

_TRUTHY = ("true", "1", "yes")
_FALSY = ("false", "0", "no")


class EnvGuardError(RuntimeError):
    """Unsafe or incomplete environment — refuse to boot."""


def assert_safe_env(environ: Mapping[str, str] | None = None) -> dict:
    """Validate the v3 environment. Returns a summary dict on success,
    raises EnvGuardError (with every violation listed) otherwise."""
    env = os.environ if environ is None else environ
    problems: list[str] = []

    missing = [k for k in REQUIRED_EXPLICIT if not str(env.get(k, "")).strip()]
    if missing:
        problems.append(
            f"missing/empty required env keys (explicit presence required, "
            f"code defaults are BANNED here): {missing}"
        )

    sim_raw = str(env.get("SIMULATION_MODE", "")).strip().lower()
    is_paper = sim_raw in _TRUTHY
    if sim_raw and not is_paper:
        if str(env.get(LIVE_ACK_KEY, "")) != LIVE_ACK_PHRASE:
            problems.append(
                f"SIMULATION_MODE={sim_raw!r} (LIVE) without {LIVE_ACK_KEY} set to "
                f"the exact ack phrase — live trading must be an explicit operator "
                f"action, never config drift"
            )

    caa = str(env.get("CANARY_AUTO_ADVANCE", "")).strip().lower()
    if caa and caa not in _FALSY:
        problems.append(
            f"CANARY_AUTO_ADVANCE={caa!r} — automatic paper->live capital "
            f"promotion is banned in v3 (must be false)"
        )

    stage = str(env.get("CANARY_STAGE", "")).strip()
    if is_paper and stage and stage != "0":
        problems.append(
            f"CANARY_STAGE={stage!r} in paper mode — must be 0 until the "
            f"acceptance gate passes and the operator flips live with the ack"
        )

    if problems:
        raise EnvGuardError(
            "mirror_v3 env guard REFUSED TO BOOT:\n  - " + "\n  - ".join(problems)
        )

    return {
        "mode": "paper" if is_paper else "LIVE",
        "canary_stage": stage,
        "auto_advance": False,
    }
