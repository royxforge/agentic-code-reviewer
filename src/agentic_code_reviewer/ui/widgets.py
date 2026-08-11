"""Reusable Textual widgets: branded header, phase-grouped stage tracker and a
chip-style live stats bar  -  plus a small action-dispatch helper."""

from __future__ import annotations

import asyncio
import inspect
import time

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Static

from agentic_code_reviewer.ui.theme import (
    BRAND_ACCENT,
    BRAND_ELEVATED,
    BRAND_SURFACE,
    BRAND_TEXT,
    BRAND_TEXT_DIM,
    PHASES,
    SPINNER_FRAMES,
    STAGE_LABEL,
    STAGE_ORDER,
)


def dispatch_action(app: App, action: str) -> None:
    """Run an app action, awaiting it when it is a coroutine.

    Textual's built-in actions (``action_quit``, …) are ``async def``  -  calling
    them from a sync handler without awaiting silently drops the action (the
    launcher's Quit menu item was a no-op for exactly this reason).
    ``asyncio.ensure_future`` keeps a strong reference until the task finishes
    and handles any awaitable, not just coroutines.
    """
    result = getattr(app, f"action_{action}")()
    if inspect.isawaitable(result):
        asyncio.ensure_future(result)


class AppHeader(Widget):
    """Branded top bar with a neo-glass cap: heavy electric-violet top line,
    a ◆ logo mark + wordmark, a right-aligned status chip and a subtitle line.

    Replaces Textual's built-in ``Header`` (which races ``HeaderTitle`` in
    headless ``run_test`` mode when screens switch fast).
    """

    # height 3 − heavy border 1 = 2 content rows (brand row + subtitle).
    DEFAULT_CSS = f"""
    # NOTE: Textual's box model counts borders inside ``height``  -  a height-3
    # bar with 2 borders leaves only 1 content row (the subtitle would clip).
    # Keep the glass cap (heavy top border) and drop the bottom border.
    AppHeader {{
        height: 3;
        background: {BRAND_SURFACE};
        padding: 0 1;
        border-top: heavy {BRAND_ACCENT};
    }}
    #header-top {{ height: 1; }}
    #header-title {{
        width: 1fr;
        text-style: bold;
        color: {BRAND_TEXT};
    }}
    #header-chip {{ width: auto; content-align: right middle; }}
    #header-subtitle {{ height: 1; color: {BRAND_TEXT_DIM}; }}
    """

    def __init__(self, title: str = "Agentic Code Reviewer", subtitle: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._title = title
        self._subtitle = subtitle

    def compose(self) -> ComposeResult:
        with Horizontal(id="header-top"):
            yield Static(
                f"[bold {BRAND_ACCENT}]◆[/]  [b]{self._title}[/]",
                id="header-title",
            )
            yield Static("", id="header-chip")
        yield Static(self._subtitle, id="header-subtitle")

    def set_subtitle(self, subtitle: str) -> None:
        self._subtitle = subtitle
        self.query_one("#header-subtitle", Static).update(subtitle)

    def set_status(self, label: str, color: str = BRAND_ACCENT) -> None:
        """Show a right-aligned status chip (e.g. ``● RUNNING`` / ``✔ DONE``)."""
        self.query_one("#header-chip", Static).update(
            f"[bold {color}]{label}[/]"
        )


class StageTracker(Widget):
    """Phase-grouped pipeline tracker with an overall progress line.

    Rows appear grouped under ``Planning / Analysis / Synthesis`` phase
    headers; stages that never start are marked ``skipped`` by :meth:`finalize`.
    A timer advances the spinner glyph of any ``running`` stage.
    """

    DEFAULT_CSS = f"""
    StageTracker {{
        height: auto;
        padding: 0 1;
    }}
    #tracker-progress {{
        height: 1;
        color: {BRAND_TEXT_DIM};
        margin-bottom: 1;
    }}
    .phase-header {{
        height: 1;
        text-style: bold;
        margin-top: 1;
    }}
    .stage-row {{ height: 1; }}
    .stage-glyph {{ width: 2; }}
    .stage-name {{ width: 24; color: {BRAND_TEXT}; }}
    .stage-status {{ width: 1fr; }}
    .stage-time {{ width: 8; text-align: right; color: {BRAND_TEXT_DIM}; }}
    .stage-row:focus {{ background: {BRAND_ELEVATED}; }}
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._status: dict[str, str] = {s: "pending" for s in STAGE_ORDER}
        self._times: dict[str, float] = {}
        self._frame = 0

    def compose(self) -> ComposeResult:
        yield Static("", id="tracker-progress")
        for phase_name, phase_color, stages in PHASES:
            yield Static(
                f"[bold {phase_color}]◆ {phase_name.upper()}[/]",
                classes="phase-header",
                id=f"phase-{phase_name.lower()}",
            )
            for stage in stages:
                yield Horizontal(
                    Static("○", classes="stage-glyph"),
                    Static(STAGE_LABEL.get(stage, stage.replace("_", " ")), classes="stage-name"),
                    Static("pending", classes="stage-status"),
                    Static("", classes="stage-time"),
                    id=f"row-{stage}",
                    classes="stage-row",
                )

    def _row(self, stage: str) -> Horizontal | None:
        return self.query_one(f"#row-{stage}", Horizontal)

    def set_status(self, stage: str, status: str, seconds: float | None = None) -> None:
        self._status[stage] = status
        if seconds is not None:
            self._times[stage] = seconds
        self._refresh_row(stage)
        self._refresh_progress()

    def _refresh_row(self, stage: str) -> None:
        row = self._row(stage)
        if row is None:
            return
        status = self._status.get(stage, "pending")
        glyph, style, status_label, time_label = self._stage_cells(stage, status)
        row.query_one(".stage-glyph", Static).update(glyph)
        row.query_one(".stage-status", Static).update(
            f"[{style}]{status_label}[/]"
        )
        row.query_one(".stage-time", Static).update(time_label)

    def _stage_cells(self, stage: str, status: str) -> tuple[str, str, str, str]:
        time_s = self._times.get(stage)
        time_label = f"{time_s:.1f}s" if time_s is not None else ""
        if status == "running":
            glyph = SPINNER_FRAMES[self._frame % len(SPINNER_FRAMES)]
            return glyph, BRAND_ACCENT, "running", time_label
        if status == "done":
            return "✓", "#34d399", "done", time_label
        if status == "failed":
            return "✗", "#fb7185", "failed", time_label
        if status == "skipped":
            return "-", "dim", "skipped", time_label
        return "○", "dim", "pending", time_label

    def _refresh_progress(self) -> None:
        """Progress line: done/total and how many stages are live right now."""
        done = sum(1 for s in self._status.values() if s == "done")
        total = len(self._status)
        running = sum(1 for s in self._status.values() if s == "running")
        tail = f" · {running} running" if running else ""
        self.query_one("#tracker-progress", Static).update(
            f"◈ stages [bold {BRAND_ACCENT}]{done}/{total}[/]{tail}"
        )

    def tick(self) -> None:
        """Advance the spinner; called by a screen interval while running."""
        self._frame += 1
        for stage, status in self._status.items():
            if status == "running":
                self._refresh_row(stage)

    def finalize(self) -> None:
        """Mark any never-started stage as skipped."""
        for stage, status in self._status.items():
            if status == "pending":
                self.set_status(stage, "skipped")


class StatsBar(Horizontal):
    """Live bottom bar: repository, model, tokens, cost, calls, elapsed."""

    DEFAULT_CSS = f"""
    StatsBar {{
        height: 1;
        background: {BRAND_SURFACE};
        padding: 0 1;
    }}
    StatsBar > Static {{ margin: 0 2; color: {BRAND_TEXT}; }}
    #stats-repo {{ color: {BRAND_ACCENT}; text-style: bold; }}
    .stats-dim {{ color: {BRAND_TEXT_DIM}; }}
    .stats-val {{ color: {BRAND_TEXT}; }}
    #stats-elapsed {{ width: 1fr; content-align: right middle; }}
    """

    def __init__(self, repository: str = "", model: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._repository = repository
        self._model = model
        self._tokens = 0
        self._cost = 0.0
        self._calls = 0
        self._started = time.monotonic()

    def compose(self) -> ComposeResult:
        yield Static(f"◆ {self._repository or 'review'}", id="stats-repo")
        yield Static("", id="stats-model", classes="stats-dim")
        yield Static("", id="stats-tokens", classes="stats-dim")
        yield Static("", id="stats-cost", classes="stats-dim")
        yield Static("", id="stats-calls", classes="stats-dim")
        yield Static("", id="stats-elapsed", classes="stats-dim")

    def set_usage(self, tokens: int, cost: float, calls: int) -> None:
        self._tokens = tokens
        self._cost = cost
        self._calls = calls
        self._redraw()

    def set_model(self, model: str) -> None:
        self._model = model
        self._redraw()

    def tick(self) -> None:
        self._redraw()

    def _redraw(self) -> None:
        elapsed = time.monotonic() - self._started
        val = BRAND_TEXT
        self.query_one("#stats-model", Static).update(
            f"[dim]model[/] [bold {val}]{self._model or '…'}[/]"
        )
        self.query_one("#stats-tokens", Static).update(
            f"[dim]tokens[/] [bold {val}]{self._tokens:,}[/]"
        )
        self.query_one("#stats-cost", Static).update(
            f"[dim]cost[/] [bold {val}]${self._cost:.4f}[/]"
        )
        self.query_one("#stats-calls", Static).update(
            f"[dim]calls[/] [bold {val}]{self._calls}[/]"
        )
        self.query_one("#stats-elapsed", Static).update(
            f"[dim]elapsed[/] [bold {val}]{elapsed:.0f}s[/]"
        )
