"""Pilot-based tests for the command palette (ctrl+p).

Regression coverage for three bugs: (1) the palette opened blank because the
provider never implemented ``discover()`` (Textual routes empty queries there,
not to ``search``), and (2) ``search`` crashed on an empty query via Textual's
fuzzy matcher, and (3) a decorative Save button that never persisted.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from textual.command import CommandPalette
from textual.widgets import OptionList

import agentic_code_reviewer.config.runtime_config as rc
from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.models.findings import ReviewFinding, Severity, VerificationStatus
from agentic_code_reviewer.models.review import Review
from agentic_code_reviewer.models.schemas import WorkflowResult
from agentic_code_reviewer.ui.app import HomeApp, _ReviewCommands
from agentic_code_reviewer.ui.screens.home import HomeScreen
from agentic_code_reviewer.ui.screens.providers import ProvidersScreen


def _result() -> WorkflowResult:
    """A finished review so export actions have something to write."""
    finding = ReviewFinding(
        category="security",
        severity=Severity.HIGH,
        confidence=0.9,
        title="SQL injection",
        description="User input reaches the SQL query.",
        repository="o/r",
        file_path="db.py",
        start_line=11,
        end_line=11,
        evidence="query = f\"...{user_input}\"",
        impact="Data exfiltration",
        recommendation="Use parameterised queries.",
        verification_status=VerificationStatus.VERIFIED,
        rule_id="sql-interpolated",
    )
    review = Review(
        repository="o/r",
        summary="Reviewed db.py",
        findings=[finding],
        model="mock/model",
    )
    return WorkflowResult(review=review, llm_call_count=5, estimated_cost_usd=0.001)


def _run(coro):
    asyncio.run(coro)


def _palette_rows(app) -> list[str]:
    """Text of every command currently shown in the open palette."""
    ol = app.screen.query_one(OptionList)
    return [
        str(ol.get_option_at_index(i).prompt).splitlines()[0]
        for i in range(ol.option_count)
    ]


def test_provider_discover_lists_all_available_commands():
    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="mock"))
    provider = _ReviewCommands(app)

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            hits = [h async for h in provider.discover()]
            texts = {h.text for h in hits}
            # Launcher actions are all available on HomeApp.
            assert "Review this directory" in texts
            assert "Providers & API keys" in texts
            assert "Return to launcher" in texts
            assert "Quit" in texts
            assert "Export review as Markdown" in texts
            assert "Export review as JSON" in texts
            assert "Export review as SARIF" in texts
            assert "Clear review history" in texts
            assert "Open config folder" in texts

    _run(_test())


def test_provider_search_filters_commands():
    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="mock"))
    provider = _ReviewCommands(app)

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            hits = [h async for h in provider.search("export")]
            assert {h.text for h in hits} == {
                "Export review as Markdown",
                "Export review as JSON",
                "Export review as SARIF",
            }
            # Clear is offered too.
            clear = [h async for h in provider.search("clear history")]
            assert [h.text for h in clear] == ["Clear review history"]
            # Empty string must not crash (Textual 8.2.8 fuzzy bug).
            empty = [h async for h in provider.search("")]
            assert len(empty) == len([h async for h in provider.discover()])

    _run(_test())


def test_palette_opens_with_commands():
    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="mock"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+p")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, CommandPalette)
            rows = _palette_rows(app)
            assert "Review this directory" in rows
            assert "Providers & API keys" in rows
            assert "Quit" in rows

    _run(_test())


def test_palette_run_command_navigates():
    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="mock"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+p")
            await pilot.pause()
            await pilot.pause()
            # Type a query that uniquely matches "Providers & API keys".
            for ch in "providers":
                await pilot.press(ch)
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, CommandPalette)
            rows = _palette_rows(app)
            assert len(rows) == 1 and "Providers" in rows[0]
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, ProvidersScreen)

    _run(_test())


def test_palette_quit_command_exits_app():
    """Regression: the palette's Quit must actually exit  -  Textual's
    ``action_quit`` is async and was being invoked without awaiting."""
    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="mock"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            assert app._exit is False
            await pilot.press("ctrl+p")
            await pilot.pause()
            await pilot.pause()
            for ch in "quit":
                await pilot.press(ch)
            await pilot.pause()
            await pilot.pause()
            rows = _palette_rows(app)
            assert len(rows) == 1 and "Quit" in rows[0]
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            assert app._exit is True

    _run(_test())


def test_palette_escape_returns_to_launcher():
    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="mock"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+p")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, CommandPalette)
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, HomeScreen)

    _run(_test())


def _last_notification(app) -> str:
    """The message of the most recent toast (``App.notify``).

    Reaches into ``app._notifications``  -  Textual 8.2.8 has no public reader;
    if a future Textual bump renames it, this is where the test breaks.
    """
    return list(app._notifications)[-1].message


def test_palette_clear_history_requires_confirmation(monkeypatch):
    """"Clear review history" shows a confirm modal; only confirming wipes."""
    from textual.widgets import Button

    from agentic_code_reviewer.ui.screens.confirm import ConfirmScreen

    tmp = Path(tempfile.mkdtemp(prefix="acr-test-"))
    monkeypatch.setattr(rc, "config_dir", lambda: tmp)
    rc.RuntimeConfig.load().record_review({"repository": "o/r", "high": 2})

    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="mock"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            assert (tmp / "history.jsonl").exists()
            app.action_clear_history()
            await pilot.pause()
            await pilot.pause()
            # Guard is up: a modal is shown and nothing has been wiped yet.
            assert isinstance(app.screen, ConfirmScreen)
            assert (tmp / "history.jsonl").exists()
            # Enter must CANCEL (safe choice is pre-focused), not wipe.
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            assert not isinstance(app.screen, ConfirmScreen)
            assert (tmp / "history.jsonl").exists()
            # Re-open and deliberately confirm via the danger button.
            app.action_clear_history()
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            app.screen.query_one("#confirm-ok", Button).press()
            await pilot.pause()
            await pilot.pause()
            assert not (tmp / "history.jsonl").exists()
            assert rc.RuntimeConfig.load().load_history() == []
            assert "Cleared 1 review from history" in _last_notification(app)

    _run(_test())


def test_palette_clear_history_cancel_keeps_history(monkeypatch):
    """Cancelling the confirm modal must leave history untouched."""
    from textual.widgets import Button

    from agentic_code_reviewer.ui.screens.confirm import ConfirmScreen

    tmp = Path(tempfile.mkdtemp(prefix="acr-test-"))
    monkeypatch.setattr(rc, "config_dir", lambda: tmp)
    rc.RuntimeConfig.load().record_review({"repository": "o/r", "high": 2})

    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="mock"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            app.action_clear_history()
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            app.screen.query_one("#confirm-cancel", Button).press()
            await pilot.pause()
            await pilot.pause()
            assert (tmp / "history.jsonl").exists()
            assert len(rc.RuntimeConfig.load().load_history()) == 1

    _run(_test())


def test_palette_clear_history_empty_does_not_prompt(monkeypatch):
    """Clearing an already-empty history just informs; no modal needed."""
    from agentic_code_reviewer.ui.screens.confirm import ConfirmScreen

    tmp = Path(tempfile.mkdtemp(prefix="acr-test-"))
    monkeypatch.setattr(rc, "config_dir", lambda: tmp)

    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="mock"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            app.action_clear_history()
            await pilot.pause()
            assert not isinstance(app.screen, ConfirmScreen)
            assert "already empty" in _last_notification(app).lower()

    _run(_test())


def test_palette_export_json_and_sarif(tmp_path):
    """Export actions write machine-readable files next to the md export."""
    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="mock"))
    app._result = _result()
    app._review_export = tmp_path / "review.md"

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            app.action_export_json()
            app.action_export_sarif()
            await pilot.pause()
            json_doc = json.loads(
                (tmp_path / "review.json").read_text(encoding="utf-8")
            )
            assert json_doc["schema"] == "agentic-code-reviewer/v1"
            assert json_doc["review"]["findings"][0]["rule_id"] == "sql-interpolated"
            sarif_doc = json.loads(
                (tmp_path / "review.sarif").read_text(encoding="utf-8")
            )
            assert sarif_doc["version"] == "2.1.0"
            assert sarif_doc["runs"][0]["results"][0]["ruleId"] == "sql-interpolated"

    _run(_test())


def test_palette_export_without_review_warns():
    """Export actions with no finished review must notify, never crash."""
    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="mock"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            app.action_export_json()
            await pilot.pause()
            assert "No finished review to export yet" in _last_notification(app)

    _run(_test())


def test_palette_open_config_reveals_folder(monkeypatch):
    """"Open config folder" launches the OS file manager on the config dir."""
    tmp = Path(tempfile.mkdtemp(prefix="acr-test-"))
    monkeypatch.setattr("agentic_code_reviewer.ui.app.config_dir", lambda: tmp)
    opened: list[Path] = []
    if os.name == "nt":
        monkeypatch.setattr(
            "agentic_code_reviewer.ui.app.os.startfile",
            lambda p: opened.append(Path(p)),
        )
    else:
        monkeypatch.setattr(
            "agentic_code_reviewer.ui.app.subprocess.Popen",
            lambda cmd: opened.append(Path(cmd[-1])),
        )

    app = HomeApp(path=".", settings=Settings(LLM_PROVIDER="mock"))

    async def _test() -> None:
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            app.action_open_config()
            await pilot.pause()
            assert opened == [tmp]
            assert "Opened config folder" in _last_notification(app)

    _run(_test())
