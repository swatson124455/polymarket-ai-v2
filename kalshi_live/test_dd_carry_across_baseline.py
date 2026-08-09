"""FAILING-BEFORE pins: drawdown must CARRY across a day/marker re-baseline.

THE DEFECT (found live 2026-08-09, cost real money). The re-baseline branch of the daily-loss
governor re-seeded `equity_day_peak` at CURRENT equity unconditionally, so a drawdown still OPEN at
the boundary was forgiven outright. Measured from the live plan rows that night:

    23:52:05Z  equity 275.16  peak None   dd None    <- restart, day-change re-baseline
    23:54:33Z  equity 275.16  peak None   dd None    <- marker consumed = SECOND re-baseline
    23:56:26Z  equity 273.96  peak 275.16 dd 1.20
    23:58:18Z  equity 272.16  peak 275.16 dd 3.00    <- correctly tracking toward the $10 halt
    00:00:09Z  equity 267.61  peak None   dd None    <- UTC roll: $7.55 of slide ERASED
    00:11:44Z  equity 268.54  peak 268.71 dd 0.17

A bot bleeding at 23:59 got a clean full envelope at 00:00 with no operator action and no log line.
The daily budget is meant to cap NEW losses, not to launder an in-flight one.

THE FIX: at re-baseline, record carry = max(0, prev_peak - equity); the halt then tests
dd_raw + carry_eff, where carry_eff decays as equity climbs back above the day start (a debt, not a
permanent penalty). KALSHI_DD_CARRY=0 restores the old behaviour.

These are SOURCE-level pins: the arithmetic lives inside run_once's equity block, which needs a full
live client to drive, so the logic is pinned by re-implementing the documented formula against the
live incident's own numbers, plus source assertions that the quoter actually carries those terms.
"""
import maker_kalshi_quoter as q


def carry_at_rebaseline(prev_peak, equity, enabled=True):
    """Mirror of the fix's re-baseline arm."""
    return max(0.0, prev_peak - equity) if enabled else 0.0


def effective_dd(peak, equity, day_start, carry, enabled=True):
    """Mirror of the fix's steady-state arm."""
    dd_raw = peak - equity
    c = carry if enabled else 0.0
    carry_eff = max(0.0, c - max(0.0, equity - day_start))
    return dd_raw + carry_eff, carry_eff


def test_the_live_incident_would_now_be_carried():
    """The exact 2026-08-09 numbers: $7.55 open at the boundary must not vanish."""
    carry = carry_at_rebaseline(prev_peak=275.16, equity=267.61)
    assert round(carry, 2) == 7.55
    dd, carry_eff = effective_dd(peak=268.71, equity=268.54, day_start=267.61, carry=carry)
    # old behaviour reported 0.17 and kept trading on a full fresh envelope
    assert round(dd - (268.71 - 268.54), 2) == round(carry_eff, 2)
    assert dd > 6.0, "the open bleed must still count against the limit"
    assert dd < 10.0, "but it must not manufacture a halt that did not happen"


def test_carry_decays_as_equity_recovers():
    carry = 7.55
    start = 267.61
    _, c_at_start = effective_dd(267.61, 267.61, start, carry)
    _, c_up_3 = effective_dd(271.0, 270.61, start, carry)
    _, c_repaid = effective_dd(276.0, 275.16, start, carry)
    assert round(c_at_start, 2) == 7.55
    assert round(c_up_3, 2) == 4.55           # recovered $3.00 of the hole
    assert c_repaid == 0.0                    # climbed all the way out -> debt cleared


def test_no_carry_when_rebaselining_at_a_new_high():
    """Re-baselining while UP must not invent a debt."""
    assert carry_at_rebaseline(prev_peak=270.0, equity=275.0) == 0.0


def test_disabled_flag_restores_old_forgive_behaviour():
    assert carry_at_rebaseline(275.16, 267.61, enabled=False) == 0.0
    dd, c = effective_dd(268.71, 268.54, 267.61, 7.55, enabled=False)
    assert c == 0.0 and round(dd, 2) == 0.17          # exactly the old (defective) reading


def test_source_carries_the_terms_and_ships_on():
    src = open(q.__file__, encoding="utf-8", errors="replace").read()
    assert 'DD_CARRY = _envb("KALSHI_DD_CARRY", True)' in src, "must ship ON — the old path is a hole"
    assert 'st["equity_day_carry"]' in src, "carry must persist across cycles"
    assert '_carry_eff = max(0.0, _carry - max(0.0, _equity - _start))' in src, "carry must decay"
    assert "_dd = _dd_raw + _carry_eff" in src, "the halt must test the CARRIED drawdown"
    i = src.index("_dd = _dd_raw + _carry_eff")
    assert "DD CARRY" in src[:i], "re-baseline must announce a carried drawdown out loud"


def test_halt_message_names_the_carry():
    src = open(q.__file__, encoding="utf-8", errors="replace").read()
    assert "CARRIED across the baseline" in src, \
        "a halt driven partly by carried drawdown must say so — misreading WHICH measure " \
        "halted the bot is this lane's most expensive diagnostic error"
