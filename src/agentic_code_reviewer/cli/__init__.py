"""Command-line interface.

    acr                                   # launcher menu on ./ (review / providers / history / help)
    acr path/to/project                   # launcher menu on any git project
    acr review-local .                    # review local changes, markdown report
    acr review-local . --format json      # machine-readable JSON for CI
    acr review-local . --fail-on high     # exit 1 when any high/critical finding exists
    acr review-local . --interactive      # triage findings: apply / dismiss / copy / skip
    acr review --repo owner/name --pr 123 --publish
    acr review-commit --repo owner/name --commit abc123
    acr benchmark --dataset benchmarks/datasets/fixture_small.json
    acr evaluate --config configs/experiment.yaml
    acr init                              # scaffold .reviewer.yaml + shell completion
    acr completion bash                   # print shell completion
    acr tui review-local .                # full-screen terminal dashboard

The CLI is intentionally thin: all behaviour lives in the workflow, evaluation
and adapter modules. Bare ``acr`` (and ``acr tui …``) launch the full-screen
Textual dashboard  -  a Claude-Code-style launcher menu first, then the live
pipeline and findings browser. Providers and API keys are configured from the
launcher (or ``acr get-started``) and persisted to the user config  -  no
secrets file is required.

Quality gates: every review command accepts ``--fail-on <severity>`` (or a
per-repo policy in ``.reviewer.yaml``). When the review contains a finding at
or above that severity, the command exits with code 1  -  the standard CI hook.
Machine output: ``--format markdown|json|sarif``.
"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agentic_code_reviewer import __version__
from agentic_code_reviewer.cli import interactive
from agentic_code_reviewer.cli.exporters import export_review
from agentic_code_reviewer.cli.knowledge import knowledge_app
from agentic_code_reviewer.config.file_config import (
    ConfigError,
    RepoConfig,
    apply_repo_config,
    config_notice,
    load_repo_config,
    should_ignore,
)
from agentic_code_reviewer.config.runtime_config import (
    PROVIDER_FIELDS,
    RuntimeConfig,
    apply_runtime_config,
    config_dir,
    config_path,
    provider_help_text,
)
from agentic_code_reviewer.config.settings import Settings, get_settings
from agentic_code_reviewer.errors import ReviewerError
from agentic_code_reviewer.evaluation.benchmark import BenchmarkLoader
from agentic_code_reviewer.evaluation.experiments import ExperimentTracker
from agentic_code_reviewer.evaluation.metrics import format_results_table, to_table_row
from agentic_code_reviewer.evaluation.runner import run_benchmark
from agentic_code_reviewer.github.adapter import request_from_commit, request_from_pull_request
from agentic_code_reviewer.github.client import GitHubClient
from agentic_code_reviewer.github.models import ReviewComment
from agentic_code_reviewer.local.git import local_request, local_request_with_fallback
from agentic_code_reviewer.models.findings import Severity
from agentic_code_reviewer.models.schemas import WorkflowResult
from agentic_code_reviewer.observability.logging import setup_logging
from agentic_code_reviewer.orchestration.state import ReviewRequest
from agentic_code_reviewer.orchestration.workflow import Workflow

app = typer.Typer(
    name="acr",
    help=(
        "Agentic Code Reviewer  -  multi-step LLM code review with benchmarking.\n\n"
        "Typing `acr` with no command opens the launcher menu on the current "
        "directory (review / providers / history / help); `acr <path>` opens it "
        "on any git project. Providers and API keys are configured from the "
        "menu  -  all credentials are set up right there."
    ),
    no_args_is_help=False,
    add_completion=False,
)
# ``main()`` reconfigures stdio to UTF-8 first; Rich then inherits it, so
# help, diffs and findings ( - , ⚡, ✔) never crash a cp1252 console.
console = Console(highlight=False)
# Human progress/status goes to stderr when a machine format is on stdout, so
# `acr review-local . --format json` stays parseable (CI contract).
err_console = Console(highlight=False, stderr=True)

_SYSTEMS = ("single-pass", "context", "rag", "agentic")

_FORMATS = ("markdown", "json", "sarif")
_FAIL_ON_CHOICES = ("any", "critical", "high", "medium", "low", "info")

# Severity -> minimum rank that trips a ``--fail-on`` gate.
_FAIL_ON_RANK: dict[str, int] = {
    "any": Severity.INFO.rank,  # any finding fails the gate
    "critical": Severity.CRITICAL.rank,
    "high": Severity.HIGH.rank,
    "medium": Severity.MEDIUM.rank,
    "low": Severity.LOW.rank,
    "info": Severity.INFO.rank,
}

tui_app = typer.Typer(
    name="tui",
    help="Interactive terminal dashboard (Textual).",
    no_args_is_help=False,
    short_help="Interactive terminal dashboard",
)
app.add_typer(tui_app)
app.add_typer(knowledge_app)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _run(request: ReviewRequest, settings: Settings) -> WorkflowResult:
    workflow = Workflow(settings)
    return workflow.run(request)


def _settings() -> Settings:
    """Settings with the user runtime config merged in (environment always wins)."""
    return apply_runtime_config(get_settings())


def _apply_repo_settings(path: str, settings: Settings) -> tuple[Settings, RepoConfig]:
    """Merge the nearest ``.reviewer.yaml`` into settings; return both."""
    try:
        repo_config = load_repo_config(path)
    except ConfigError as exc:
        raise typer.BadParameter(str(exc)) from None
    if repo_config.source_path is not None:
        settings = apply_repo_config(settings, repo_config)
    return settings, repo_config


def _filtered_findings(result: WorkflowResult, repo_config: RepoConfig) -> WorkflowResult:
    """Drop findings on paths the repo config says to ignore."""
    if not repo_config.ignore_patterns:
        return result
    kept = [
        f for f in result.review.findings if not should_ignore(repo_config, f.file_path)
    ]
    if len(kept) == len(result.review.findings):
        return result
    result.review.findings = kept
    return result


def _gate_violation(result: WorkflowResult, fail_on: str | None) -> str:
    """Return a human-readable gate violation ("" = gate passes)."""
    if not fail_on:
        return ""
    threshold = _FAIL_ON_RANK[fail_on]
    offenders = [
        f for f in result.review.findings if f.severity.rank >= threshold
    ]
    if not offenders:
        return ""
    worst = max(offenders, key=lambda f: f.severity.rank)
    return f"{worst.severity.value.upper()} finding: {worst.title}  -  gate is --fail-on {fail_on}"


def _enforce_gate(violation: str, fail_on: str | None, out: Console) -> None:
    """Print the gate result and exit 1 when violated (CI contract)."""
    if not fail_on:
        return
    if violation:
        out.print(f"[red]✗ Quality gate failed ({fail_on}): {violation}[/red]")
        raise typer.Exit(code=1)
    out.print(f"[green]✔ Quality gate passed (--fail-on {fail_on}).[/green]")


def _render(
    result: WorkflowResult,
    output: Path | None,
    *,
    fmt: str = "markdown",
    fail_on: str | None = None,
) -> None:
    """Render a review: machine format to stdout/file, human summary to stderr."""
    if fmt == "markdown" and output is None:
        _render_human(result)
        return
    if fmt != "markdown":
        export_review(result, fmt, output)
        # Human summary goes to stderr so stdout stays parseable for CI.
        _render_human(result, compact=True, stream=err_console)
        return
    export_review(result, "markdown", output)
    if output is not None:
        console.print(f"\n[dim]Review written to {output}[/dim]")


def _render_human(
    result: WorkflowResult,
    *,
    compact: bool = False,
    stream: Console | None = None,
) -> None:
    """Human-oriented summary; the TUI is the full experience, this is the CLI view."""
    out = stream or console
    review = result.review
    if compact:
        m = review.metrics
        out.print(
            Panel(
                f"[bold]Agentic Code Review[/bold]  -  {review.repository}\n"
                f"model {review.model} · {result.llm_call_count} calls · "
                f"${result.estimated_cost_usd:.4f}\n"
                f"[red]{m.critical} critical[/] [bold red]· {m.high} high[/] "
                f"[yellow]· {m.medium} medium[/] [green]· {m.low} low[/] "
                f"[cyan]· {m.info} info[/]",
                expand=False,
            )
        )
        return
    out.print(Panel(f"[bold]Agentic Code Review[/bold]  -  {review.repository}", expand=False))
    out.print(review.summary)
    if not review.findings:
        out.print("\n[green]No findings above the confidence threshold.[/green]")
    for finding in review.findings:
        color = {
            "critical": "red", "high": "red", "medium": "yellow",
            "low": "green", "info": "cyan",
        }.get(finding.severity.value, "white")
        loc = f"{finding.file_path}:{finding.start_line}" if finding.start_line else finding.file_path
        out.print(
            f"\n[{color}][bold]{finding.severity.value.upper()}[/bold][/{color}] "
            f"[bold]{finding.title}[/bold]  -  {loc}"
        )
        out.print(finding.description)
        if finding.evidence:
            out.print(f"[dim]Evidence: {finding.evidence}[/dim]")
    _print_meta(result)
    if result.agent_errors:
        out.print("\n[dim]Stage failures (review completed in degraded mode):[/dim]")
        for err in result.agent_errors:
            out.print(f"[dim]  - {err.stage}: {err.error_type}[/dim]")


def _print_meta(result: WorkflowResult) -> None:
    m = result.review.metrics
    table = Table(title="Summary", show_header=False)
    table.add_row("Critical", str(m.critical))
    table.add_row("High", str(m.high))
    table.add_row("Medium", str(m.medium))
    table.add_row("Low", str(m.low))
    table.add_row("Info", str(m.info))
    table.add_row("LLM calls", str(result.llm_call_count))
    table.add_row("Est. cost", f"${result.estimated_cost_usd:.4f}")
    table.add_row("Model", result.review.model)
    console.print(table)


def _print_metrics(metrics: dict, path: Path) -> None:
    rows = [to_table_row(m) for m in metrics.values()]
    console.print("\n" + format_results_table(rows))
    console.print(f"[dim]Artifacts written to {path}[/dim]")


def _publish(github: GitHubClient, repo: str, pr: int, result: WorkflowResult) -> None:
    review = result.review
    comments = []
    for finding in review.findings[:20]:
        if finding.file_path and finding.start_line:
            body = (
                f"**{finding.severity.value.upper()}**  -  {finding.title}\n\n"
                f"{finding.description}\n\n*Confidence {finding.confidence:.2f}*"
            )
            comments.append(
                ReviewComment(path=finding.file_path, line=finding.start_line, body=body)
            )
    try:
        github.create_review(
            repo,
            pr,
            commit_id=review.commit or "",
            body=review.to_markdown(),
            comments=comments,
        )
        console.print("[green]Review published to GitHub.[/green]")
    except ReviewerError as exc:
        console.print(f"[red]Publish failed: {exc}[/red]")
        raise typer.Exit(code=1) from None


def _parse_systems(value: str) -> list[str]:
    selected = [s.strip() for s in value.split(",") if s.strip()]
    unknown = [s for s in selected if s not in _SYSTEMS]
    if unknown:
        raise typer.BadParameter(f"unknown system(s): {', '.join(unknown)}; choose from {_SYSTEMS}")
    return selected


def _apply_overrides(settings: Settings, overrides: dict) -> Settings:
    """Map config keys to Settings fields (kebab/underscore tolerant)."""
    if not overrides:
        return settings
    updates: dict = {}
    for key, value in overrides.items():
        field = key.replace("-", "_").lower()
        if field in settings.model_fields:
            updates[field] = value
    return settings.model_copy(update=updates)


def _fail_on_option() -> str | None:
    """The shared ``--fail-on`` option definition."""
    return typer.Option(
        None,
        "--fail-on",
        help="Exit 1 when a finding at this severity or worse exists (CI quality gate). "
        "Any repo-level gate in .reviewer.yaml is used when the flag is absent.",
    )


def _gate_from_config(repo_config: RepoConfig) -> str | None:
    """Derive a ``--fail-on`` level from a repo config's quality gate.

    The flag is the explicit (per-run) choice; the config gate is the repo-wide
    policy. When the gate limits a severity bucket, the derived level is the
    lowest-severity bucket that has a limit, so any finding at or above it is
    counted. ``None`` when the config has no gate configured.
    """
    gate = repo_config.quality_gate
    if not gate.configured:
        return None
    for sev in ("info", "low", "medium", "high", "critical"):
        if getattr(gate, sev) is not None:
            return sev
    return None


def _finish_review(
    result: WorkflowResult,
    *,
    output: Path | None,
    fmt: str,
    fail_on: str | None,
    interactive_mode: bool = False,
    auto_apply: bool = False,
    local_path: str | None = None,
    repo_config: RepoConfig | None = None,
    notice: str = "",
    settings: Settings | None = None,
) -> None:
    """Render, optionally triage, then enforce the quality gate (shared tail)."""
    if repo_config is not None and config_notice(repo_config):
        err_console.print(f"[dim]{config_notice(repo_config)}[/dim]")
    result = _filtered_findings(result, repo_config or RepoConfig())
    # Explicit --fail-on wins; otherwise the .reviewer.yaml gate applies.
    effective_gate: str | None = fail_on
    if effective_gate is None and repo_config is not None:
        effective_gate = _gate_from_config(repo_config)
    _render(result, output, fmt=fmt)
    if notice:
        err_console.print(f"[dim]{notice}[/dim]")
    if interactive_mode or auto_apply:
        # Dismissals become repo memory: a dismissed finding is recorded as a
        # ``dismissed`` knowledge entry so future reviews suppress similar ones.
        dismissals = _dismissal_recorder(settings)
        actions = interactive.run_triage(
            result.review.findings,
            local_path=local_path,
            auto_apply=auto_apply,
            on_finding=dismissals,
        )
        if auto_apply and actions["apply"]:
            console.print(
                f"[green]{actions['apply']} fix(es) applied to the working tree.[/green]"
            )
    # Gate status always goes to stderr: ``--format json`` must stay clean on stdout.
    _enforce_gate(_gate_violation(result, effective_gate), effective_gate, err_console)
    RuntimeConfig.load().record_review_result(result)


def _dismissal_recorder(
    settings: Settings | None,
) -> Callable[[object, str], None] | None:
    """Build the ``on_finding`` triage callback that records dismissals.

    Returns None when the knowledgebase is disabled, so triage behavior is
    otherwise unchanged.
    """
    if settings is None or not settings.knowledge_enabled:
        return None

    def _record(finding: object, action: str) -> None:
        if action != "dismiss":
            return
        try:
            from agentic_code_reviewer.knowledge.dismiss import finding_to_dismissal_entry
            from agentic_code_reviewer.knowledge.store import KnowledgeStore
            from agentic_code_reviewer.models.findings import ReviewFinding

            if not isinstance(finding, ReviewFinding):
                return
            store = KnowledgeStore(settings.knowledge_dir or None)
            store.add(finding_to_dismissal_entry(finding, finding.repository))
        except Exception:  # noqa: BLE001 - knowledge must never break triage
            pass

    return _record


# ---------------------------------------------------------------------------
# Bare invocation (Claude-code style)
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", help="Show version and exit."),
) -> None:
    """``acr`` with no command opens the launcher menu on the current directory."""
    if version:
        console.print(f"acr {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        _launch_tui_home(".")


@tui_app.callback(invoke_without_command=True)
def _tui_root(ctx: typer.Context) -> None:
    """``acr tui`` with no subcommand behaves like bare ``acr``."""
    if ctx.invoked_subcommand is None:
        _launch_tui_home(".")


# ---------------------------------------------------------------------------
# Setup: init / completion / version
# ---------------------------------------------------------------------------

_INIT_TEMPLATE = """# Agentic Code Reviewer  -  repository policy
# Discovered from the nearest .reviewer.yaml (walks up from the reviewed path).

