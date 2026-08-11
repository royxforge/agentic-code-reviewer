"""Interactive findings triage for the plain CLI.

After a non-TUI review completes, the user can walk through findings one at a
time and act on them  -  apply the suggested fix (``git apply``), dismiss, copy
the finding, or quit. The loop is deliberately simple and safe:

* findings are never auto-applied without an explicit per-finding confirmation
  (or the ``--apply`` convenience flag);
* ``git apply`` only ever stages nothing and only touches the working tree;
  failures are surfaced as warnings, never exceptions.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agentic_code_reviewer.models.findings import ReviewFinding

# Severity → ANSI colour used in triage output.
_SEVERITY_STYLE = {
    "critical": "bold red",
    "high": "red",
    "medium": "yellow",
    "low": "green",
    "info": "cyan",
}

_DIFF_FENCE = re.compile(r"(?m)^\s*```(?:diff)?\s*$")


def _extract_diff(recommendation: str) -> str | None:
    """Pull a ``diff --git`` patch out of a recommendation's prose.

    The patch starts at the first ``diff --git`` line and ends at the closing
    ``` fence, or  -  when there is no fence  -  at a blank line that appears
    *after* a hunk has started (a blank line inside a hunk is a valid diff
    line, so we only treat blank lines between hunks/sections as the end).
    """
    if not recommendation:
        return None
    lines = recommendation.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("diff --git")), None)
    if start is None:
        return None
    hunk_seen = False
    end = len(lines)
    for i in range(start, len(lines)):
        if i > start and _DIFF_FENCE.match(lines[i]):
            end = i
            break
        if lines[i].startswith("@@"):
            hunk_seen = True
            continue
        if hunk_seen and not lines[i].strip():
            end = i
            break
    patch = "\n".join(lines[start:end]).strip()
    return patch or None


def apply_patch(local_path: str | None, patch: str) -> tuple[bool, str]:
    """Apply a unified diff to the working tree with ``git apply``.

    Returns ``(ok, message)``. Never stages, never commits, never touches
    other worktrees. A dirty target file is not a failure of the reviewer  -
    we report it and move on.
    """
    if not local_path:
        return False, "no local repository to apply to"
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".patch", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(patch)
            patch_path = handle.name
        proc = subprocess.run(
            ["git", "-C", local_path, "apply", "--whitespace=nowarn", patch_path],
            capture_output=True,
            text=True,
            # Same Windows safety as local.git._git: explicit UTF-8 so git's
            # output (which can contain non-ASCII bytes) never fails to decode
            # and leave stdout/stderr as None.
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"git apply failed: {exc}"
    finally:
        try:
            Path(patch_path).unlink(missing_ok=True)
        except NameError:
            pass
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return False, (detail[-1] if detail else "git apply rejected the patch")
    return True, "patch applied to the working tree"


def _finding_table(findings: list[ReviewFinding]) -> Table:
    table = Table(
        title="Findings  -  triage",
        show_header=True,
        header_style="bold #a78bfa",
        border_style="#2b2b45",
        expand=True,
    )
    table.add_column("#", justify="right", width=3)
    table.add_column("Severity", width=9)
    table.add_column("Category", width=16)
    table.add_column("Location", ratio=2)
    table.add_column("Finding", ratio=5)
    for idx, finding in enumerate(findings, start=1):
        loc = finding.file_path or "?"
        if finding.start_line:
            loc += f":{finding.start_line}"
        style = _SEVERITY_STYLE.get(finding.severity.value, "")
        table.add_row(
            str(idx),
            f"[{style}]{finding.severity.value.upper()}[/]",
            finding.category,
            f"[dim]{loc}[/]",
            finding.title,
        )
    return table


def run_triage(
    findings: list[ReviewFinding],
    *,
    local_path: str | None = None,
    auto_apply: bool = False,
    input_stream: TextIO | None = None,
    console: Console | None = None,
    on_finding: Callable[[ReviewFinding, str], None] | None = None,
) -> dict[str, int]:
    """Walk findings and let the user act on them.

    ``input_stream`` is injectable for tests (defaults to ``sys.stdin``).
    ``on_finding`` is called with ``(finding, action)`` after every decision  -
    used by the CLI to log / write a patch journal. Returns action counts.
    """
    out = console or Console()
    counts = {"apply": 0, "dismiss": 0, "copy": 0, "skip": 0, "quit": 0}
    if not findings:
        out.print("[dim]No findings to triage.[/dim]")
        return counts

    stream = input_stream
    out.print(_finding_table(findings))

    for idx, finding in enumerate(findings, start=1):
        style = _SEVERITY_STYLE.get(finding.severity.value, "")
        header = f"[{style}]{finding.severity.value.upper()}[/] {idx}/{len(findings)}  -  {finding.title}"
        panel_text = f"[bold]{finding.description}[/]\n"
        if finding.file_path:
            loc = finding.file_path + (f":{finding.start_line}" if finding.start_line else "")
            panel_text += f"\n[dim]Location:[/] {loc}\n"
        if finding.evidence:
            panel_text += f"[dim]Evidence:[/] {finding.evidence}\n"
        if finding.recommendation:
            panel_text += f"\n[cyan]Recommended fix:[/]\n{finding.recommendation.strip()}"
        out.print(Panel(panel_text, title=header, border_style="#2b2b45"))

        if auto_apply:
            action = "apply"
        else:
            prompt = " ".join(f"[{k}]{label}[/]" for k, label in _ACTIONS)
            action = _ask(stream, f"{prompt}  › ", "skip")

        if action == "quit":
            counts["quit"] += 1
            out.print("[dim]Triage stopped.[/dim]")
            break
        if action == "skip":
            counts["skip"] += 1
            out.print("[dim]Skipped  -  no change made.[/dim]")
        elif action == "dismiss":
            counts["dismiss"] += 1
            out.print(f"[dim]Dismissed {idx}  -  {finding.title}[/dim]")
        elif action == "copy":
            counts["copy"] += 1
            _copy(finding)
            out.print("[dim]Copied to clipboard.[/dim]")
        else:
            patch = _extract_diff(finding.recommendation)
            if patch:
                ok, message = apply_patch(local_path, patch)
                counts["apply" if ok else "dismiss"] += 1
                if ok:
                    out.print(f"[green]✔ Applied: {finding.title}[/green]")
                else:
                    out.print(f"[yellow]⚠ Could not apply: {message}[/yellow]")
            else:
                counts["dismiss"] += 1
                out.print(
                    "[yellow]⚠ No diff in recommendation  -  suggest applying manually.[/yellow]"
                )
        if on_finding is not None:
            on_finding(finding, action)

    out.print(
        f"[dim]Triage complete  -  applied {counts['apply']}, dismissed "
        f"{counts['dismiss']}, copied {counts['copy']}, skipped {counts['skip']}.[/dim]"
    )
    return counts


_ACTIONS = (
    ("a", "apply"),
    ("d", "dismiss"),
    ("c", "copy"),
    ("s", "skip"),
    ("q", "quit"),
)


def _ask(stream: TextIO | None, prompt: str, default: str) -> str:
    """One line of input; EOF or KeyboardInterrupt resolves to quit."""
    import sys

    src = stream if stream is not None else sys.stdin
    try:
        value = src.readline().strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "quit"
    if not value:
        return default
    for key, label in _ACTIONS:
        if value == label or value == key:
            return label
    return default


def _copy(finding: ReviewFinding) -> None:
    """Copy the finding to the clipboard (best effort, never raises)."""
    text = (
        f"[{finding.severity.value.upper()}] {finding.title}\n"
        f"{finding.description}\n"
        f"Evidence: {finding.evidence}\n"
        f"Recommendation: {finding.recommendation}"
    )
    try:
        import platform
        import subprocess as sp

        command = {"Windows": ["clip"], "Darwin": ["pbcopy"]}.get(platform.system())
        if command is None:
            command = ["xclip", "-selection", "clipboard"]
        sp.run(
            command,
            input=text,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=5,
        )
    except Exception:  # noqa: BLE001 - clipboard must never crash the CLI
        pass
