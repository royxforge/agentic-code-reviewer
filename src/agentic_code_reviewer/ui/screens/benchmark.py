"""Benchmark screen: header with system chips, live progress, per-run log and a
polished comparison table."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, ProgressBar, RichLog, Static

from agentic_code_reviewer.evaluation.metrics import SystemMetrics, to_table_row
from agentic_code_reviewer.ui.theme import (
    BRAND_ACCENT,
    BRAND_ACCENT_2,
    BRAND_BORDER,
    BRAND_PANEL,
    BRAND_SURFACE,
    BRAND_TEXT,
    BRAND_TEXT_DIM,
)
from agentic_code_reviewer.ui.widgets import AppHeader


class BenchmarkScreen(Screen):
    """Runs all systems over a dataset with a live progress bar."""

    # ``app.``-prefixed: screen bindings resolve only on the screen in Textual 8.2.8.
    BINDINGS = [
        Binding("q", "app.quit", "Quit"),
        Binding("ctrl+p", "app.command_palette", "Command Palette"),
    ]

    DEFAULT_CSS = f"""
    #bench-header {{
        height: 3;
        padding: 0 1;
        background: {BRAND_SURFACE};
        border-bottom: solid {BRAND_BORDER};
    }}
    #bench-title {{ height: 1; text-style: bold; color: {BRAND_TEXT}; }}
    #bench-sub {{ height: 1; color: {BRAND_TEXT_DIM}; }}
    #bench-progress-wrap {{ height: 3; padding: 0 1; align: center middle; }}
    #bench-bar {{ width: 1fr; }}
    #bench-pct {{ width: 7; content-align: right middle; color: {BRAND_ACCENT}; }}
    #bench-current {{ height: 1; padding: 0 1; color: {BRAND_TEXT_DIM}; }}
    #bench-log {{ height: 1fr; padding: 0 1; }}
    #bench-results {{ height: 1fr; }}
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
        dataset: Path,
        systems: list[str],
        total_runs: int = 0,
        entries: int = 0,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.dataset = dataset
        self.systems = systems
        self.total_runs = total_runs
        self.entries = entries

    def compose(self) -> ComposeResult:
        yield AppHeader(
            title="Agentic Code Reviewer  -  Benchmark",
            subtitle=f"{self.dataset}",
        )
        yield Static("", id="bench-header")
        with Horizontal(id="bench-progress-wrap"):
            yield ProgressBar(
                total=self.total_runs, show_percentage=False, show_eta=False, id="bench-bar"
            )
            yield Static("0%", id="bench-pct")
        yield Static("", id="bench-current")
        with VerticalScroll():
            yield Static("◆ RUN LOG", classes="pane-title")
            yield RichLog(id="bench-log", highlight=True, markup=True)
        yield Static("◆ RESULTS", classes="pane-title")
        yield DataTable(id="bench-results", cursor_type="row", zebra_stripes=True)
        # ``show_command_palette=False``: Textual's footer renders the palette
        # key a SECOND time (right side) when it is also bound  -  disable that.
        yield Footer(show_command_palette=False)

    # ------------------------------------------------------------------
    def on_mount(self) -> None:
        self._render_header()

    def _render_header(self) -> None:
        chips = "  ".join(
            f"[b {BRAND_ACCENT}]{s}[/]" if i == 0 else f"[b {BRAND_ACCENT_2}]{s}[/]"
            for i, s in enumerate(self.systems)
        )
        self.query_one("#bench-header", Static).update(
            f"BENCHMARK  -  {self.dataset.name}\n"
            f"[dim]systems[/] {chips}   "
            f"[dim]entries[/] {self.entries or '…'}   "
            f"[dim]runs[/] {self.total_runs or '…'}"
        )

    def configure(self, total_runs: int, entries: int) -> None:
        """Set the progress total once the dataset has loaded."""
        self.total_runs = total_runs
        self.entries = entries
        self.query_one("#bench-bar", ProgressBar).update(total=total_runs)
        self._render_header()

    def handle_progress(
        self, entry_id: str, system: str, done: int, total: int
    ) -> None:
        """Called from the app thread for every completed (entry, system) run."""
        bar = self.query_one("#bench-bar", ProgressBar)
        bar.progress = done
        pct = int((done / total) * 100) if total else 0
        self.query_one("#bench-pct", Static).update(f"{pct}%")
        self.query_one("#bench-current", Static).update(
            f" [dim]{entry_id}[/] · [bold {BRAND_ACCENT}]{system}[/]  -  "
            f"{done}/{total} runs complete"
        )
        self.query_one("#bench-log", RichLog).write(
            f"[dim]✓[/] [bold #3fb950]{system}[/] [dim]{entry_id}[/]"
        )

    def show_results(self, metrics_by_system: dict[str, SystemMetrics]) -> None:
        """Populate the final comparison table."""
        table = self.query_one("#bench-results", DataTable)
        table.add_columns(
            "System", "Precision", "Recall", "F1", "FPR", "Detect",
            "Severity", "Complete", "Latency", "Cost",
        )
        colors = [BRAND_ACCENT, BRAND_ACCENT_2, "#58a6ff", "#3fb950"]
        for idx, (system, metrics) in enumerate(metrics_by_system.items()):
            row = to_table_row(metrics)
            table.add_row(
                Text(system, style=f"bold {colors[idx % len(colors)]}"),
                Text(_fmt(row["precision"])),
                Text(_fmt(row["recall"])),
                Text(_fmt(row["f1"])),
                Text(_fmt(row["false_positive_rate"])),
                Text(_fmt(row["bug_detection_rate"])),
                Text(_fmt(row["severity_accuracy"])),
                Text(_fmt(row["completion_rate"])),
                Text(f"{row['mean_latency_s']:.1f}s"),
                Text(f"${row['mean_cost_usd']:.4f}"),
                key=system,
            )
        self.query_one("#bench-log", RichLog).write(
            f"[bold #3fb950]✔ benchmark complete  -  {len(metrics_by_system)} system(s)[/]"
        )
        table.focus()

    def set_error(self, message: str) -> None:
        self.query_one("#bench-log", RichLog).write(
            f"[bold #f85149]✗ {message}[/]"
        )


def _fmt(value: float | None) -> str:
    return " - " if value is None else f"{value:.3f}"