quality_gate:
  critical: 0      # fail when any critical finding exists
  high: 5          # fail when more than 5 high findings exist
  medium: 20
  low: null        # never fail on low / info
  info: null

ignore_patterns:
  - "**/test_*.py"
  - "**/tests/**"
  - "docs/**"
  - "*.lock"

severity_weights:
  critical: 1.0
  high: 0.8
  medium: 0.5
  low: 0.2
  info: 0.0

# Optional model overrides (any Settings field name works, kebab or underscore).
# max_tokens: 8192
# temperature: 0.2
# providers:
#   llm_provider: openai
#   openai_model: gpt-4o-mini
"""


@app.command("init")
def init(
    force: bool = typer.Option(False, "--force", help="Overwrite an existing .reviewer.yaml"),
) -> None:
    """Scaffold a .reviewer.yaml repository policy file."""
    target = Path(".reviewer.yaml")
    if target.exists() and not force:
        console.print(
            "[yellow].reviewer.yaml already exists  -  pass --force to overwrite.[/yellow]"
        )
        raise typer.Exit(code=1)
    target.write_text(_INIT_TEMPLATE, encoding="utf-8")
    console.print(f"[green]✔ Wrote {target}[/green]")
    console.print("  Run `acr review-local . --fail-on high` to enforce the gate in CI.")


@app.command("completion")
def completion(shell: str = typer.Argument(..., help="bash | zsh | fish | powershell")) -> None:
    """Print shell completion for the acr command."""
    from typer.completion import completion_init

    completion_init()
    import typer.completion as typer_completion

    if getattr(typer_completion.Shells, shell, None) is None:
        raise typer.BadParameter("shell must be one of: bash, zsh, fish, powershell, pwsh")
    console.print(
        typer_completion.get_completion_script(
            prog_name="acr",
            complete_var="_ACR_COMPLETE",
            shell=shell,
        )
    )


# ---------------------------------------------------------------------------
# Onboarding: get-started / providers / history / help
# ---------------------------------------------------------------------------

_PROVIDER_LABELS = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "gemini": "Google Gemini",
    "openai_compatible": "OpenAI-compatible endpoint (vLLM, LM Studio, Groq, DeepSeek…)",
    "ollama": "Ollama (local, no key)",
    "mock": "Mock (no key, test double)",
}


@app.command("get-started")
def get_started(
    provider: str | None = typer.Option(
        None, "--provider", help="openai | anthropic | gemini | openai-compatible | ollama | mock"
    ),
    api_key: str | None = typer.Option(None, "--api-key", help="API key for the provider (skips the prompt)"),
    base_url: str | None = typer.Option(None, "--base-url", help="Base URL (openai-compatible / ollama)"),
    model: str | None = typer.Option(None, "--model", help="Model name override"),
) -> None:
    """One-time interactive setup: pick a provider and store its API key.

    Credentials are saved to the user config and used by every later run.
    Environment variables still win when set.
    """
    from rich.prompt import Confirm, Prompt

    console.print(
        Panel(
            "[bold]Agentic Code Reviewer  -  setup[/bold]\n\n"
            "Answers are stored in your user config; no secrets file involved.\n"
            f"Config file: [cyan]{config_path()}[/cyan]",
            expand=False,
        )
    )
    if provider is not None:
        provider = provider.replace("-", "_").lower()
    if provider is not None and provider not in PROVIDER_FIELDS:
        raise typer.BadParameter(
            f"unknown provider {provider!r}; choose from {', '.join(PROVIDER_FIELDS)}"
        )
    if provider is None:
        console.print("\n[bold]Choose a provider:[/bold]")
        for key in PROVIDER_FIELDS:
            console.print(f"  [cyan]{key:20}[/] {_PROVIDER_LABELS[key]}")
        console.print(provider_help_text(), highlight=False)
        provider = Prompt.ask("Provider", default="openai").replace("-", "_").lower()
        while provider not in PROVIDER_FIELDS:
            console.print(f"[red]Unknown provider '{provider}'. Choose from {', '.join(PROVIDER_FIELDS)}.[/red]")
            provider = Prompt.ask("Provider", default="openai").replace("-", "_").lower()

    config = RuntimeConfig.load()
    values: dict[str, str] = {}

    if provider in ("openai", "anthropic", "gemini"):
        key_name = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}[provider]
        if api_key is None:
            console.print(f"\n[bold]Enter your {_PROVIDER_LABELS[provider]} API key[/bold]")
            api_key = Prompt.ask(key_name, password=True)
        values["api_key"] = api_key.strip() if api_key else ""
        if not values["api_key"]:
            raise typer.BadParameter(f"no API key provided for {provider}")
        if model is None:
            model_field = {
                "openai": "openai_model",
                "anthropic": "anthropic_model",
                "gemini": "gemini_model",
            }[provider]
            defaults = {
                "openai": "gpt-4o-mini",
                "anthropic": "claude-sonnet-4-5",
                "gemini": "gemini-2.5-flash",
            }
            default_model = str(config.get(model_field, "") or defaults[provider])
            model = Prompt.ask(
                "Model (press Enter to keep default)", default=default_model
            )
        values["model"] = model.strip()
    elif provider == "openai_compatible":
        if base_url is None:
            base_url = Prompt.ask(
                "Base URL (e.g. http://localhost:8000/v1)",
                default=str(config.get("openai_compatible_base_url", "")) or None,
            )
        if not base_url:
            raise typer.BadParameter("openai-compatible needs a base URL")
        values["base_url"] = base_url.strip()
        if api_key is None:
            api_key = Prompt.ask(
                "API key (optional  -  local servers usually ignore it)",
                password=True,
                default="",
            )
        values["api_key"] = (api_key or "").strip()
        if model is None:
            model = Prompt.ask(
                "Model",
                default=str(config.get("openai_compatible_model", "")) or None,
            )
        if model:
            values["model"] = model.strip()
    elif provider == "ollama":
        if base_url is None:
            base_url = Prompt.ask(
                "Ollama base URL",
                default=str(config.get("ollama_base_url", "http://localhost:11434")),
            )
        values["base_url"] = base_url.strip()
        if model is None:
            model = Prompt.ask(
                "Model (must be pulled locally)",
                default=str(config.get("ollama_model", "qwen2.5-coder:7b")),
            )
        values["model"] = model.strip()

    config.set_provider(provider, **values)
    config.save()
    console.print(f"[green]✔ Saved {_PROVIDER_LABELS[provider]} to {config.path}[/green]")
    if provider == "mock":
        console.print("  Mock needs no key  -  run `acr review-local .` to try it.")
    else:
        console.print("  Run `acr review-local .` to review the current change.")
    # Only offer to open the dashboard on a real interactive terminal; a piped
    # or CI stdin must not fail the setup that already succeeded.
    try:
        interactive_terminal = sys.stdin.isatty()
    except Exception:  # noqa: BLE001
        interactive_terminal = False
    if not interactive_terminal:
        return
    try:
        launch = Confirm.ask("\nOpen the interactive dashboard now?", default=False)
    except Exception:  # noqa: BLE001 - EOF on piped stdin
        launch = False
    if launch:
        _launch_tui_local(".", None, None)


@app.command("providers")
def providers() -> None:
    """Show the configured providers and which one is active."""
    settings = _settings()
    config = RuntimeConfig.load()
    active_key = settings.llm_provider.replace("-", "_")

    table = Table(title="Providers", show_header=True, header_style="bold #a78bfa", border_style="#2b2b45")
    table.add_column("Provider", width=22)
    table.add_column("Status", width=16)
    table.add_column("Model", ratio=2)
    table.add_column("Notes", ratio=3)
    for key in PROVIDER_FIELDS:
        stored = config.provider_configured(key)
        env_set = False
        creds = config.credentials(key)
        api_key = creds.get("api_key")
        for candidate in PROVIDER_FIELDS[key].values():
            if candidate in Settings.model_fields and getattr(settings, candidate, False):
                env_set = True
                break
        status = (
            "active"
            if key == active_key
            else ("environment" if env_set else ("configured" if stored else " - "))
        )
        style = "bold green" if key == active_key else ("cyan" if env_set or stored else "dim")
        status_text = {
            "active": "● active",
            "environment": "environment",
            "configured": "configured",
            " - ": " - ",
        }[status]
        model = ""
        for logical, field in PROVIDER_FIELDS[key].items():
            if logical == "model":
                model = str(getattr(settings, field, "") or "") or ""
        notes = _PROVIDER_LABELS[key]
        if api_key:
            notes += "  -  key stored"
        table.add_row(key, f"[{style}]{status_text}[/]", model, f"[dim]{notes}[/]")
    console.print(table)
    console.print(f"\n[dim]Config: {config.path}[/dim]")
    console.print("[dim]Run `acr get-started` to change the active provider.[/dim]")


@app.command("history")
def history(
    limit: int = typer.Option(15, "--limit", help="Number of recent reviews to show"),
    json_output: bool = typer.Option(False, "--json", help="Print entries as JSON"),
) -> None:
    """Show your recent reviews (stored locally, no server)."""
    config = RuntimeConfig.load()
    entries = config.load_history(limit=limit)
    if not entries:
        console.print("[dim]No reviews yet  -  run `acr review-local .` first.[/dim]")
        return
    if json_output:
        import json

        console.print(json.dumps(entries, indent=2))
        return
    table = Table(
        title=f"Review history  -  last {len(entries)}",
        show_header=True,
        header_style="bold #a78bfa",
        border_style="#2b2b45",
    )
    table.add_column("When", width=11)
    table.add_column("Repository", ratio=2)
    table.add_column("Source", width=9)
    table.add_column("Findings", width=12)
    table.add_column("Model", ratio=2)
    table.add_column("Calls", width=6)
    for e in reversed(entries):
        when = (e.get("ts") or "")[5:16].replace("T", " ")  # MM-DD HH:MM
        sev = " · ".join(
            f"{e.get(k, 0)}{k[0].upper()}" for k in ("critical", "high", "medium", "low", "info") if e.get(k, 0)
        ) or "0"
        table.add_row(
            when,
            str(e.get("repository", "?")),
            str(e.get("source", "?")),
            sev,
            str(e.get("model", "?") or "?"),
            str(e.get("llm_calls", "") or ""),
        )
    console.print(table)
    console.print(f"[dim]History file: {config_dir() / 'history.jsonl'}[/dim]")


@app.command("help")
def help_command() -> None:
    """Show the command reference (same as `acr --help`)."""
    from typer._click import Context

    cmd = typer.main.get_command(app)
    with Context(cmd, info_name="acr") as ctx:
        typer.echo(cmd.get_help(ctx))


# ---------------------------------------------------------------------------
# Review commands
# ---------------------------------------------------------------------------


@app.command()
def review(
    repo: str = typer.Option(..., "--repo", help="owner/repository"),
    pr: int = typer.Option(..., "--pr", help="Pull request number"),
    publish: bool = typer.Option(False, "--publish", help="Publish the review on GitHub"),
    output: Path | None = typer.Option(None, "--output", help="Write review to file"),
    requirement: str = typer.Option(
        "", "--requirement", help="Stated intent to review against (overrides the PR body)"
    ),
    fmt: str = typer.Option("markdown", "--format", help="Output format"),
    fail_on: str | None = _fail_on_option(),
) -> None:
    """Review a GitHub pull request (public repos work without a token)."""
    settings = _settings()
    setup_logging(settings.log_level, settings.log_format)
    github = GitHubClient(settings)
    try:
        request = request_from_pull_request(github, repo, pr, settings)
    finally:
        github.close()
    if requirement:
        request.description = requirement
    result = _run(request, settings)
    _finish_review(
        result,
        output=output,
        fmt=fmt,
        fail_on=fail_on,
        settings=settings,
    )
    if publish:
        _publish(github, repo, pr, result)


@app.command()
def review_commit(
    repo: str = typer.Option(..., "--repo", help="owner/repository"),
    commit: str = typer.Option(..., "--commit", help="Commit SHA"),
    output: Path | None = typer.Option(None, "--output", help="Write review to file"),
    requirement: str = typer.Option("", "--requirement", help="Stated intent to review against"),
    fmt: str = typer.Option("markdown", "--format", help="Output format"),
    fail_on: str | None = _fail_on_option(),
) -> None:
    """Review a single GitHub commit."""
    settings = _settings()
    setup_logging(settings.log_level, settings.log_format)
    github = GitHubClient(settings)
    try:
        request = request_from_commit(github, repo, commit, settings)
    finally:
        github.close()
    if requirement:
        request.description = requirement
    result = _run(request, settings)
    _finish_review(
        result, output=output, fmt=fmt, fail_on=fail_on, settings=settings
    )


@app.command()
def review_local(
    path: str = typer.Argument(".", help="Path to a local git repository"),
    base: str = typer.Option("HEAD", "--base", help="Base ref to diff against"),
    output: Path | None = typer.Option(None, "--output", help="Write review to file"),
    requirement: str = typer.Option(
        "", "--requirement", help="Stated intent to review the change against (e.g. a ticket description)"
    ),
    fmt: str = typer.Option("markdown", "--format", help="Output format"),
    fail_on: str | None = _fail_on_option(),
    interactive_mode: bool = typer.Option(
        False, "--interactive", "-i", help="Triage findings (apply/dismiss/copy/skip)"
    ),
    auto_apply: bool = typer.Option(False, "--apply", help="Auto-apply suggested fixes"),
) -> None:
    """Review local uncommitted changes in a git repository."""
    settings = _settings()
    settings, repo_config = _apply_repo_settings(path, settings)
    setup_logging(settings.log_level, settings.log_format)
    request = local_request(path, base=base)
    if requirement:
        request.description = requirement
    result = _run(request, settings)
    _finish_review(
        result,
        output=output,
        fmt=fmt,
        fail_on=fail_on,
        interactive_mode=interactive_mode,
        auto_apply=auto_apply,
        local_path=request.local_path,
        repo_config=repo_config,
        settings=settings,
    )


# ---------------------------------------------------------------------------
# Interactive TUI commands
# ---------------------------------------------------------------------------


def _tui_log_file() -> str:
    """Logs go to a temp file so they never corrupt the TUI screen."""
    return str(Path(tempfile.gettempdir()) / "acr_tui.log")


def _launch_tui_review(
    request: ReviewRequest,
    settings: Settings,
    output: Path | None,
    notice: str = "",
) -> None:
    from agentic_code_reviewer.ui import launch_reviewer

    setup_logging(settings.log_level, settings.log_format, log_file=_tui_log_file())
    launch_reviewer(settings, request, export_path=output, startup_notice=notice or None)


def _launch_tui_local(path: str, base: str | None, output: Path | None) -> None:
    """Build a local review request (auto-falling back to the last commit when
    the working tree is clean) and open the interactive dashboard on it."""
    settings = _settings()
    request, notice = local_request_with_fallback(path, base=base)
    _launch_tui_review(request, settings, output, notice=notice)


def _launch_tui_home(path: str = ".") -> None:
    """Open the launcher menu (Claude-Code style) on ``path``.

    From the menu the user can start a review of the path, configure
    providers / API keys (persisted to the user config), browse history or
    read the help reference.
    """
    from agentic_code_reviewer.ui import launch_home

    settings = _settings()
    setup_logging(settings.log_level, settings.log_format, log_file=_tui_log_file())
    launch_home(path, settings)


@tui_app.command("review")
def tui_review(
    repo: str = typer.Option(..., "--repo", help="owner/repository"),
    pr: int = typer.Option(..., "--pr", help="Pull request number"),
    output: Path | None = typer.Option(None, "--output", help="Write review markdown to file (export key)"),
    requirement: str = typer.Option(
        "", "--requirement", help="Stated intent to review against (overrides the PR body)"
    ),
) -> None:
    """Interactively review a GitHub pull request."""
    settings = _settings()
    github = GitHubClient(settings)
    try:
        request = request_from_pull_request(github, repo, pr, settings)
    finally:
        github.close()
    if requirement:
        request.description = requirement
    _launch_tui_review(request, settings, output)


@tui_app.command("review-commit")
def tui_review_commit(
    repo: str = typer.Option(..., "--repo", help="owner/repository"),
    commit: str = typer.Option(..., "--commit", help="Commit SHA"),
    output: Path | None = typer.Option(None, "--output", help="Write review markdown to file (export key)"),
    requirement: str = typer.Option("", "--requirement", help="Stated intent to review against"),
) -> None:
    """Interactively review a single GitHub commit."""
    settings = _settings()
    github = GitHubClient(settings)
    try:
        request = request_from_commit(github, repo, commit, settings)
    finally:
        github.close()
    if requirement:
        request.description = requirement
    _launch_tui_review(request, settings, output)


@tui_app.command("review-local")
def tui_review_local(
    path: str = typer.Argument(..., help="Path to a local git repository"),
    base: str = typer.Option("HEAD", "--base", help="Base ref to diff against"),
    output: Path | None = typer.Option(None, "--output", help="Write review markdown to file (export key)"),
    requirement: str = typer.Option(
        "", "--requirement", help="Stated intent to review the change against (e.g. a ticket description)"
    ),
) -> None:
    """Interactively review local uncommitted changes."""
    settings = _settings()
    request = local_request(path, base=base)
    if requirement:
        request.description = requirement
    _launch_tui_review(request, settings, output)


@tui_app.command("benchmark")
def tui_benchmark(
    dataset: Path = typer.Option(..., "--dataset", help="Benchmark dataset (JSON/JSONL)"),
    systems: str = typer.Option("single-pass,context,rag,agentic", "--systems"),
    limit: int | None = typer.Option(None, "--limit", help="Only the first N entries"),
    provider: str = typer.Option(
        "",
        "--provider",
        help="Override LLM_PROVIDER (openai | anthropic | ollama | gemini | openai-compatible | mock)",
    ),
) -> None:
    """Interactively run baseline + agentic systems over a benchmark dataset."""
    settings = _settings()
    if provider:
        settings = settings.model_copy(update={"llm_provider": provider})
    setup_logging(settings.log_level, settings.log_format, log_file=_tui_log_file())
    from agentic_code_reviewer.ui import launch_benchmark

    launch_benchmark(settings, dataset, _parse_systems(systems), limit=limit)


# ---------------------------------------------------------------------------
# Benchmark / experiment commands
# ---------------------------------------------------------------------------


@app.command()
def benchmark(
    dataset: Path = typer.Option(..., "--dataset", help="Benchmark dataset (JSON/JSONL)"),
    systems: str = typer.Option("single-pass,context,rag,agentic", "--systems"),
    limit: int | None = typer.Option(None, "--limit", help="Only the first N entries"),
    provider: str = typer.Option(
        "",
        "--provider",
        help="Override LLM_PROVIDER (openai | anthropic | ollama | gemini | openai-compatible | mock)",
    ),
) -> None:
    """Run baseline + agentic systems over a benchmark dataset."""
    settings = _settings()
    if provider:
        settings = settings.model_copy(update={"llm_provider": provider})
    setup_logging(settings.log_level, settings.log_format)
    selected = _parse_systems(systems)
    entries = BenchmarkLoader.load(dataset)
    tracker = ExperimentTracker("benchmark", root="benchmarks/results")
    tracker.save_config({"dataset": str(dataset), "systems": selected, "limit": limit})
    metrics = run_benchmark(entries, selected, settings, tracker, limit=limit)
    _print_metrics(metrics, tracker.path)


@app.command()
def evaluate(
    config: Path = typer.Option(..., "--config", help="Experiment YAML configuration"),
    name: str | None = typer.Option(None, "--name", help="Experiment name override"),
) -> None:
    """Run a reproducible experiment defined by a YAML config."""
    import yaml

    if not config.exists():
        raise typer.BadParameter(f"config not found: {config}")
    cfg = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    settings = _settings()
    overrides = cfg.get("settings", {})
    settings = _apply_overrides(settings, overrides)
    setup_logging(settings.log_level, settings.log_format)

    dataset_path = cfg.get("dataset") or settings.benchmark_dataset
    raw_systems = cfg.get("systems", "single-pass,context,rag,agentic")
    systems_value = ",".join(raw_systems) if isinstance(raw_systems, list) else raw_systems
    systems = _parse_systems(systems_value)
    limit = cfg.get("limit")
    tracker = ExperimentTracker(name or cfg.get("name", "experiment"), root=cfg.get("root", "experiments"))
    tracker.save_config({"config_file": str(config), "dataset": str(dataset_path), "systems": systems})
    entries = BenchmarkLoader.load(str(dataset_path))
    metrics = run_benchmark(
        entries, systems, settings, tracker, limit=limit, summary=cfg.get("notes", "")
    )
    _print_metrics(metrics, tracker.path)


# ---------------------------------------------------------------------------
def _ensure_utf8_stdio() -> None:
    """Force UTF-8 on stdout/stderr so non-ASCII output never crashes the CLI.

    Python on Windows defaults to the console code page (cp1252), which cannot
    encode  - , ⚡, ✔, emoji or CJK. The reviewer's findings routinely contain
    all of those. Textual reconfigures the console itself, so this guard only
    runs for the non-TUI path.
    """
    import io

    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        try:
            encoding = getattr(stream, "encoding", None)
            buffer = getattr(stream, "buffer", None)
        except Exception:  # noqa: BLE001 - defensive
            continue
        if encoding is None or encoding.lower().replace("-", "") == "utf8":
            continue
        if buffer is None:
            continue
        try:
            setattr(sys, name, io.TextIOWrapper(buffer, encoding="utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            continue


def _dispatch_bare_path(first: str) -> bool:
    """Handle claude-code-style `acr <path>` before Typer subcommand dispatch.

    Typer/Click groups can't have both a positional path argument and working
    subcommand dispatch (the path swallows ``acr review-local .``). So the
    bare path case is intercepted here: if the first token is not a known
    command and not an option, treat it as the repo path for the dashboard.
    Returns True when the dashboard was launched (caller should return).
    """
    if first.startswith("-"):
        return False
    cmd = typer.main.get_command(app)
    if first in getattr(cmd, "commands", {}):
        return False
    _launch_tui_home(first)
    return True


def main() -> None:
    _ensure_utf8_stdio()
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_format)
    first = sys.argv[1] if len(sys.argv) > 1 else None
    if first is not None and not first.startswith("-"):
        try:
            if _dispatch_bare_path(first):
                return
        except ReviewerError as exc:
            console.print(f"[red]Error: {exc}[/red]")
            sys.exit(1)
    try:
        app()
    except ReviewerError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
