"""Render the TUI screens to SVG files for documentation.

Run from the project root:

    .venv/Scripts/python scripts/screenshot_tui.py

Outputs docs/screenshots/{home,providers,history,help,pipeline,results,
categories,benchmark}.svg  -  real screenshots captured headlessly from the
actual Textual widgets (mock / AdaptiveMock providers, no network required).
The user config dir is isolated to a temp folder so nothing touches the
user's real providers or history.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, ".")

from agentic_code_reviewer.config.settings import Settings  # noqa: E402
from agentic_code_reviewer.orchestration.state import ReviewRequest  # noqa: E402
from agentic_code_reviewer.ui.app import BenchmarkApp, HomeApp, ReviewerApp  # noqa: E402
from agentic_code_reviewer.ui.screens.benchmark import BenchmarkScreen  # noqa: E402
from agentic_code_reviewer.ui.screens.help import HelpScreen  # noqa: E402
from agentic_code_reviewer.ui.screens.history import HistoryScreen  # noqa: E402
from agentic_code_reviewer.ui.screens.pipeline import PipelineScreen  # noqa: E402
from agentic_code_reviewer.ui.screens.providers import ProvidersScreen  # noqa: E402
from agentic_code_reviewer.ui.screens.results import ResultsScreen  # noqa: E402
from tests.integration.helpers import AdaptiveMock  # noqa: E402

OUT = Path("docs/screenshots")
OUT.mkdir(parents=True, exist_ok=True)


class _HostApp(HomeApp):
    """HomeApp without the automatic home-screen push (host only)."""

    def on_mount(self) -> None:
        pass


# ----------------------------------------------------------------
def _isolate_runtime_config() -> Path:
    """Point the runtime config at a throwaway dir (never touch real data)."""
    import agentic_code_reviewer.config.runtime_config as rc

    tmp = Path(tempfile.mkdtemp(prefix="acr-shots-"))
    rc.config_dir = lambda: tmp
    return tmp


def _seed_runtime(tmp: Path) -> None:
    """Fake provider + history so the launcher screens look alive."""
    from agentic_code_reviewer.config.runtime_config import RuntimeConfig

    cfg = RuntimeConfig.load()
    cfg.set_provider("openai", api_key="sk-demo-9f8e7d6c", model="gpt-4o-mini")
    cfg.save()
    for repo, high, med in (
        ("shop/api", 2, 3),
        ("analytics-pipeline", 1, 2),
        ("rag-evaluation-framework", 3, 4),
        ("portfolio", 0, 1),
    ):
        cfg.record_review(
            {
                "repository": repo,
                "source": "local",
                "model": "gpt-4o-mini",
                "llm_calls": 14,
                "high": high,
                "medium": med,
                "low": 2,
                "info": 1,
                "findings": high + med + 3,
            }
        )

# The mock security finding (tests/integration/helpers.py) points at db.py
# line 11  -  so this diff must make line 11 of the NEW file an added line, or
# the aggregator's line-discipline rule drops it.
SQL_DIFF = """diff --git a/db.py b/db.py
--- a/db.py
+++ b/db.py
@@ -8,5 +8,5 @@ def search_users(query):
     if query is None:
         return []
     sql = "SELECT * FROM users WHERE name = '%s'" % query
