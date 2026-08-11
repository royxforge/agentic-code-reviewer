"""Headless (pilot-based) tests for the Textual dashboard.

``App.run_test()`` drives the app without a real terminal. The workflow is run
with the AdaptiveMock client, so the full pipeline  -  pipeline screen → results
screen  -  is exercised end to end.
"""

from __future__ import annotations

import asyncio
import time

from agentic_code_reviewer.orchestration.state import ReviewRequest
from agentic_code_reviewer.ui.app import ReviewerApp
from agentic_code_reviewer.ui.screens.pipeline import PipelineScreen
from agentic_code_reviewer.ui.screens.results import ResultsScreen
from agentic_code_reviewer.ui.theme import STAGE_ORDER
from tests.integration.helpers import AdaptiveMock


def _request(sql_diff: str, repo_files: dict[str, str]) -> ReviewRequest:
    return ReviewRequest(
        repository="o/r",
        diff_text=sql_diff,
        changed_files=["db.py", "app.py"],
        repo_files=repo_files,
        source="inline",
    )


async def _wait_for_results(app, timeout: float = 30.0) -> None:
    """Pump the app loop until the results screen is active."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if isinstance(app.screen, ResultsScreen):
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"results screen never appeared; got {type(app.screen)}")


def test_pipeline_screen_renders_and_streams_events():
    """The pipeline screen renders its stage tracker and reflects events."""
    from textual.app import App as TestApp

    from agentic_code_reviewer.orchestration.events import WorkflowEvent
    from agentic_code_reviewer.ui.widgets import StageTracker

    class Host(TestApp):
        def compose(self):
            yield PipelineScreen(repository="o/r", source="inline")

    async def _run() -> None:
        async with Host().run_test() as pilot:
            await pilot.pause()
            tracker = pilot.app.query_one("#stage-tracker", StageTracker)
            assert len(tracker._status) == len(STAGE_ORDER)
            # Stream lifecycle events into the screen widget.
            pipeline = pilot.app.query_one(PipelineScreen)
            pipeline.handle_event(WorkflowEvent.stage_start("planner"))
            pipeline.handle_event(WorkflowEvent.stage_done("planner", 0.4))
            pipeline.handle_event(WorkflowEvent.stage_failed("security", 1.2, "boom"))
            await pilot.pause()
            assert tracker._status["planner"] == "done"
            assert tracker._status["security"] == "failed"

    asyncio.run(_run())


def test_reviewer_app_pipeline_to_results(
    mock_settings, sql_injection_diff, sample_repo_files
):
    app = ReviewerApp(
        _request(sql_injection_diff, sample_repo_files),
        mock_settings,
        client=AdaptiveMock(),
    )

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            # The mock workflow finishes almost instantly; the app may already
            # be on the results screen. Poll for the transition either way.
            await _wait_for_results(app)

            results = app.screen
            assert isinstance(results, ResultsScreen)
            review = results.result.review
            assert review.repository == "o/r"
            assert any(f.category == "security" for f in review.findings)

    asyncio.run(_run())


def test_benchmark_app_shows_results(mock_settings, tmp_path):
    """The benchmark app runs systems and populates the results table."""
    import json

    from agentic_code_reviewer.ui.app import BenchmarkApp
    from agentic_code_reviewer.ui.screens.benchmark import BenchmarkScreen

    dataset = tmp_path / "fixture.json"
    dataset.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "entry_id": "e1",
                        "repository": "o/r",
                        "commit": "c",
                        "diff": (
                            "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
                            "@@ -1,2 +1,2 @@\n def f():\n-    return 0\n+    return 1\n"
                        ),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    app = BenchmarkApp(dataset, ["single-pass"], mock_settings)

    async def _run() -> None:
        async with app.run_test():
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                # The progress bar fills *before* the results table is populated
                # (two separate queued callbacks), so wait for the actual
                # outcome rather than the progress tick.
                if (
                    isinstance(app.screen, BenchmarkScreen)
                    and app.screen.query_one("#bench-results").row_count >= 1
                ):
                    break
                await asyncio.sleep(0.05)
            assert app.screen.query_one("#bench-results").row_count >= 1

    asyncio.run(_run())


def test_reviewer_app_exports_markdown(
    mock_settings, sql_injection_diff, sample_repo_files, tmp_path
):
    export = tmp_path / "review.md"
    app = ReviewerApp(
        _request(sql_injection_diff, sample_repo_files),
        mock_settings,
        export_path=export,
        client=AdaptiveMock(),
    )

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            await _wait_for_results(app)
            # Trigger the export action directly (no keypress needed).
            app.action_export()
            assert export.exists()
            assert "## Agentic Code Review" in export.read_text(encoding="utf-8")

    asyncio.run(_run())
