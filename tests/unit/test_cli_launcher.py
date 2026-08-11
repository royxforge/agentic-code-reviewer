"""Bare ``acr`` dispatch: the launcher menu is the default entry point."""

from __future__ import annotations

import sys

from typer.testing import CliRunner

from agentic_code_reviewer.cli import _dispatch_bare_path, app, main

runner = CliRunner()


def _patch_launcher(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "agentic_code_reviewer.cli._launch_tui_home",
        lambda path=".": calls.append(path),
    )
    return calls


def test_bare_acr_opens_launcher(monkeypatch):
    calls = _patch_launcher(monkeypatch)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert calls == ["."]


def test_bare_acr_tui_opens_launcher(monkeypatch):
    calls = _patch_launcher(monkeypatch)
    result = runner.invoke(app, ["tui"])
    assert result.exit_code == 0
    assert calls == ["."]


def test_acr_path_dispatches_to_launcher(monkeypatch):
    calls = _patch_launcher(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["acr", "path/to/project"])
    main()
    assert calls == ["path/to/project"]


def test_dispatch_bare_path_ignores_known_commands():
    """`acr review-local …` must NOT be swallowed by the path intercept."""
    assert _dispatch_bare_path("review-local") is False
    assert _dispatch_bare_path("get-started") is False
    assert _dispatch_bare_path("--version") is False


def test_acr_version_still_works(monkeypatch):
    calls = _patch_launcher(monkeypatch)
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "acr" in result.output
    assert calls == []  # version exits without opening the launcher