-    return _db.execute(sql)
+    sql = f"SELECT * FROM users WHERE name LIKE '%{query}%'"
+    return _db.execute(sql)
"""

SAMPLE_FILES = {
    "db.py": (
        "def search_users(query):\n"
        "    if query is None:\n"
        "        return []\n"
        "    sql = \"SELECT * FROM users WHERE name = '%s'\" % query\n"
        "    sql = f\"SELECT * FROM users WHERE name LIKE '%{query}%'\"\n"
        "    return _db.execute(sql)\n"
    ),
    "app.py": "def main():\n    name = input()\n    print(search_users(name))\n",
}

W = 118
H = 38


def capture_home() -> None:
    """Launcher menu with a ready provider banner."""
    os.environ["OPENAI_API_KEY"] = "sk-demo-9f8e7d6c"  # in-process only
    settings = Settings(LLM_PROVIDER="openai")
    app = HomeApp(path="./portfolio", settings=settings)

    async def _run() -> None:
        async with app.run_test(size=(W, H)) as pilot:
            await pilot.pause()
            await pilot.pause()
            svg = app.export_screenshot(title="Agentic Code Reviewer  -  launcher")
            (OUT / "home.svg").write_text(svg, encoding="utf-8")
            print("wrote", OUT / "home.svg")

    asyncio.run(_run())


def capture_providers() -> None:
    """Providers screen with the active (stored) provider selected."""
    os.environ["OPENAI_API_KEY"] = "sk-demo-9f8e7d6c"
    settings = Settings(LLM_PROVIDER="openai")
    app = _HostApp(path=".", settings=settings)

    async def _run() -> None:
        async with app.run_test(size=(W, H)) as pilot:
            app.push_screen(ProvidersScreen(settings))
            await pilot.pause()
            await pilot.pause()
            svg = app.export_screenshot(title="Agentic Code Reviewer  -  providers")
            (OUT / "providers.svg").write_text(svg, encoding="utf-8")
            print("wrote", OUT / "providers.svg")

    asyncio.run(_run())


def capture_history() -> None:
    """History screen with seeded local reviews."""
    app = _HostApp(path=".", settings=Settings(LLM_PROVIDER="mock"))

    async def _run() -> None:
        async with app.run_test(size=(W, H)) as pilot:
            app.push_screen(HistoryScreen())
            await pilot.pause()
            await pilot.pause()
            table = app.screen.query_one("#history-table")
            if table.row_count:
                table.focus()
            svg = app.export_screenshot(title="Agentic Code Reviewer  -  history")
            (OUT / "history.svg").write_text(svg, encoding="utf-8")
            print("wrote", OUT / "history.svg")

    asyncio.run(_run())


def capture_help() -> None:
    """Help reference screen."""
    app = _HostApp(path=".", settings=Settings(LLM_PROVIDER="mock"))

    async def _run() -> None:
        async with app.run_test(size=(W, H)) as pilot:
            app.push_screen(HelpScreen())
            await pilot.pause()
            await pilot.pause()
            svg = app.export_screenshot(title="Agentic Code Reviewer  -  help")
            (OUT / "help.svg").write_text(svg, encoding="utf-8")
            print("wrote", OUT / "help.svg")

    asyncio.run(_run())


def capture_pipeline() -> None:
    """Pipeline screen mid-run: the real ReviewerApp + a slowed-down mock so a
    genuine mid-flight state (stages done, findings streaming, usage ticking)
    can be captured."""

    from agentic_code_reviewer.ui.widgets import StageTracker


    class SlowMock(AdaptiveMock):
        def complete(self, messages, **kwargs):
            time.sleep(0.4)  # keep the workflow in-flight long enough to capture
            return super().complete(messages, **kwargs)

    request = ReviewRequest(
        repository="o/r",
        diff_text=SQL_DIFF,
        changed_files=["db.py", "app.py"],
        repo_files=SAMPLE_FILES,
        source="local",
    )
    settings = Settings(LLM_PROVIDER="mock")
    app = ReviewerApp(request, settings, client=SlowMock())

    async def _run() -> None:
        async with app.run_test(size=(W, H)) as pilot:
            deadline = time.monotonic() + 20.0
            exported = False
            while time.monotonic() < deadline:
                await pilot.pause()
                screen = app.screen
                if not isinstance(screen, PipelineScreen):
                    continue
                tracker = screen.query_one("#stage-tracker", StageTracker)
                done = sum(1 for s in tracker._status.values() if s == "done")
                if done >= 3 and not exported:
                    exported = True
                    await pilot.pause()
                    svg = app.export_screenshot(title="Agentic Code Reviewer  -  pipeline")
                    (OUT / "pipeline.svg").write_text(svg, encoding="utf-8")
                    print("wrote", OUT / "pipeline.svg")
                    break
            if not exported:
                print("pipeline capture timed out; screen:", type(app.screen).__name__)
                sys.exit(1)

    asyncio.run(_run())


def capture_results() -> None:
    """Results screen from a real (mock) workflow run."""

    request = ReviewRequest(
        repository="o/r",
        diff_text=SQL_DIFF,
        changed_files=["db.py", "app.py"],
        repo_files=SAMPLE_FILES,
        source="inline",
    )
    settings = Settings(LLM_PROVIDER="mock")
    app = ReviewerApp(request, settings, client=AdaptiveMock())

    async def _run() -> None:
        async with app.run_test(size=(W, H)) as pilot:
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                if isinstance(app.screen, ResultsScreen):
                    break
                await asyncio.sleep(0.05)
            await pilot.pause()
            svg = app.export_screenshot(title="Agentic Code Reviewer  -  findings")
            (OUT / "results.svg").write_text(svg, encoding="utf-8")
            print("wrote", OUT / "results.svg")

    asyncio.run(_run())


def capture_categories() -> None:
    """Categories screen from a real (mock) workflow run.

    The screen highlights the first row (correctness) by default; the mock
    finding lives in the ``security`` category, so move the cursor there so
    the screenshot shows a populated findings pane.
    """
    from textual.widgets import DataTable

    request = ReviewRequest(
        repository="o/r",
        diff_text=SQL_DIFF,
        changed_files=["db.py", "app.py"],
        repo_files=SAMPLE_FILES,
        source="inline",
    )
    settings = Settings(LLM_PROVIDER="mock")
    app = ReviewerApp(request, settings, client=AdaptiveMock())

    async def _run() -> None:
        async with app.run_test(size=(W, H)) as pilot:
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                if isinstance(app.screen, ResultsScreen):
                    break
                await asyncio.sleep(0.05)
            app.screen.action_categories()
            await pilot.pause()
            await pilot.pause()
            # ALLOWED_CHECKS order: correctness(0), security(1), ...  -  highlight
            # the security row so the SQL-injection finding is visible.
            table = app.screen.query_one("#category-table", DataTable)
            table.move_cursor(row=1)
            await pilot.pause()
            await pilot.pause()
            svg = app.export_screenshot(title="Agentic Code Reviewer  -  categories")
            (OUT / "categories.svg").write_text(svg, encoding="utf-8")
            print("wrote", OUT / "categories.svg")

    asyncio.run(_run())


def capture_benchmark() -> None:
    """Benchmark screen with live progress and a populated results table."""

    dataset = Path("benchmarks/datasets/fixture_small.json")
    settings = Settings(LLM_PROVIDER="mock")
    app = BenchmarkApp(dataset, ["single-pass", "agentic"], settings, limit=2)

    async def _run() -> None:
        async with app.run_test(size=(W, H)) as pilot:
            deadline = time.monotonic() + 40.0
            while time.monotonic() < deadline:
                screen = app.screen
                if (
                    isinstance(screen, BenchmarkScreen)
                    and screen.query_one("#bench-results").row_count >= 1
                ):
                    break
                await asyncio.sleep(0.05)
            await pilot.pause()
            svg = app.export_screenshot(title="Agentic Code Reviewer  -  benchmark")
            (OUT / "benchmark.svg").write_text(svg, encoding="utf-8")
            print("wrote", OUT / "benchmark.svg")

    asyncio.run(_run())


def main() -> None:
    if not Path("benchmarks/datasets/fixture_small.json").exists():
        print("run from the project root (missing benchmarks/datasets/fixture_small.json)")
        sys.exit(1)
    seed_dir = _isolate_runtime_config()
    _seed_runtime(seed_dir)
    print("capturing home ...", flush=True)
    capture_home()
    print("capturing providers ...", flush=True)
    capture_providers()
    print("capturing history ...", flush=True)
    capture_history()
    print("capturing help ...", flush=True)
    capture_help()
    print("capturing pipeline ...", flush=True)
    capture_pipeline()
    print("capturing results ...", flush=True)
    capture_results()
    print("capturing categories ...", flush=True)
    capture_categories()
    print("capturing benchmark ...", flush=True)
    capture_benchmark()
    print("done ->", OUT.resolve(), flush=True)


if __name__ == "__main__":
    main()
