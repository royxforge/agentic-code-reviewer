"""Pilot-based tests for the launcher home screen and its navigation."""

from __future__ import annotations

import asyncio

from textual.widgets import ListView

from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.ui.app import HomeApp
from agentic_code_reviewer.ui.screens.help import HelpScreen
from agentic_code_reviewer.ui.screens.history import HistoryScreen
from agentic_code_reviewer.ui.screens.home import (
    HomeScreen,
    PathPromptScreen,
    WelcomeScreen,
)
from agentic_code_reviewer.ui.screens.providers import ProvidersScreen


def _run(coro):
    asyncio.run(coro)


def _isolate_config(monkeypatch):
    """Point the runtime config at a throwaway dir (never touch real data)."""
    import tempfile
    from pathlib import Path

    import agentic_code_reviewer.config.runtime_config as rc

    _tmp = Path(tempfile.mkdtemp(prefix="acr-test-cfg-"))
    monkeypatch.setattr(rc, "config_dir", lambda: _tmp)
    return _tmp


def _no_env_credentials(monkeypatch) -> None:
    """Clear provider credentials/selection from the real environment."""
    for var in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "OPENAI_COMPATIBLE_API_KEY",
        "OLLAMA_API_KEY",
        "LLM_PROVIDER",
    ):
        monkeypatch.delenv(var, raising=False)


def test_home_menu_lists_six_actions():
    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="mock"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, HomeScreen)
            menu = app.screen.query_one("#home-menu", ListView)
            assert len(menu.children) == 6

    _run(_test())


def test_home_provider_card_warns_when_provider_not_configured(monkeypatch):
    # openai selected but no key anywhere -> provider card must point to
    # the Providers screen. (pydantic-settings reads the real environment,
    # so isolate the keys and the stored config.)
    _isolate_config(monkeypatch)
    _no_env_credentials(monkeypatch)
    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="openai"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            # Unconfigured launch auto-opens the first-run onboarding modal.
            assert isinstance(app.screen, WelcomeScreen)
            await pilot.press("escape")
            await pilot.pause()
            card = str(app.screen.query_one("#home-provider").render())
            assert "not configured" in card.lower()
            assert "add an API key" in card  # points the user to the in-app Providers screen

    _run(_test())


def test_home_status_panel_lists_recent_reviews_and_engine(monkeypatch):
    """The right pane shows recent local reviews and engine stats."""
    import tempfile
    from pathlib import Path

    import agentic_code_reviewer.config.runtime_config as rc

    _tmp = Path(tempfile.mkdtemp(prefix="acr-test-"))
    monkeypatch.setattr(rc, "config_dir", lambda: _tmp)
    cfg = rc.RuntimeConfig.load()
    cfg.record_review({"repository": "shop/api", "source": "local", "model": "gpt-4o",
                       "high": 2, "medium": 3})
    cfg.record_review({"repository": "blog", "source": "local", "model": "gpt-4o",
                       "high": 1})
    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="mock"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            recent = str(app.screen.query_one("#home-recent").render())
            assert "shop/api" in recent
            assert "blog" in recent
            assert "2H" in recent
            engine = str(app.screen.query_one("#home-engine").render())
            assert "pipeline stages" in engine
            assert "review categories" in engine

    _run(_test())


def test_number_keys_navigate_to_sub_screens():
    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="mock"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            await pilot.press("3")
            await pilot.pause()
            assert isinstance(app.screen, ProvidersScreen)
            await pilot.press("q")
            await pilot.pause()
            assert isinstance(app.screen, HomeScreen)

            await pilot.press("4")
            await pilot.pause()
            assert isinstance(app.screen, HistoryScreen)
            await pilot.press("q")
            await pilot.pause()

            await pilot.press("5")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, HelpScreen)
            await pilot.press("q")
            await pilot.pause()
            assert isinstance(app.screen, HomeScreen)

    _run(_test())


def test_path_prompt_returns_to_home_on_cancel():
    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="mock"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            await pilot.press("2")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, PathPromptScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, HomeScreen)

    _run(_test())


def test_review_flow_returns_to_home_from_results(sql_injection_diff, sample_repo_files, monkeypatch):
    """Full round trip: launcher -> review -> results -> `h` back to the menu.
    History recording is isolated to a temp config dir."""
    import tempfile
    from pathlib import Path

    import agentic_code_reviewer.config.runtime_config as rc

    _tmp = Path(tempfile.mkdtemp(prefix="acr-test-"))
    monkeypatch.setattr(rc, "config_dir", lambda: _tmp)
    from agentic_code_reviewer.orchestration.state import ReviewRequest
    from agentic_code_reviewer.ui.screens.results import ResultsScreen
    from tests.integration.helpers import AdaptiveMock

    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="mock"))
    request = ReviewRequest(
        repository="o/r",
        diff_text=sql_injection_diff,
        changed_files=["db.py"],
        repo_files=sample_repo_files,
        source="local",
    )

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, HomeScreen)
            app.start_review(request, app.settings, client=AdaptiveMock())
            for _ in range(300):
                await pilot.pause()
                if isinstance(app.screen, ResultsScreen):
                    break
            assert isinstance(app.screen, ResultsScreen)
            await pilot.press("h")
            await pilot.pause()
            assert isinstance(app.screen, HomeScreen)

    _run(_test())


