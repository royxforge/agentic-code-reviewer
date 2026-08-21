"""Knowledgebase CLI commands.

    acr knowledge add --title ... --content ... [--repo o/r] [--category security]
    acr knowledge list [--repo o/r]
    acr knowledge remove <entry-id>
    acr knowledge search "<query>" [--repo o/r] [--top-k 5]
    acr knowledge index-docs <path> [--repo o/r]

The knowledgebase is persistent, cross-run knowledge injected into analysis
prompts: curated rules/conventions, auto-captured verified findings (repo
memory), and indexed documentation. All entries live as JSONL under the user
config dir (``config_dir()/knowledge/``) and are retrieved with deterministic
token overlap - no LLM call, no extra tokens.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from agentic_code_reviewer.knowledge.models import KnowledgeEntry, KnowledgeKind
from agentic_code_reviewer.knowledge.store import KnowledgeStore

console = Console(highlight=False)

knowledge_app = typer.Typer(
    name="knowledge",
    help=(
        "Manage the knowledgebase: curated rules, auto-captured findings and "
        "indexed docs injected into analysis prompts."
    ),
    no_args_is_help=True,
    short_help="Knowledgebase management",
)


def _store() -> KnowledgeStore:
    from agentic_code_reviewer.config.runtime_config import apply_runtime_config
    from agentic_code_reviewer.config.settings import Settings, get_settings

    settings: Settings = apply_runtime_config(get_settings())
    return KnowledgeStore(settings.knowledge_dir or None)


def _kind(value: str) -> KnowledgeKind:
    try:
        return KnowledgeKind(value.strip().lower())
    except ValueError:
        raise typer.BadParameter(
            f"unknown kind {value!r}; choose from rule, finding, doc"
        ) from None


@knowledge_app.command("add")
def add(
    title: str = typer.Option(..., "--title", help="Short title of the entry"),
    content: str = typer.Option(..., "--content", help="Body of the entry"),
    repo: str = typer.Option("", "--repo", help="Repository the entry applies to (empty = all)"),
    kind: str = typer.Option("rule", "--kind", help="rule | finding | doc"),
    category: str = typer.Option("", "--category", help="Review category (e.g. security)"),
    tags: str = typer.Option("", "--tags", help="Comma-separated tags"),
) -> None:
    """Add a curated rule / convention / decision to the knowledgebase."""
    store = _store()
    entry = KnowledgeEntry(
        kind=_kind(kind),
        title=title.strip(),
        content=content.strip(),
        repository=repo.strip(),
        category=category.strip().lower(),
        source="manual",
        tags=[t.strip() for t in tags.split(",") if t.strip()],
    )
    store.add(entry)
    console.print(
        f"[green]✔ Added knowledge entry {entry.entry_id()}[/green]"
        f"[dim] ({entry.kind.value}, repo={entry.repository or 'all'})[/dim]"
    )
    console.print("  It will be injected into analysis prompts for relevant reviews.")


@knowledge_app.command("list")
def list_entries(
    repo: str = typer.Option("", "--repo", help="Filter by repository (empty = all)"),
    kind: str = typer.Option("", "--kind", help="rule | finding | doc"),
) -> None:
    """List knowledgebase entries."""
    store = _store()
    entries = store.entries(repo.strip())
    if kind:
        try:
            wanted = _kind(kind)
        except typer.BadParameter:
            wanted = None
        entries = [e for e in entries if wanted is None or e.kind == wanted]
    if not entries:
        console.print("[dim]No knowledge entries found.[/dim]")
        return
    table = Table(title=f"Knowledgebase  -  {len(entries)} entries", show_header=True, border_style="#2b2b45")
    table.add_column("ID", width=16)
    table.add_column("Kind", width=8)
    table.add_column("Repo", width=22)
    table.add_column("Category", width=16)
    table.add_column("Title", ratio=3)
    for e in entries:
        table.add_row(
            e.entry_id(),
            e.kind.value,
            e.repository or "*",
            e.category or "-",
            e.title,
        )
    console.print(table)
    console.print(f"[dim]Store: {store.directory}[/dim]")


@knowledge_app.command("remove")
def remove(entry_id: str = typer.Argument(..., help="Entry id (from `acr knowledge list`)")) -> None:
    """Remove a knowledgebase entry."""
    store = _store()
    removed_any = False
    # The id is content-derived, so scan every repo file for it.
    for path in store.directory.glob("*.jsonl"):
        repo = path.stem
        if repo == "_global":
            repo = ""
        if store.remove(repo, entry_id.strip()):
            removed_any = True
    if removed_any:
        console.print(f"[green]✔ Removed knowledge entry {entry_id}[/green]")
    else:
        console.print(f"[yellow]No entry with id {entry_id} found.[/yellow]")
        raise typer.Exit(code=1)


@knowledge_app.command("search")
def search(
    query: str = typer.Argument(..., help="Search text (tokens are matched against titles + content)"),
    repo: str = typer.Option("", "--repo", help="Repository to search (empty = all)"),
    top_k: int = typer.Option(5, "--top-k", help="Maximum results"),
) -> None:
    """Search the knowledgebase (deterministic token overlap, no LLM)."""
    store = _store()
    entries = store.search(query, repo.strip(), top_k=top_k)
    if not entries:
        console.print("[dim]No matching knowledge entries.[/dim]")
        return
    for e in entries:
        console.print(f"[bold cyan]{e.entry_id()}[/bold cyan]  [{e.kind.value}] {e.title}")
        console.print(f"  [dim]repo={e.repository or '*'}, category={e.category or '-'}[/dim]")
        console.print(f"  {e.content.replace(chr(10), ' ')[:240]}")
        console.print("")


@knowledge_app.command("index-docs")
def index_docs(
    path: str = typer.Argument(..., help="File or directory of markdown docs to index"),
    repo: str = typer.Option("", "--repo", help="Repository these docs belong to"),
) -> None:
    """Index markdown docs (docs/, CHANGELOG, README) into the knowledgebase.

    Each markdown heading becomes one doc entry; the content is the section
    under it, so retrieval returns whole, meaningful units.
    """
    root = Path(path)
    if not root.exists():
        raise typer.BadParameter(f"path not found: {root}")
    files = [root] if root.is_file() else sorted(root.rglob("*.md")) + sorted(root.rglob("*.rst"))
    store = _store()
    added = 0
    for file_path in files:
        try:
            text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        sections = _split_markdown_sections(text)
        for heading, body in sections:
            entry = KnowledgeEntry(
                kind=KnowledgeKind.DOC,
                title=heading,
                content=body.strip(),
                repository=repo.strip(),
                source_file=str(file_path),
                source="docs",
                tags=["docs"],
            )
            before = len(store.entries(repo.strip()))
            store.add(entry)
            if len(store.entries(repo.strip())) > before:
                added += 1
    console.print(f"[green]✔ Indexed {added} doc section(s) from {len(files)} file(s)[/green]")
    console.print(f"  Store: {store.directory}")


def _split_markdown_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown into (heading, body) pairs at '#' headings."""
    sections: list[tuple[str, str]] = []
    current_heading = ""
    current_body: list[str] = []
    for line in text.splitlines():
        if line.startswith("#"):
            if current_heading:
                sections.append((current_heading, "\n".join(current_body)))
            current_heading = line.lstrip("#").strip()
            current_body = []
        elif current_heading:
            current_body.append(line)
    if current_heading:
        sections.append((current_heading, "\n".join(current_body)))
    return [(h, b) for h, b in sections if h and b.strip()]
