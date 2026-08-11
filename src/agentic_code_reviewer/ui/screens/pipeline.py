"""Live pipeline screen: hero strip, phase-grouped stage tracker, activity log
and a live severity counter."""

from __future__ import annotations

from datetime import datetime

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, RichLog, Static

from agentic_code_reviewer.models.findings import Severity
from agentic_code_reviewer.orchestration.events import WorkflowEvent
from agentic_code_reviewer.ui.theme import (
    BRAND_ACCENT,
    BRAND_ACCENT_2,
    BRAND_BORDER,
    BRAND_PANEL,
    BRAND_SURFACE,
    BRAND_TEXT,
    BRAND_TEXT_DIM,
    SEVERITY_COLOR,
    SEVERITY_PILL_BG,
    severity_pill,
)
from agentic_code_reviewer.ui.widgets import AppHeader, StageTracker, StatsBar


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


class PipelineScreen(Screen):
    """Shows the agentic workflow as it executes, stage by stage."""

    # ``app.``-prefixed: screen bindings resolve only on the screen in Textual 8.2.8.
    BINDINGS = [
        Binding("q", "app.quit", "Quit"),
        Binding("ctrl+p", "app.command_palette", "Command Palette"),
    ]

    DEFAULT_CSS = f"""
    #pipeline-left {{
        width: 50;
        border-right: solid {BRAND_BORDER};
    }}
    #pipeline-right {{ width: 1fr; }}
    #hero {{
        height: 4;
        padding: 0 1;
        background: {BRAND_SURFACE};
        border-bottom: solid {BRAND_BORDER};
    }}
    #hero-repo {{ text-style: bold; color: {BRAND_TEXT}; }}
    #hero-sub {{ height: 1; color: {BRAND_TEXT_DIM}; }}
    #severity-counts {{ height: 1; color: {BRAND_TEXT_DIM}; }}
    #activity-log {{ height: 1fr; padding: 0 1; }}
    .pane-title {{
        height: 1;
        background: {BRAND_PANEL};
        color: {BRAND_ACCENT_2};
        text-style: bold;
        padding: 0 1;
    }}
    """

    def __init__(
        self,
        repository: str,
        model: str = "",
        source: str = "",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._repository = repository
        self._model = model
        self._source = source
        self._severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}

    def compose(self) -> ComposeResult:
        yield AppHeader(
            title="Agentic Code Reviewer",
            subtitle=f"{self._repository} · {self._source or 'review'}",
        )
        # Note: Textual's box model counts the border inside ``height``, so a
        # ``height: N`` + ``border-bottom`` widget has N−1 content rows. The
        # hero declares 4 and carries exactly 3 rows below.
        with Vertical(id="hero"):
            yield Static(self._repository, id="hero-repo")
            yield Static(
                f"[dim]source[/] {self._source or 'review'}   "
                f"[dim]model[/] {self._model or '…'}",
                id="hero-sub",
            )
            yield Static("", id="severity-counts")
        with Horizontal():
            with VerticalScroll(id="pipeline-left"):
                yield Static("◆ PIPELINE", classes="pane-title")
                yield StageTracker(id="stage-tracker")
            with VerticalScroll(id="pipeline-right"):
                yield Static("◆ ACTIVITY", classes="pane-title")
                yield RichLog(id="activity-log", highlight=True, markup=True)
        yield StatsBar(repository=self._repository, model=self._model, id="stats-bar")
        # ``show_command_palette=False``: Textual's footer renders the palette
        # key a SECOND time (right side) when it is also bound  -  disable that.
        yield Footer(show_command_palette=False)

    def on_mount(self) -> None:
        self.set_interval(0.1, self._tick, pause=False)
        self.query_one(AppHeader).set_status("READY", BRAND_TEXT_DIM)

    def _tick(self) -> None:
        self.query_one("#stage-tracker", StageTracker).tick()
        self.query_one("#stats-bar", StatsBar).tick()

    # ------------------------------------------------------------------
    def handle_event(self, event: WorkflowEvent) -> None:
        """Called from the app thread for every workflow event."""
        # The background worker may emit events before this screen finishes
        # composing (e.g. a very fast mock run)  -  drop them rather than crash
        # the app loop with a NoMatches query.
        if not self.is_mounted:
            return
        log = self.query_one("#activity-log", RichLog)
        tracker = self.query_one("#stage-tracker", StageTracker)
        stats = self.query_one("#stats-bar", StatsBar)
        header = self.query_one(AppHeader)
        t = _ts()

        if event.kind == "stage_start":
            tracker.set_status(event.stage, "running")
            header.set_status("● RUNNING", BRAND_ACCENT)
            log.write(f"[dim]{t}[/dim] [bold {BRAND_ACCENT}]▶ {event.stage}[/]")
        elif event.kind == "stage_done":
            tracker.set_status(event.stage, "done", event.seconds)
            log.write(
                f"[dim]{t}[/dim] [bold #3fb950]✓ {event.stage}[/] "
                f"[dim]({event.seconds:.1f}s)[/dim]"
            )
        elif event.kind == "stage_failed":
            tracker.set_status(event.stage, "failed", event.seconds)
            header.set_status("✗ FAILED", "#f85149")
            log.write(
                f"[dim]{t}[/dim] [bold #f85149]✗ {event.stage}[/] "
                f"[dim]({event.seconds:.1f}s)[/dim] [dim]{event.message[:80]}[/dim]"
            )
        elif event.kind == "stage_skipped":
            tracker.set_status(event.stage, "skipped")
            log.write(
                f"[dim]{t}[/dim] [yellow]- {event.stage} skipped[/] "
                f"[dim]{event.message}[/dim]"
            )
        elif event.kind == "plan":
            summary = getattr(event.payload, "summary", "")[:120]
            log.write(f"[dim]{t}[/dim] [bold]plan[/] {summary}")
        elif event.kind == "context":
            count = event.payload or 0
            log.write(
                f"[dim]{t}[/dim] [bold {BRAND_ACCENT}]context[/] {count} chunk(s) selected"
            )
        elif event.kind == "decomposed":
            log.write(
                f"[dim]{t}[/dim] [bold #d29922]decomposed[/] {event.message}"
            )
        elif event.kind == "finding":
            finding = event.payload
            sev = finding.severity.value
            self._severity_counts[sev] = self._severity_counts.get(sev, 0) + 1
            self._render_severity_counts()
            color = SEVERITY_COLOR.get(finding.severity, "bold")
            loc = (
                f"{finding.file_path}:{finding.start_line}"
                if finding.start_line
                else finding.file_path
            )
            log.write(
                f"[dim]{t}[/dim] [{color}]{sev.upper()}[/] "
                f"[bold]{finding.title}[/] [dim]{loc}[/dim]"
            )
        elif event.kind == "usage":
            stats.set_usage(
                event.payload.get("total_tokens", 0),
                event.payload.get("cost_usd", 0.0),
                event.payload.get("calls", 0),
            )
        elif event.kind == "log":
            log.write(f"[dim]{t}[/dim] {event.message}")
        elif event.kind == "finish":
            result = event.payload
            stats.set_usage(
                sum(result.token_usage.values()),
                result.estimated_cost_usd,
                result.llm_call_count,
            )
            stats.set_model(result.review.model)
            tracker.finalize()
            header.set_status("✔ COMPLETE", "#3fb950")
            log.write(
                f"[dim]{t}[/dim] [bold #3fb950]✔ review complete  -  "
                f"{len(result.review.findings)} finding(s), "
                f"{result.llm_call_count} LLM call(s), "
                f"${result.estimated_cost_usd:.4f}[/]"
            )

    def _render_severity_counts(self) -> None:
        counts = self._severity_counts
        labels = [
            ("critical", Severity.CRITICAL),
            ("high", Severity.HIGH),
            ("medium", Severity.MEDIUM),
            ("low", Severity.LOW),
            ("info", Severity.INFO),
        ]
        pills = []
        for name, sev in labels:
            n = counts[name]
            if n == 0:
                pills.append(f"[dim]{name}[/] [bold {SEVERITY_PILL_BG[sev]}]{n}[/]")
            else:
                pills.append(severity_pill(sev.value, n))
        self.query_one("#severity-counts", Static).update("  ".join(pills))
