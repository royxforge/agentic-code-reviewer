"""Interactive terminal dashboard for the Agentic Code Reviewer.

A full-screen Textual TUI (``acr tui ...``) with:

- a launcher menu (``acr`` / ``acr <path>``)  -  review, providers & API keys,
  history, help; everything configurable from the CLI,
- a live pipeline screen (animated stage tracker, activity log, usage ticker),
- a findings browser (severity-coloured table + detail pane + markdown export),
- a benchmark screen (progress bar, per-run log, results table).

The workflow never imports this package: it emits :class:`WorkflowEvent`
objects through an optional event sink, and the TUI subscribes to that stream.
"""

from __future__ import annotations

from agentic_code_reviewer.ui.app import (
    BenchmarkApp,
    HomeApp,
    ReviewerApp,
    launch_benchmark,
    launch_home,
    launch_reviewer,
)

__all__ = [
    "BenchmarkApp",
    "HomeApp",
    "ReviewerApp",
    "launch_benchmark",
    "launch_home",
    "launch_reviewer",
]
