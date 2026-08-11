"""Pilot-based tests for the categories screen (findings grouped by check type)."""

from __future__ import annotations

import asyncio

from textual.app import App as TextualApp
from textual.widgets import DataTable

from agentic_code_reviewer.models.findings import ReviewFinding, Severity, VerificationStatus
from agentic_code_reviewer.models.review import ALLOWED_CHECKS, Review
from agentic_code_reviewer.models.schemas import WorkflowResult
from agentic_code_reviewer.ui.screens.categories import CategoriesScreen
from agentic_code_reviewer.ui.screens.results import ResultsScreen


def _finding(category: str, severity: Severity, title: str, file_path: str) -> ReviewFinding:
    return ReviewFinding(
        category=category,
        severity=severity,
        confidence=0.9,
        title=title,
        description=f"{title} description",
        repository="o/r",
        file_path=file_path,
        start_line=10,
        evidence="evidence",
        impact="impact",
        recommendation="recommendation",
        verification_status=VerificationStatus.VERIFIED,
    )


def _result() -> WorkflowResult:
    review = Review(
        repository="o/r",
        summary="reviewed",
        findings=[
            _finding("security", Severity.HIGH, "SQL injection", "db.py"),
            _finding("security", Severity.MEDIUM, "Weak auth", "auth.py"),
            _finding("correctness", Severity.HIGH, "Off-by-one", "search.py"),
            _finding("performance", Severity.LOW, "Slow query", "db.py"),
        ],
        model="mock/model",
    )
    return WorkflowResult(review=review, llm_call_count=4, estimated_cost_usd=0.01)


class Host(TextualApp):
    def __init__(self, result, **kwargs) -> None:
        super().__init__(**kwargs)
        self._result = result

    def on_mount(self) -> None:
        self.push_screen(CategoriesScreen(self._result))


def _table(app: TextualApp, id: str) -> DataTable:
    # In Textual 8, ``app.query_one`` resolves against the default screen, so
    # query the pushed screen's own DOM instead.
    return app.screen.query_one(f"#{id}", DataTable)


def test_categories_screen_lists_all_check_types():
    app = Host(_result())

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            table = _table(app, "category-table")
            assert table.row_count == len(ALLOWED_CHECKS)
            # The first category (correctness) is highlighted and its findings shown.
            findings_table = _table(app, "category-findings")
            assert findings_table.row_count == 1  # 1 correctness finding

    asyncio.run(_run())


def test_categories_drill_down_filters_to_category():
    app = Host(_result())

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            # Highlight the correctness row, then drill in.
            table = _table(app, "category-table")
            # Row order follows ALLOWED_CHECKS: find the correctness index.
            index = ALLOWED_CHECKS.index("correctness")
            table.move_cursor(row=index)
            await pilot.pause()
            # The findings pane should now show correctness findings.
            findings_table = _table(app, "category-findings")
            assert findings_table.row_count == 1

            app.screen.action_drill_down()
            await pilot.pause()

            assert isinstance(app.screen, ResultsScreen)
            results: ResultsScreen = app.screen
            assert results.only_category == "correctness"
            assert len(results._findings) == 1
            assert results._findings[0].category == "correctness"

    asyncio.run(_run())


def test_categories_zero_count_categories_shown():
    app = Host(_result())

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            table = _table(app, "category-table")
            # All registered categories render (23 with the upgrade); only a few
            # have findings  -  the rest render with 0.
            assert table.row_count == len(ALLOWED_CHECKS)
            row_categories = app.screen._row_categories
            assert len(row_categories) == len(ALLOWED_CHECKS)

    asyncio.run(_run())