def test_quit_menu_item_exits_app():
    """Regression: selecting Quit (click/enter path) must actually exit  -
    Textual's ``action_quit`` is async and was being called un-awaited."""
    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="mock"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            assert app._exit is False
            menu = app.screen.query_one("#home-menu", ListView)
            menu.index = 5  # Quit
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            assert app._exit is True

    _run(_test())


def test_number_key_6_quits_app():
    """The ``6`` shortcut (Quit) must exit too (same async-action path)."""
    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="mock"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            assert app._exit is False
            await pilot.press("6")
            await pilot.pause()
            await pilot.pause()
            assert app._exit is True

    _run(_test())


def test_jk_keys_navigate_the_menu():
    """j/k move the launcher menu cursor like the results table."""
    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="mock"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            menu = app.screen.query_one("#home-menu", ListView)
            assert menu.index == 0
            await pilot.press("j")
            await pilot.pause()
            assert menu.index == 1
            await pilot.press("j")
            await pilot.pause()
            assert menu.index == 2
            await pilot.press("k")
            await pilot.pause()
            assert menu.index == 1

    _run(_test())


def test_q_on_results_returns_to_home(sql_injection_diff, sample_repo_files, monkeypatch):
    """`q` from a finished review returns to the launcher (not quit) when the
    review was started there."""
    import tempfile
    from pathlib import Path

    import agentic_code_reviewer.config.runtime_config as rc

    _tmp = Path(tempfile.mkdtemp(prefix="acr-test-"))
    monkeypatch.setattr(rc, "config_dir", lambda: _tmp)
    from agentic_code_reviewer.orchestration.state import ReviewRequest
    from agentic_code_reviewer.ui.screens.results import ResultsScreen
    from tests.integration.helpers import AdaptiveMock

    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="mock"))
    request = ReviewRequest(
        repository="o/r",
        diff_text=sql_injection_diff,
        changed_files=["db.py"],
        repo_files=sample_repo_files,
        source="local",
    )

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            app.start_review(request, app.settings, client=AdaptiveMock())
            for _ in range(300):
                await pilot.pause()
                if isinstance(app.screen, ResultsScreen):
                    break
            assert isinstance(app.screen, ResultsScreen)
            await pilot.press("q")
            await pilot.pause()
            assert isinstance(app.screen, HomeScreen)

    _run(_test())


def test_review_this_directory_on_non_git_opens_path_prompt():
    """`1` on a non-git directory must not dead-end: it opens the path picker."""
    import tempfile

    non_git = tempfile.mkdtemp(prefix="acr-nongit-")
    app = HomeApp(path=non_git, settings=Settings(LLM_PROVIDER="mock"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, HomeScreen)
            await pilot.press("1")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, PathPromptScreen)

    _run(_test())


def test_footer_has_no_duplicate_command_palette():
    """Textual's footer re-renders the palette key on the right when it is also
    bound; we disable that so `ctrl+p` appears exactly once."""
    from textual.widgets import Footer

    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="mock"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            footer = app.screen.query_one(Footer)
            assert footer.show_command_palette is False

    _run(_test())


def test_first_run_shows_onboarding_when_unconfigured(monkeypatch):
    """No stored config and no environment credentials  -  the welcome modal
    auto-opens; escape dismisses it back to the launcher."""
    _isolate_config(monkeypatch)
    _no_env_credentials(monkeypatch)
    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="openai"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, WelcomeScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, HomeScreen)

    _run(_test())


def test_first_run_does_not_show_when_configured():
    """A stored or environment provider means no onboarding  -  straight to the
    launcher dashboard."""
    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="mock"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, HomeScreen)

    _run(_test())


def test_first_run_configure_opens_providers_screen(monkeypatch):
    """Option 1 from the welcome modal opens the Providers wizard."""
    _isolate_config(monkeypatch)
    _no_env_credentials(monkeypatch)
    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="openai"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, WelcomeScreen)
            await pilot.press("1")
            await pilot.pause()
            assert isinstance(app.screen, ProvidersScreen)
            await pilot.press("q")
            await pilot.pause()
            assert isinstance(app.screen, HomeScreen)

    _run(_test())


def test_first_run_configure_mock_activates_provider(monkeypatch):
    """Option 2 activates the keyless mock provider; the launcher card turns
    READY and the choice is persisted to the (isolated) user config."""
    import agentic_code_reviewer.config.runtime_config as rc

    _isolate_config(monkeypatch)
    _no_env_credentials(monkeypatch)
    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="openai"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, WelcomeScreen)
            await pilot.press("2")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, HomeScreen)
            card = str(app.screen.query_one("#home-provider").render())
            assert "ready" in card.lower()
            stored = rc.RuntimeConfig.load().get("llm_provider")
            assert stored == "mock"

    _run(_test())


def test_path_prompt_rejects_empty_input():
    """Submitting an empty path just closes the prompt  -  no review starts."""
    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="mock"))
    started: list[str] = []
    app._begin_local_review = lambda path: started.append(path)  # type: ignore[method-assign]

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            await pilot.press("2")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, PathPromptScreen)
            await pilot.press("enter")  # empty input -> submit no-op
            await pilot.pause()
            assert isinstance(app.screen, HomeScreen)
            assert started == []

    _run(_test())
