"""Textual applications for the interactive dashboard.

Two entry points:

* ``acr tui review-local …`` / ``acr tui review …``  -  :class:`ReviewerApp`
  runs one review inside a live pipeline dashboard.
* ``acr`` / ``acr <path>``  -  :class:`HomeApp` opens the launcher menu
  (Claude-Code style) from which the user can start a review, configure
  providers / API keys, browse history or read help.
* ``acr tui benchmark …``  -  :class:`BenchmarkApp` runs systems over a dataset.

All apps run their workload in a background thread and stream progress into
the UI through ``call_from_thread``  -  the workflow/baselines never touch the
UI directly. Errors in the background thread are surfaced as a screen message
rather than crashing the terminal.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Static

from agentic_code_reviewer.cli.exporters import review_to_json, review_to_sarif
from agentic_code_reviewer.config.runtime_config import (
    RuntimeConfig,
    apply_runtime_config,
    config_dir,
    is_first_run,
)
from agentic_code_reviewer.config.settings import Settings, get_settings
from agentic_code_reviewer.errors import NotAGitRepositoryError, ReviewerError
from agentic_code_reviewer.evaluation.benchmark import BenchmarkEntry, BenchmarkLoader
from agentic_code_reviewer.evaluation.experiments import ExperimentTracker
from agentic_code_reviewer.evaluation.metrics import SystemMetrics
from agentic_code_reviewer.evaluation.runner import run_benchmark
from agentic_code_reviewer.llm.client import BaseLLMClient
from agentic_code_reviewer.local.git import local_request_with_fallback
from agentic_code_reviewer.models.schemas import WorkflowResult
from agentic_code_reviewer.orchestration.events import EventSink, WorkflowEvent
from agentic_code_reviewer.orchestration.state import ReviewRequest
from agentic_code_reviewer.orchestration.workflow import Workflow
from agentic_code_reviewer.ui.screens.benchmark import BenchmarkScreen
from agentic_code_reviewer.ui.screens.help import HelpScreen
from agentic_code_reviewer.ui.screens.history import HistoryScreen
from agentic_code_reviewer.ui.screens.home import (
    HomeScreen,
    PathPromptScreen,
    WelcomeScreen,
)
from agentic_code_reviewer.ui.screens.pipeline import PipelineScreen
from agentic_code_reviewer.ui.screens.providers import ProvidersScreen
from agentic_code_reviewer.ui.screens.results import ResultsScreen
from agentic_code_reviewer.ui.screens.splash import SplashScreen
from agentic_code_reviewer.ui.widgets import AppHeader, dispatch_action

# Shared design-system: brand palette mapped onto Textual's theme variables
# plus global styling for the built-in widgets (Footer, tables, inputs, bars).
_THEME_CSS = """
$background: #0a0a12;
$surface: #12121e;
$panel: #1a1a2a;
$elevated: #24243c;
$primary: #a78bfa;
$secondary: #c4b5fd;
$accent: #a78bfa;
# NOTE: `$foreground` is deliberately NOT overridden  -  Textual 8.2.8's own
# Markdown table CSS (`keyline: thin $foreground 20%`) fails to parse when the
# variable is redefined at app scope, crashing any Markdown that contains a
# table. Textual's default foreground is visually identical to #eceef6.
$text: #e8e9f3;
$text-muted: #9d9fb4;
$success: #34d399;
$warning: #fbbf24;
$error: #fb7185;
$border: #2b2b45;

Screen { background: $background; }
Footer { background: $panel; color: $text-muted; }

/* Tables: hairline frame, violet header band, quiet cursor. */
DataTable { background: $background; border: solid $border; }
DataTable > .datatable--header {
    background: $panel;
    color: $primary;
    text-style: bold;
}
DataTable > .datatable--cursor { background: $elevated; }
DataTable:focus { border: solid $primary; }

/* Logs / markdown: soft elevated panes. Textual 8.2.8 rejects type-selector
   descendants (e.g. ``Markdown h1``) and Markdown is a single widget, so
   headings/code inherit Textual's default violet-tinted theme. */
RichLog { background: $surface; border: solid $border; }
Markdown { background: $background; }

/* Inputs: glass fields with violet focus. */
Input {
    background: $surface;
    border: solid $border;
    color: $text;
}
Input:focus { border: solid $primary; }

