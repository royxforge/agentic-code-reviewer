"""Results screen: severity-pill summary, findings table with per-category
colours, and a detail card with evidence / impact / recommendation."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Markdown, Static

from agentic_code_reviewer.models.findings import ReviewFinding
from agentic_code_reviewer.models.schemas import WorkflowResult
from agentic_code_reviewer.ui.theme import (
    BRAND_ACCENT_2,
    BRAND_BORDER,
    BRAND_PANEL,
    BRAND_SURFACE,
    BRAND_TEXT,
    BRAND_TEXT_DIM,
    CATEGORY_COLOR,
    VERIFICATION_COLOR,
    severity_pill,
    severity_text,
)
from agentic_code_reviewer.ui.widgets import AppHeader


class ResultsScreen(Screen):
    """Browses the final review: findings table on the left, details on the right.

    Keybindings:
      ↑/↓ or j/k  move the cursor
      e           export the review as Markdown
      q           quit
    """

    # NOTE: actions not defined on this screen must use the ``app.`` namespace
    #  -  in Textual 8.2.8 a screen binding only resolves actions on the screen
    # itself (``q`` → ``quit`` silently did nothing before this fix).
    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("c", "categories", "Categories"),
        Binding("e", "export", "Export Markdown"),
        Binding("h", "app.go_home", "Home"),
        Binding("q", "back_or_quit", "Back"),
        Binding("escape", "back_or_quit", "Back", show=False),
        Binding("ctrl+p", "app.command_palette", "Command Palette"),
    ]

    def action_back_or_quit(self) -> None:
        """Return to the launcher from a review started there; quit otherwise."""
        if len(self.app.screen_stack) > 1:
            self.app.pop_screen()
        else:
            self.app.exit()

    # Vim-style navigation: forward to the focused widget (the DataTable's
    # own ``cursor_down`` / ``cursor_up`` actions).
    def action_cursor_down(self) -> None:
        focused = self.app.focused
        if focused is not None and hasattr(focused, "action_cursor_down"):
            focused.action_cursor_down()

    def action_cursor_up(self) -> None:
        focused = self.app.focused
        if focused is not None and hasattr(focused, "action_cursor_up"):
            focused.action_cursor_up()

    DEFAULT_CSS = f"""
    #results-header {{
        height: 5;
        padding: 0 1;
        background: {BRAND_SURFACE};
        border-bottom: solid {BRAND_BORDER};
        border-top: solid {BRAND_BORDER};
    }}
    #results-title {{ height: 1; text-style: bold; color: {BRAND_TEXT}; }}
    #results-pills {{ height: 1; }}
    #results-meta {{ height: 1; color: {BRAND_TEXT_DIM}; }}
    #findings-table {{ height: 1fr; border: none; border-right: solid {BRAND_BORDER}; }}
    #detail-pane {{ width: 46%; border-left: solid {BRAND_BORDER}; }}
    #detail-badge {{ height: 1; padding: 0 1; }}
    #detail-scroll {{ height: 1fr; padding: 0 1; }}
    #detail-empty {{ padding: 1; color: {BRAND_TEXT_DIM}; }}
    .pane-title {{
        height: 1;
        background: {BRAND_PANEL};
        color: {BRAND_ACCENT_2};
        text-style: bold;
        padding: 0 1;
    }}
    """

    def __init__(
        self,
        result: WorkflowResult,
        export_path: Path | None = None,
        only_category: str | None = None,
        filtered: list[ReviewFinding] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.result = result
        self.export_path = export_path
        self.only_category = only_category
        self._findings = filtered if filtered is not None else result.review.findings
        self._rows: dict[str, ReviewFinding] = {}

    def compose(self) -> ComposeResult:
        title = (
            f"{self.result.review.repository} · {self.only_category} findings"
            if self.only_category
            else f"{self.result.review.repository} · review complete"
        )
        yield AppHeader(
            title="Agentic Code Reviewer",
            subtitle=title,
        )
        yield Static("", id="results-header")
        with Horizontal():
            with Vertical():
                yield Static("◆ FINDINGS", classes="pane-title")
                yield DataTable(
                    id="findings-table", cursor_type="row", zebra_stripes=True
                )
            with Vertical(id="detail-pane"):
                yield Static("◆ DETAIL", classes="pane-title")
                yield Static("", id="detail-badge")
                with VerticalScroll(id="detail-scroll"):
                    yield Static(
                        "Select a finding to see evidence, impact and recommendation.",
                        id="detail-empty",
                    )
        # ``show_command_palette=False``: Textual's footer renders the palette
        # key a SECOND time (right side) when it is also bound  -  disable that.
        yield Footer(show_command_palette=False)

    def on_mount(self) -> None:
        table = self.query_one("#findings-table", DataTable)
        table.add_columns(
            "Severity", "Category", "Location", "Finding", "Confidence", "Verified"
        )
        # Row keys must be unique  -  index-based, never derived from finding text
        # (duplicate titles would silently overwrite the detail mapping).
        for idx, finding in enumerate(self._findings):
            row_key = table.add_row(
                severity_text(finding.severity.value),
                Text(
                    finding.category,
                    style=CATEGORY_COLOR.get(finding.category, BRAND_TEXT_DIM),
                ),
                Text(
                    f"{finding.file_path}:{finding.start_line}"
                    if finding.start_line
                    else finding.file_path,
                    style="#8b949e",
                ),
                Text(finding.title, style=f"bold {BRAND_TEXT}"),
                Text(f"{finding.confidence:.2f}", style="#d29922"),
                Text(
                    finding.verification_status.value,
                    style=VERIFICATION_COLOR.get(finding.verification_status, "dim"),
                ),
                key=f"finding-{idx}",
            )
            self._rows[str(row_key.value)] = finding
        self._render_header()
        if self._rows:
            table.focus()
            table.move_cursor(row=0)

    def _render_header(self) -> None:
        m = self.result.review.metrics
        pills = "  ".join(
            severity_pill(name, getattr(m, name))
            for name in ("critical", "high", "medium", "low", "info")
        )
        verified = sum(
            1 for f in self._findings if f.evidence_status.value == "confirmed"
        )
        meta = (
            f"[dim]model[/] [bold {BRAND_TEXT}]{self.result.review.model}[/]  "
            f"[dim]calls[/] {self.result.llm_call_count}  "
            f"[dim]cost[/] ${self.result.estimated_cost_usd:.4f}  "
            f"[dim]tokens[/] {sum(self.result.token_usage.values()):,}  "
            f"[dim]verified[/] {verified}/{len(self._findings)}"
        )
        title = (
            f"REVIEW  -  {self.only_category}  -  {self.result.review.repository}"
            if self.only_category
            else f"REVIEW COMPLETE  -  {self.result.review.repository}"
        )
        self.query_one("#results-header", Static).update(f"{title}\n{pills}\n{meta}")

    # ------------------------------------------------------------------
    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._show_detail(event.row_key)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._show_detail(event.row_key)

    def _show_detail(self, row_key) -> None:
        finding = self._rows.get(str(row_key.value))
        if finding is None:
            return
        loc = (
            f"{finding.file_path}:{finding.start_line}"
            if finding.start_line
            else finding.file_path
        )
        self.query_one("#detail-badge", Static).update(
            severity_pill(finding.severity.value)
            + "  "
            + f"[dim]{finding.category}[/]  [dim]{loc}[/dim]"
        )
        scroll = self.query_one("#detail-scroll", VerticalScroll)
        scroll.remove_children()
        scroll.mount(Markdown(self._finding_markdown(finding)))

    def _finding_markdown(self, f: ReviewFinding) -> str:
        from agentic_code_reviewer.ui.theme import SEVERITY_ICON

        icon = SEVERITY_ICON.get(f.severity, "")
        lines = [
            f"## {icon} {f.title}",
            "",
            f"**Confidence:** {f.confidence:.2f} · "
            f"**Evidence:** {f.evidence_status.value}",
            "",
            f.description,
        ]
        if f.evidence:
            lines += ["", "**Evidence:**", "", f"```\n{f.evidence}\n```"]
        if f.impact:
            lines += ["", "**Impact:**", "", f.impact]
        if f.recommendation:
            lines += ["", "**Recommendation:**", "", f.recommendation]
        if f.related_files:
            lines += ["", "**Related files:** " + ", ".join(f.related_files)]
        if f.rule_id:
            lines += ["", f"*rule: {f.rule_id}*"]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def action_export(self) -> None:
        path = self.export_path or Path("review.md")
        path.write_text(self.result.review.to_markdown(), encoding="utf-8")
        self.app.notify(f"Review exported to {path}", title="Export", severity="information")

    def action_categories(self) -> None:
        from agentic_code_reviewer.ui.screens.categories import CategoriesScreen

        self.app.switch_screen(CategoriesScreen(self.result, self.export_path))
