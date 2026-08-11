"""Pilot-based tests for the providers configuration screen (credentials configured in-app)."""

from __future__ import annotations

import asyncio

import pytest
from textual.app import App as TextualApp
from textual.widgets import Button, DataTable, Input

from agentic_code_reviewer.config.runtime_config import RuntimeConfig
from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.ui.screens.providers import ProvidersScreen


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Point the runtime config at a temp directory."""
    import agentic_code_reviewer.config.runtime_config as rc

    monkeypatch.setattr(rc, "config_dir", lambda: tmp_path)
    return tmp_path


class Host(TextualApp):
    def __init__(self, settings, **kwargs) -> None:
        super().__init__(**kwargs)
        self.settings = settings
        self.notices: list[str] = []

    def refresh_settings(self) -> None:
        pass

    def notify(self, message: str = "", *args, **kwargs) -> None:
        self.notices.append(str(message))

    def on_mount(self) -> None:
        self.push_screen(ProvidersScreen(self.settings))


def _run(coro):
    asyncio.run(coro)


def _table(app: TextualApp) -> DataTable:
    return app.screen.query_one("#provider-table", DataTable)


def test_providers_screen_lists_all_providers(isolated_config):
    app = Host(Settings(LLM_PROVIDER="mock"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            await pilot.pause()
            assert _table(app).row_count == 6

    _run(_test())


def test_save_writes_credentials_to_config(isolated_config):
    app = Host(Settings(LLM_PROVIDER="mock"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            await pilot.pause()
            # gemini is row 2 in PROVIDER_FIELDS order.
            _table(app).move_cursor(row=2)
            await pilot.pause()
            await pilot.pause()
            api = app.screen.query_one("#field-api_key", Input)
            api.value = "AIza-test-key"
            model = app.screen.query_one("#field-model", Input)
            model.value = "gemini-2.5-pro"
            app.screen.action_save()
            await pilot.pause()

            cfg = RuntimeConfig.load()
            assert cfg.get("llm_provider") == "gemini"
            assert cfg.get("gemini_api_key") == "AIza-test-key"
            assert cfg.get("gemini_model") == "gemini-2.5-pro"
            assert (isolated_config / "config.yaml").exists()

    _run(_test())


def test_save_requires_api_key_for_cloud_providers(isolated_config):
    app = Host(Settings(LLM_PROVIDER="mock"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            await pilot.pause()
            # openai (row 0) with no key -> must not save.
            _table(app).move_cursor(row=0)
            await pilot.pause()
            await pilot.pause()
            app.screen.action_save()
            await pilot.pause()

            cfg = RuntimeConfig.load()
            assert cfg.get("llm_provider") is None
            assert any("needs an API key" in n for n in app.notices)

    _run(_test())


def test_save_requires_base_url_for_openai_compatible(isolated_config):
    app = Host(Settings(LLM_PROVIDER="mock"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            await pilot.pause()
            # openai_compatible is row 3; leave base URL empty.
            _table(app).move_cursor(row=3)
            await pilot.pause()
            await pilot.pause()
            app.screen.action_save()
            await pilot.pause()

            cfg = RuntimeConfig.load()
            assert cfg.get("llm_provider") is None
            assert any("base URL" in n for n in app.notices)

    _run(_test())


def test_save_button_persists_credentials(isolated_config):
    """Clicking the ``Save & apply`` button must persist (regression: the
    button previously had no handler and silently did nothing)."""
    app = Host(Settings(LLM_PROVIDER="mock"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            await pilot.pause()
            # anthropic is row 1.
            _table(app).move_cursor(row=1)
            await pilot.pause()
            await pilot.pause()
            api = app.screen.query_one("#field-api_key", Input)
            api.value = "sk-ant-test"
            save = app.screen.query_one("#provider-save", Button)
            save.press()
            await pilot.pause()
            await pilot.pause()

            cfg = RuntimeConfig.load()
            assert cfg.get("llm_provider") == "anthropic"
            assert cfg.get("anthropic_api_key") == "sk-ant-test"
            assert any("Saved" in n for n in app.notices)

    _run(_test())
