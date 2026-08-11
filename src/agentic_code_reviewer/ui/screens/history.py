"""History screen: recent reviews stored locally in ``history.jsonl``.

Shows a table of past reviews (when, repository, source, severity counts,
model, LLM calls) with a detail pane for the highlighted row. Nothing leaves
the machine  -  history is an append-only local file in the user config dir.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Markdown, Static

from agentic_code_reviewer.config.runtime_config import RuntimeConfig
from agentic_code_reviewer.ui.theme import (
    BRAND_ACCENT_2,
    BRAND_BORDER,
    BRAND_PANEL,
    BRAND_SURFACE,
    BRAND_TEXT,
    BRAND_TEXT_DIM,
)
from agentic_code_reviewer.ui.widgets import AppHeader

_CSS = f"""
#history-header {{
    height: 3;
    padding: 0 1;
    background: {BRAND_SURFACE};
    border-bottom: solid {BRAND_BORDER};
}}
#history-title {{ height: 1; text-style: bold; color: {BRAND_TEXT}; }}
#history-sub {{ height: 1; color: {BRAND_TEXT_DIM}; }}
#history-table {{ height: 1fr; border: none; border-right: solid {BRAND_BORDER}; }}
#history-detail {{ width: 46%; border-left: solid {BRAND_BORDER}; }}
#history-detail-scroll {{ height: 1fr; padding: 0 1; }}
#history-empty {{ padding: 2 1; color: {BRAND_TEXT_DIM}; }}
.pane-title {{
    height: 1;
    background: {BRAND_PANEL};
    color: {BRAND_ACCENT_2};
    text-style: bold;
    padding: 0 1;
}}
"""


class HistoryScreen(Screen):
    """Browse the local review history."""

    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
        Binding("q", "back", "Back"),
        Binding("escape", "back", "Back", show=False),
    ]

    DEFAULT_CSS = _CSS

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._entries: list[dict] = []
        self._rows: dict[str, dict] = {}

    def compose(self) -> ComposeResult:
        yield AppHeader(title="Agentic Code Reviewer", subtitle="review history")
        yield Static("", id="history-header")
        with Horizontal():
            with Vertical():
                yield Static("◆ RECENT REVIEWS", classes="pane-title")
                yield DataTable(id="history-table", cursor_type="row", zebra_stripes=True)
            with Vertical(id="history-detail"):
                yield Static("◆ DETAIL", classes="pane-title")
                with VerticalScroll(id="history-detail-scroll"):
                    yield Static("Select a review to see its summary.", id="history-empty")
        # ``show_command_palette=False``: Textual's footer renders the palette
        # key a SECOND time (right side) when it is also bound  -  disable that.
        yield Footer(show_command_palette=False)

    def on_mount(self) -> None:
        self._load()
        table = self.query_one("#history-table", DataTable)
        if table.row_count:
            table.focus()
            table.move_cursor(row=0)

    def _load(self) -> None:
        self._entries = RuntimeConfig.load().load_history(limit=100)
        table = self.query_one("#history-table", DataTable)
        table.clear(columns=True)
        table.add_columns("When", "Repository", "Source", "Findings", "Model", "Calls")
        self._rows.clear()
        if not self._entries:
            table.add_row(
                Text("No reviews yet  -  run a review from the launcher.", style="dim")
            )
        for idx, e in enumerate(reversed(self._entries)):
            when = (e.get("ts") or "")[5:16].replace("T", " ")
            sev = " · ".join(
                f"{e.get(k, 0)}{k[0].upper()}"
                for k in ("critical", "high", "medium", "low", "info")
                if e.get(k, 0)
            ) or "0"
            row_key = table.add_row(
                when,
                str(e.get("repository", "?")),
                str(e.get("source", "?")),
                sev,
                str(e.get("model", "?") or "?"),
                str(e.get("llm_calls", "") or ""),
                key=f"hist-{idx}",
            )
            self._rows[str(row_key.value)] = e
        header = self.query_one("#history-header", Static)
        header.update(
            f"HISTORY  -  {len(self._entries)} review(s)\n"
            "[dim]stored locally in your user config · r refreshes[/dim]"
        )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        entry = self._rows.get(str(event.row_key.value))
        if entry is None:
            return
        scroll = self.query_one("#history-detail-scroll", VerticalScroll)
        scroll.remove_children()
        scroll.mount(Markdown(self._entry_markdown(entry)))

    def _entry_markdown(self, e: dict) -> str:
        sev = " · ".join(
            f"{e.get(k, 0)} {k}"
            for k in ("critical", "high", "medium", "low", "info")
            if e.get(k, 0)
        ) or "none"
        lines = [
            f"## {e.get('repository', '?')}",
            "",
            f"**When:** `{e.get('ts', '?')}`  ·  **Source:** {e.get('source', '?')}",
            "",
            f"**Findings:** {sev}",
            "",
            f"**Model:** {e.get('model', '?')}",
            f"**LLM calls:** {e.get('llm_calls', '?')}",
            f"**Est. cost:** ${e.get('cost_usd', 0)}",
        ]
        if e.get("commit"):
            lines += ["", f"**Commit:** `{e.get('commit')}`"]
        if e.get("pull_request"):
            lines += ["", f"**Pull request:** #{e.get('pull_request')}"]
        if e.get("review_id"):
            lines += ["", f"*review id: {e.get('review_id')}*"]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def action_refresh(self) -> None:
        self._load()
        self.app.notify("History refreshed", severity="information")

    def action_back(self) -> None:
        self.app.pop_screen()