/* Buttons: primary = electric violet with dark ink. */
Button {
    background: $panel;
    color: $text;
    border: solid $border;
}
Button.primary { background: $primary; color: #0a0a12; text-style: bold; }
Button:focus { border: solid $primary; }

/* Bars. */
ProgressBar { background: $surface; }
ProgressBar > .bar--bar { background: $primary; }
"""

_APP_CSS = """
#fatal-pane { align: center middle; height: 100%; }
#fatal-box {
    width: 74;
    height: auto;
    padding: 1 2;
    border: solid $border;
    border-top: heavy $error;
    background: $surface;
}
#fatal-title { text-style: bold; color: $error; }
#fatal-detail { color: $text-muted; }
#fatal-hint { color: $text-disabled; }
"""


class _FatalScreen(Screen):
    """Shown when the background workload fails before producing a review."""

    BINDINGS = [Binding("q", "app.quit", "Quit")]

    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.message = message

    def compose(self) -> ComposeResult:
        yield AppHeader(
            title="Agentic Code Reviewer", subtitle="fatal error  -  the review could not be produced"
        )
        with Vertical(id="fatal-pane"):
            with Vertical(id="fatal-box"):
                yield Static("✗  REVIEW FAILED", id="fatal-title")
                yield Static(self.message, id="fatal-detail")
                yield Static("press q to quit", id="fatal-hint")
        # ``show_command_palette=False``: Textual's footer renders the palette
        # key a SECOND time (right side) when it is also bound  -  disable that.
        yield Footer(show_command_palette=False)


# (text, action, help)  -  shown only when the app defines that action, so the
# palette adapts per context (launcher gets navigation, results get export…).
_PALETTE_COMMANDS = [
    ("Review this directory", "review_cwd", "Start a review of the current project"),
    ("Review another path…", "open_path_prompt", "Pick a git project to review"),
    ("Providers & API keys", "open_providers", "Configure credentials  -  stored in your user config"),
    ("Review history", "open_history", "Browse recent local reviews"),
    ("Help & shortcuts", "open_help", "In-app reference"),
    ("Return to launcher", "go_home", "Back to the main menu"),
    ("Export review as Markdown", "export", "Write the review to review.md"),
    ("Export review as JSON", "export_json", "Write the review to review.json"),
    ("Export review as SARIF", "export_sarif", "Write the review to review.sarif"),
    ("Clear review history", "clear_history", "Wipe all recorded reviews from local history"),
    ("Open config folder", "open_config", "Reveal the acr config directory in your file manager"),
    ("Quit", "quit", "Exit acr"),
]


class _ReviewCommands(Provider):
    """Command palette entries (Textual 8 ``search``/``discover`` API)."""

    async def discover(self) -> Hits:
        """Commands offered when the palette is opened with an empty query.

        Textual routes an empty query to ``discover()`` (not ``search()``)  -
        without this, the palette opens blank.
        """
        for text, action, help_text in _PALETTE_COMMANDS:
            if self._available(action):
                yield DiscoveryHit(
                    display=text,
                    command=self._call(action),
                    text=text,
                    help=help_text,
                )

    async def search(self, query: str) -> Hits:
        # Textual 8.2.8's fuzzy matcher raises on an empty query, so never
        # hand it an empty string (the palette calls ``discover`` for that).
        if not query.strip():
            async for hit in self.discover():
                yield hit
            return
        matcher = self.matcher(query)
        for text, action, help_text in _PALETTE_COMMANDS:
            if not self._available(action):
                continue
            score = matcher.match(text)
            if score <= 0.0:
                continue
            yield Hit(
                score=score,
                match_display=text,
                command=self._call(action),
                text=text,
                help=help_text,
            )

    def _available(self, action: str) -> bool:
        """Only offer commands the current app can actually run."""
        if action == "go_home":
            # ``ReviewerApp`` defines ``action_go_home`` only so the Results
            # ``h`` binding doesn't crash  -  it doesn't navigate back, so the
            # launcher-only command must not be offered there.
            return type(self.app).__name__ == "HomeApp"
        return hasattr(self.app, f"action_{action}")

    def _call(self, action: str):
        def _run() -> None:
            dispatch_action(self.app, action)

        return _run


class ReviewFlowMixin(App):
    """Runs one review: pipeline screen, background worker, results screen.

    Shared by :class:`ReviewerApp` (direct ``acr tui review …``) and
    :class:`HomeApp` (launcher → pick a review). On finish the result is
    recorded to the local history and the results screen is shown.
    """

    # Attribute declarations so mypy knows these exist before start_review.
    _result: WorkflowResult | None = None
    _pipeline: PipelineScreen | None = None
    _review_request: ReviewRequest
    _review_settings: Settings
    _review_export: Path | None
    _review_client: BaseLLMClient | None

    def start_review(
        self,
        request: ReviewRequest,
        settings: Settings,
        export_path: Path | None = None,
        client: BaseLLMClient | None = None,
        startup_notice: str | None = None,
    ) -> None:
        self._review_request = request
        self._review_settings = settings
        self._review_export = export_path
        self._review_client = client
        self._result = None
        source = {
            "github_pr": "GitHub PR",
            "github_commit": "GitHub commit",
            "local": "local git",
            "benchmark": "benchmark",
        }.get(request.source, "review")
        self._pipeline = PipelineScreen(repository=request.repository, source=source)
        self.push_screen(self._pipeline)
        if startup_notice:
            self.notify(startup_notice, title="Review scope", severity="information")
        self.run_worker(self._run_review, thread=True)

    def _sink(self) -> EventSink:
        """Event sink called from the workflow thread  -  forward to the UI thread."""
        return self._on_workflow_event_safe

    def _on_workflow_event_safe(self, event: WorkflowEvent) -> None:
        try:
            self.call_from_thread(self._on_workflow_event, event)
        except Exception:  # noqa: BLE001 - app may be tearing down
            pass

    def _on_workflow_event(self, event: WorkflowEvent) -> None:
        if self._pipeline is not None:
            self._pipeline.handle_event(event)
        if event.kind == "finish" and event.payload is not None:
            self._result = event.payload
            self._pipeline = None
            RuntimeConfig.load().record_review_result(event.payload)
            self.switch_screen(ResultsScreen(event.payload, self._review_export))

    def _run_review(self) -> None:
        """Background thread: run the full workflow, streaming events."""
        try:
            workflow = Workflow(
                self._review_settings,
                client=self._review_client,
                event_sink=self._sink(),
            )
            workflow.run(self._review_request)
        except ReviewerError as exc:
            self._fail(str(exc))
        except Exception as exc:  # noqa: BLE001 - surface, never crash the TUI
            self._fail(f"{type(exc).__name__}: {exc}")

    def _fail(self, message: str) -> None:
        try:
            self.call_from_thread(self._show_fatal, message)
        except Exception:  # noqa: BLE001
            pass

    def _show_fatal(self, message: str) -> None:
        self.switch_screen(_FatalScreen(message))

    # -- actions ----------------------------------------------------------
    def _export_path(self, suffix: str) -> Path:
        """Where an export lands: next to the CLI ``--output`` file when set."""
        if self._review_export is not None:
            return self._review_export.with_suffix(suffix)
        return Path(f"review{suffix}")

    def _export_file(self, body: str, suffix: str) -> None:
        """Write an export body to disk and confirm."""
        path = self._export_path(suffix)
        path.write_text(body, encoding="utf-8")
        self.notify(f"Review exported to {path}", title="Export", severity="information")

    def action_export(self) -> None:
        if self._result is not None:
            self._export_file(self._result.review.to_markdown(), ".md")
        else:
            self.notify("No finished review to export yet", severity="warning")

    def action_export_json(self) -> None:
        if self._result is not None:
            self._export_file(review_to_json(self._result), ".json")
        else:
            self.notify("No finished review to export yet", severity="warning")

    def action_export_sarif(self) -> None:
        if self._result is not None:
            self._export_file(review_to_sarif(self._result), ".sarif")
        else:
            self.notify("No finished review to export yet", severity="warning")

    def action_clear_history(self) -> None:
        """Wipe the local review history  -  guarded by a confirmation prompt."""
        from agentic_code_reviewer.ui.screens.confirm import ConfirmScreen

        # Never stack a second prompt on an open one (double palette trigger).
        if isinstance(self.screen, ConfirmScreen):
            return
        count = len(RuntimeConfig.load().load_history())
        if count == 0:
            self.notify("History is already empty", severity="information")
            return

        noun = "review" if count == 1 else "reviews"
        self.push_screen(
            ConfirmScreen(
                title="Clear review history?",
                message=(
                    f"This permanently deletes {count} recorded {noun} from your "
                    "user config. This cannot be undone."
                ),
                confirm_label="Clear history",
                on_confirm=self._clear_history_now,
            )
        )

    def _clear_history_now(self) -> None:
        """The actual wipe, run only after the user confirmed."""
        removed = RuntimeConfig.load().clear_history()
        noun = "review" if removed == 1 else "reviews"
        self.notify(
            f"Cleared {removed} {noun} from history",
            title="History",
            severity="warning",
        )

    def action_open_config(self) -> None:
        """Reveal the acr config folder in the OS file manager."""
        folder = config_dir()
        try:
            if os.name == "nt":
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError:
            self.notify(f"Config folder: {folder}", severity="information")
            return
        self.notify(
            f"Opened config folder: {folder}", title="Config", severity="information"
        )


class ReviewerApp(ReviewFlowMixin):
    """Runs one review inside a live pipeline dashboard (``acr tui …``)."""

    TITLE = "Agentic Code Reviewer"
    SUB_TITLE = "planner → agents → verifier → aggregator"
    COMMANDS = {_ReviewCommands}
    BINDINGS = [Binding("ctrl+p", "command_palette", "Command Palette")]
    CSS = _THEME_CSS + _APP_CSS

    def __init__(
        self,
        request: ReviewRequest,
        settings: Settings,
        export_path: Path | None = None,
        client: BaseLLMClient | None = None,
        startup_notice: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._review_request = request
        self._review_settings = settings
        self._review_export = export_path
        self._review_client = client
        self.startup_notice = startup_notice
        self._result = None
        self._pipeline: PipelineScreen | None = None

    def on_mount(self) -> None:
        self.start_review(
            self._review_request,
            self._review_settings,
            export_path=self._review_export,
            client=self._review_client,
            startup_notice=self.startup_notice,
        )

    def action_go_home(self) -> None:
        self.notify("Run `acr` to open the launcher menu", severity="information")


class HomeApp(ReviewFlowMixin):
    """Claude-Code-style launcher: menu → review / providers / history / help.

    Bare ``acr`` (or ``acr <path>``) opens this app. ``<path>`` becomes the
    default review target. Providers / API keys are configured from the
    Providers screen and persisted to the user config.
    """

    TITLE = "Agentic Code Reviewer"
    SUB_TITLE = "launcher"
    COMMANDS = {_ReviewCommands}
    BINDINGS = [Binding("ctrl+p", "command_palette", "Command Palette")]
    CSS = _THEME_CSS + _APP_CSS

    def __init__(
        self,
        path: str = ".",
        settings: Settings | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.initial_path = str(path)
        self.settings = settings or apply_runtime_config(get_settings())
        self._result = None
        self._pipeline: PipelineScreen | None = None

    def refresh_settings(self) -> None:
        """Re-merge the runtime config after the user changed providers."""
        self.settings = apply_runtime_config(get_settings())

    def on_mount(self) -> None:
        if self.is_headless:
            # Headless runs (tests, screenshot capture) skip the boot splash
            # so programmatic drivers see the launcher immediately.
            self._show_launcher()
            return
        # Real terminal: brand splash first; it calls back when dismissed
        # (auto-timer or any key), then the launcher dashboard appears.
        self.push_screen(SplashScreen(on_dismiss=self._show_launcher))

    def _show_launcher(self) -> None:
        """Push the launcher dashboard (plus onboarding on first run)."""
        self.push_screen(HomeScreen(self.initial_path))
        # First-run onboarding: no stored provider and nothing set via the
        # environment  -  guide the user into provider setup before the
        # dashboard. Never reappears once any provider is configured.
        if is_first_run(self.settings):
            self.push_screen(WelcomeScreen())

    # -- review actions ----------------------------------------------------
    def action_review_cwd(self) -> None:
        self._begin_local_review(self.initial_path)

    def action_review_path(self, path: str) -> None:
        self._begin_local_review(path)

    def _begin_local_review(self, path: str) -> None:
        try:
            request, notice = local_request_with_fallback(path, base=None)
        except NotAGitRepositoryError as exc:
            # The shortcut "just did nothing" for the user here: the directory
            # is not a git repo, so bounce them into the path picker instead.
            self.notify(
                f"{exc.message}  -  pick a git project below",
                title="Cannot review",
                severity="warning",
            )
            self.action_open_path_prompt()
            return
        except ReviewerError as exc:
            self.notify(str(exc), title="Cannot review", severity="error")
            return
        self.start_review(
            request,
            self.settings,
            startup_notice=notice or None,
        )

    # -- navigation --------------------------------------------------------
    def action_open_path_prompt(self) -> None:
        self.push_screen(PathPromptScreen())

    def action_open_providers(self) -> None:
        self.push_screen(ProvidersScreen(self.settings))

    def action_open_history(self) -> None:
        self.push_screen(HistoryScreen())

    def action_open_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_go_home(self) -> None:
        """Return to the launcher (from the results screen of a review)."""
        self.pop_screen()


class BenchmarkApp(App):
    """Runs all systems over a dataset with a live progress bar."""

    TITLE = "Agentic Code Reviewer  -  Benchmark"
    SUB_TITLE = "system comparison over a historical-bug dataset"
    COMMANDS = {_ReviewCommands}
    BINDINGS = [Binding("ctrl+p", "command_palette", "Command Palette")]
    CSS = _THEME_CSS + _APP_CSS

    def __init__(
        self,
        dataset: Path,
        systems: list[str],
        settings: Settings,
        limit: int | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.dataset = dataset
        self.systems = systems
        self.settings = settings
        self.limit = limit
        self._screen: BenchmarkScreen | None = None

    def on_mount(self) -> None:
        # Screen must exist before we can surface load errors on it.
        self._screen = BenchmarkScreen(
            dataset=self.dataset,
            systems=self.systems,
            total_runs=0,
            entries=0,
        )
        self.push_screen(self._screen)
        self.run_worker(self._load_and_run, thread=True)

    def _load_and_run(self) -> None:
        try:
            entries = BenchmarkLoader.load(self.dataset)
        except ReviewerError as exc:
            self._fail(str(exc))
            return
        total_runs = len(entries) * len(self.systems)
        if self._screen is not None:
            self.call_from_thread(
                self._screen.configure, total_runs, len(entries)
            )
        self._run(entries)

    def _run(self, entries: list[BenchmarkEntry]) -> None:
        if not entries:
            return
        try:
            tracker = ExperimentTracker("tui-benchmark", root="benchmarks/results")
            tracker.save_config(
                {"dataset": str(self.dataset), "systems": self.systems, "limit": self.limit}
            )
            metrics = run_benchmark(
                entries,
                self.systems,
                self.settings,
                tracker,
                limit=self.limit,
                on_progress=self._on_progress_safe,
            )
            self.call_from_thread(self._show_results, metrics)
        except ReviewerError as exc:
            self._fail(str(exc))
        except Exception as exc:  # noqa: BLE001
            self._fail(f"{type(exc).__name__}: {exc}")

    def _on_progress_safe(
        self, entry_id: str, system: str, done: int, total: int
    ) -> None:
        try:
            self.call_from_thread(
                self._on_progress, entry_id, system, done, total
            )
        except Exception:  # noqa: BLE001
            pass

    def _on_progress(self, entry_id: str, system: str, done: int, total: int) -> None:
        if self._screen is not None:
            self._screen.handle_progress(entry_id, system, done, total)

    def _show_results(self, metrics: dict[str, SystemMetrics]) -> None:
        if self._screen is not None:
            self._screen.show_results(metrics)

    def _fail(self, message: str) -> None:
        try:
            self.call_from_thread(self._show_fatal, message)
        except Exception:  # noqa: BLE001
            pass

    def _show_fatal(self, message: str) -> None:
        if self._screen is not None:
            self._screen.set_error(message)

    def action_export(self) -> None:
        self.notify("Benchmark results are saved in benchmarks/results/", severity="information")


# ---------------------------------------------------------------------------
def launch_reviewer(
    settings: Settings,
    request: ReviewRequest,
    export_path: Path | None = None,
    client: BaseLLMClient | None = None,
    startup_notice: str | None = None,
) -> None:
    """Run the interactive review dashboard (blocks until the TUI exits)."""
    app = ReviewerApp(
        request,
        settings,
        export_path=export_path,
        client=client,
        startup_notice=startup_notice,
    )
    app.run()


def launch_home(path: str = ".", settings: Settings | None = None) -> None:
    """Run the launcher app (blocks until the TUI exits)."""
    app = HomeApp(path, settings)
    app.run()


def launch_benchmark(
    settings: Settings,
    dataset: Path,
    systems: list[str],
    limit: int | None = None,
) -> None:
    """Run the interactive benchmark dashboard (blocks until the TUI exits)."""
    app = BenchmarkApp(dataset, systems, settings, limit=limit)
    app.run()
