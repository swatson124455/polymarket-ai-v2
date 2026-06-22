"""Regression guard: exception logs must render PLAIN tracebacks, never rich.

Root cause (2026-06-10, EB scan-wedge): `structlog.dev.ConsoleRenderer()` with no
explicit `exception_formatter` auto-upgrades to rich's traceback renderer
(Panel + Syntax + pygments) when rich is importable. That rendering runs
synchronously on the event-loop thread at every `exc_info=True` log site; on a
large SQLAlchemy/asyncpg exception, pygments tokenization froze the esports
event loop for minutes (py-spy: MainThread pinned in
`pygments get_tokens_unprocessed` across two dumps 60s apart), wedging the scan
loop after cycle 1 and driving the scan-stall restart churn.

These tests render an exception through the EXACT renderer configured in
config/logging_setup.py and assert the output is a standard Python traceback
with none of rich's box-drawing/panel artifacts. If someone reverts the
`exception_formatter=plain_traceback` pin, this fails immediately (rich IS
installed in this environment, so the auto-upgrade would kick in).
"""
import structlog

import config.logging_setup as logging_setup


def _render_exception_line():
    """Run an exc_info event through the configured processor chain; return text."""
    logging_setup.configure_logging()
    try:
        processors = structlog.get_config()["processors"]
        renderer = processors[-1]
        assert isinstance(renderer, structlog.dev.ConsoleRenderer)
        try:
            raise ValueError("boom: SELECT 1 FROM giant_statement -- simulated")
        except ValueError:
            import sys
            event_dict = {
                "event": "Error monitoring positions: simulated",
                "level": "error",
                "timestamp": "2026-06-10T00:00:00Z",
                "exc_info": sys.exc_info(),
            }
            # format_exc_info is handled inside ConsoleRenderer for dev usage
            return renderer(None, "error", event_dict)
    finally:
        structlog.reset_defaults()


def test_exception_renders_plain_traceback_not_rich():
    out = _render_exception_line()
    assert "Traceback (most recent call last)" in out, (
        "expected a standard Python traceback in rendered output"
    )
    assert "ValueError" in out and "boom" in out


def test_exception_output_has_no_rich_panel_artifacts():
    out = _render_exception_line()
    # rich Traceback panels use box-drawing characters; plain_traceback never does.
    for artifact in ("╭", "╰", "│", "───"):  # ╭ ╰ │ ───
        assert artifact not in out, (
            f"rich box-drawing artifact {artifact!r} found — renderer regressed to "
            "rich traceback formatting (the event-loop-freezing path)"
        )


def test_renderer_pins_plain_traceback_formatter():
    """Belt-and-braces: the configured formatter IS structlog.dev.plain_traceback."""
    logging_setup.configure_logging()
    try:
        renderer = structlog.get_config()["processors"][-1]
        fmt = getattr(renderer, "_exception_formatter", None)
        assert fmt is structlog.dev.plain_traceback, (
            f"ConsoleRenderer exception_formatter is {fmt!r}, expected plain_traceback"
        )
    finally:
        structlog.reset_defaults()
