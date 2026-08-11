"""Pilot-based tests for the review history screen."""

from __future__ import annotations

import asyncio

import pytest
from textual.app import App as TextualApp
from textual.widgets import DataTable

from agentic_code_reviewer.config.runtime_config import RuntimeConfig
from agentic_code_reviewer.ui.screens.history import HistoryScreen


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    import agentic_code_reviewer.config.runtime_config as rc

    monkeypatch.setattr(rc, "config_dir", lambda: tmp_path)
    return tmp_path


class Host(TextualApp):
    def on_mount(self) -> None:
        self.push_screen(HistoryScreen())


def _run(coro):
    asyncio.run(coro)


def _seed(isolated_config, n: int) -> None:
    cfg = RuntimeConfig.load()
    for i in range(n):
        cfg.record_review(
            {
                "repository": f"owner/repo{i}",
                "source": "local",
                "model": "mock/model",
                "llm_calls": 4,
                "high": i % 2,
            }
        )


def test_history_shows_seeded_entries(isolated_config):
    _seed(isolated_config, 3)
    app = Host()

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            await pilot.pause()
            table: DataTable = app.screen.query_one("#history-table", DataTable)
            assert table.row_count == 3
            # Most recent first.
            first_row = [str(c) for c in table.get_row_at(0)]
            assert any("repo2" in c for c in first_row)

    _run(_test())


def test_history_empty_state(isolated_config):
    app = Host()

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            await pilot.pause()
            table: DataTable = app.screen.query_one("#history-table", DataTable)
            # A single placeholder row explains the empty state.
            assert table.row_count == 1
            text = " ".join(str(c) for c in table.get_row_at(0))
            assert "No reviews yet" in text

    _run(_test())


def test_history_refresh_picks_up_new_entries(isolated_config):
    _seed(isolated_config, 1)
    app = Host()

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            await pilot.pause()
            table: DataTable = app.screen.query_one("#history-table", DataTable)
            assert table.row_count == 1
            _seed(isolated_config, 1)  # one more review happens elsewhere
            app.screen.action_refresh()
            await pilot.pause()
            assert table.row_count == 2

    _run(_test())
