"""Pilot-based tests for the boot splash screen (``acr`` launch animation)."""

from __future__ import annotations

import asyncio

from textual.widgets import Static

from agentic_code_reviewer.ui.app import HomeApp
from agentic_code_reviewer.ui.screens.home import HomeScreen
from agentic_code_reviewer.ui.screens.splash import SPLASH_SECONDS, SplashScreen


def _run(coro):
    asyncio.run(coro)


def _mount_splash(app, pilot):
    """Show the splash on a real HomeApp and return it (bypasses the
    headless fast-path so we can drive the animation directly)."""
    splash = SplashScreen(on_dismiss=app._show_launcher)
    app.push_screen(splash)
    return splash


def test_splash_shows_brand_and_version():
    """The splash renders the logo, product name, version, and a loading line."""
    from agentic_code_reviewer import __version__

    app = HomeApp(path=".", settings=None)
    seen = {}

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            splash = _mount_splash(app, pilot)
            await pilot.pause()
            assert isinstance(app.screen, SplashScreen)
            logo = str(splash.query_one("#splash-logo", Static).render())
            # The logo is block/box-drawing art, not literal letters.
            assert "█" in logo and "╗" in logo and "╝" in logo
            assert len(logo.splitlines()) >= 5
            name = str(splash.query_one("#splash-name", Static).render())
            assert "AGENTIC CODE REVIEWER" in name
            version = str(splash.query_one("#splash-version", Static).render())
            assert __version__ in version
            seen["ok"] = True

    _run(_test())
    assert seen.get("ok")


def test_splash_auto_dismisses_after_timeout():
    """Without any input the splash pops itself and the launcher appears."""
    app = HomeApp(path=".", settings=None)

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            _mount_splash(app, pilot)
            await pilot.pause()
            assert isinstance(app.screen, SplashScreen)
            # Let the auto-dismiss timer (SPLASH_SECONDS) elapse.
            await pilot.pause(SPLASH_SECONDS + 0.5)
            await pilot.pause()
            assert isinstance(app.screen, HomeScreen)

    _run(_test())


def test_splash_dismisses_on_any_key():
    """A key press skips the animation immediately and shows the launcher."""
    app = HomeApp(path=".", settings=None)

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            _mount_splash(app, pilot)
            await pilot.pause()
            assert isinstance(app.screen, SplashScreen)
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, HomeScreen)

    _run(_test())


def test_splash_animation_advances():
    """The loading line changes between frames (spinner + label tick)."""
    app = HomeApp(path=".", settings=None)

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            splash = _mount_splash(app, pilot)
            await pilot.pause()
            first = str(splash.query_one("#splash-spinner", Static).render())
            for _ in range(4):
                await pilot.pause(0.15)
            second = str(splash.query_one("#splash-spinner", Static).render())
            assert first != second

    _run(_test())


def test_splash_dismiss_callback_fires_exactly_once():
    """A key press just before the auto-dismiss timer never double-fires the
    dismiss callback (the screen guard makes the late timer a no-op)."""
    app = HomeApp(path=".", settings=None)
    calls = []

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            splash = SplashScreen(on_dismiss=lambda: calls.append(1))
            app.push_screen(splash)
            await pilot.pause()
            # Advance almost to the auto-dismiss deadline, then press a key.
            await pilot.pause(max(0.01, SPLASH_SECONDS - 0.1))
            await pilot.press("enter")
            await pilot.pause()
            # Let the (now stale) auto-dismiss timer fire and process.
            await pilot.pause(SPLASH_SECONDS + 0.5)
            await pilot.pause()
            assert len(calls) == 1

    _run(_test())


def test_headless_home_skips_splash():
    """Headless launch (tests, screenshots) goes straight to the launcher."""
    from agentic_code_reviewer.config.settings import Settings

    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="mock"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, HomeScreen)
            assert not isinstance(app.screen, SplashScreen)

    _run(_test())
