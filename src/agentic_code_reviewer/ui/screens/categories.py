"""Categories screen: findings grouped by review category.

Left pane: every review category from ``ALLOWED_CHECKS`` (23 in total,
including authorization, reliability, architecture, compatibility,
configuration and resource lifecycle) with live severity chips and a total
count. Right pane: the findings for the currently highlighted category.
``enter`` drills into a filtered findings view for that category;
``backspace`` returns from the drill-down.
"""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from agentic_code_reviewer.models.findings import ReviewFinding, Severity
from agentic_code_reviewer.models.review import ALLOWED_CHECKS
from agentic_code_reviewer.models.schemas import WorkflowResult
from agentic_code_reviewer.ui.theme import (
    BRAND_ACCENT_2,
    BRAND_BORDER,
    BRAND_PANEL,
    BRAND_SURFACE,
    BRAND_TEXT,
    BRAND_TEXT_DIM,
    CATEGORY_COLOR,
    SEVERITY_COLOR,
    VERIFICATION_COLOR,
    severity_text,
)

_SEVERITIES = (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO)


class CategoriesScreen(Screen):
    """Browse findings grouped by review category."""

    # ``app.``-prefixed actions are required for app-level actions in Textual
    # 8.2.8 (screen bindings only resolve on the screen itself).
    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("enter", "drill_down", "Filter category"),
        Binding("backspace", "drill_up", "Back"),
        Binding("e", "export", "Export Markdown"),
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

    def action_cursor_down(self) -> None:
        focused = self.app.focused
        if focused is not None and hasattr(focused, "action_cursor_down"):
            focused.action_cursor_down()

    def action_cursor_up(self) -> None:
        focused = self.app.focused
        if focused is not None and hasattr(focused, "action_cursor_up"):
            focused.action_cursor_up()

    DEFAULT_CSS = f"""
    #categories-header {{
        height: 3;
        padding: 0 1;
        background: {BRAND_SURFACE};
        border-bottom: solid {BRAND_BORDER};
    }}
    #categories-title {{ height: 1; text-style: bold; color: {BRAND_TEXT}; }}
    #categories-sub {{ height: 1; color: {BRAND_TEXT_DIM}; }}
    #category-pane {{ width: 34; }}
    #category-table {{ height: 1fr; border: none; border-right: solid {BRAND_BORDER}; }}
    #category-findings-pane {{ width: 3fr; border-left: solid {BRAND_BORDER}; }}
    #category-detail-pane {{ width: 2fr; border-left: solid {BRAND_BORDER}; }}
    #category-empty {{ padding: 1; color: {BRAND_TEXT_DIM}; }}
    #category-findings {{ height: 1fr; }}
    #category-detail {{ height: 1fr; }}
    .pane-title {{
        height: 1;
        background: {BRAND_PANEL};
        color: {BRAND_ACCENT_2};
        text-style: bold;
        padding: 0 1;
    }}
    #category-detail-scroll {{ height: 1fr; padding: 0 1; }}
    """

    def __init__(
        self,
        result: WorkflowResult,
        export_path: Path | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.result = result
        self.export_path = export_path
        self._findings = result.review.findings
        self._rows: dict[str, ReviewFinding] = {}
        # row index -> category, in table order (keys are stable per row).
        self._row_categories: dict[int, str] = {}

    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static("", id="categories-header")
        with Horizontal():
            with Vertical(id="category-pane"):
                yield Static("◆ CATEGORIES", classes="pane-title")
                yield DataTable(
                    id="category-table", cursor_type="row", zebra_stripes=True
                )
            with Vertical(id="category-findings-pane"):
                yield Static("◆ FINDINGS", classes="pane-title")
                yield DataTable(
                    id="category-findings",
                    cursor_type="row",
                    zebra_stripes=True,
                    show_header=True,
                )
            with Vertical(id="category-detail-pane"):
                yield Static("◆ DETAIL", classes="pane-title")
                with VerticalScroll(id="category-detail-scroll"):
                    yield Static(
                        "Select a finding to see evidence, impact and recommendation.",
                        id="category-empty",
                    )
        # ``show_command_palette=False``: Textual's footer renders the palette
        # key a SECOND time (right side) when it is also bound  -  disable that.
        yield Footer(show_command_palette=False)

    def on_mount(self) -> None:
        table = self.query_one("#category-table", DataTable)
        table.add_columns("Category", "Critical", "High", "Med", "Low", "Info", "Total")
        by_category: dict[str, list[ReviewFinding]] = {}
        for finding in self._findings:
            by_category.setdefault(finding.category, []).append(finding)

        for category in ALLOWED_CHECKS:
            findings = by_category.get(category, [])
            counts = {sev: sum(1 for f in findings if f.severity == sev) for sev in _SEVERITIES}
            total = len(findings)
            table.add_row(
                Text(
                    category,
                    style=CATEGORY_COLOR.get(category, BRAND_TEXT_DIM),
                ),
                *[
                    Text(str(counts[sev]), style=_chip(counts[sev], sev))
                    for sev in _SEVERITIES
                ],
                Text(str(total), style=f"bold {BRAND_TEXT}" if total else "dim"),
                key=f"cat-{category}",
            )
            self._row_categories[table.row_count - 1] = category
        # Categories outside ALLOWED_CHECKS (defensive: LLM output is validated,
        # but never crash the browser for an unexpected category).
        for category in by_category:
            if category not in ALLOWED_CHECKS:
                findings = by_category[category]
                table.add_row(
                    Text(category, style=BRAND_TEXT_DIM),
                    *[Text("", style="dim") for _ in _SEVERITIES],
                    Text(str(len(findings)), style="dim"),
                    key=f"cat-{category}",
                )
                self._row_categories[table.row_count - 1] = category

        self._render_header()
        if table.row_count:
            table.focus()
            table.move_cursor(row=0)
            first = self._row_categories.get(0)
            if first:
                self._show_category_by_name(first)

    def _render_header(self) -> None:
        total = len(self._findings)
        self.query_one("#categories-header", Static).update(
            f"CATEGORIES  -  {self.result.review.repository}\n"
            f"[dim]{len(self._row_categories)} categories · {total} findings  -  "
            f"enter filters a category, backspace returns[/dim]"
        )

    # ------------------------------------------------------------------
    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "category-table":
            self._drill_into(event.cursor_row)
        else:
            self._show_finding_detail(event.row_key)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "category-table":
            self._show_category_by_index(event.cursor_row)

    def _show_category_by_index(self, row_index: int) -> None:
        category = self._row_categories.get(row_index)
        if category:
            self._show_category_by_name(category)

    def _show_category_by_name(self, category: str) -> None:
        table = self.query_one("#category-findings", DataTable)
        table.clear(columns=True)
        table.add_columns("Severity", "Location", "Finding", "Confidence", "Verified")
        self._rows.clear()
        findings = [f for f in self._findings if f.category == category]
        if not findings:
            table.add_row(Text("No findings in this category", style="dim"))
            return
        for idx, finding in enumerate(findings):
            row_key = table.add_row(
                severity_text(finding.severity.value),
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
                key=f"cat-finding-{category}-{idx}",
            )
            self._rows[str(row_key.value)] = finding

    def _show_finding_detail(self, row_key) -> None:
        finding = self._rows.get(str(row_key.value))
        if finding is None:
            return
        scroll = self.query_one("#category-detail-scroll", VerticalScroll)
        scroll.remove_children()
        from textual.widgets import Markdown

        scroll.mount(Markdown(self._finding_markdown(finding)))

    # ------------------------------------------------------------------
    def _drill_into(self, row_index: int) -> None:
        category = self._row_categories.get(row_index)
        if category is None:
            return
        filtered = [f for f in self._findings if f.category == category]
        from agentic_code_reviewer.ui.screens.results import ResultsScreen

        self.app.switch_screen(
            ResultsScreen(
                self.result,
                self.export_path,
                only_category=category,
                filtered=filtered,
            )
        )

    # ------------------------------------------------------------------
    def action_drill_down(self) -> None:
        table = self.query_one("#category-table", DataTable)
        if table.row_count:
            self._drill_into(table.cursor_row)

    def action_drill_up(self) -> None:
        from agentic_code_reviewer.ui.screens.results import ResultsScreen

        self.app.switch_screen(ResultsScreen(self.result, self.export_path))

    def action_export(self) -> None:
        path = self.export_path or Path("review.md")
        path.write_text(self.result.review.to_markdown(), encoding="utf-8")
        self.app.notify(f"Review exported to {path}", title="Export", severity="information")

    def _finding_markdown(self, f: ReviewFinding) -> str:
        from agentic_code_reviewer.ui.theme import SEVERITY_ICON

        icon = SEVERITY_ICON.get(f.severity, "")
        lines = [
            f"## {icon} {f.title}",
            "",
            f"**Confidence:** {f.confidence:.2f} · "
            f"**Verification:** {f.verification_status.value}",
            "",
            f.description,
        ]
        if f.evidence:
            lines += ["", "**Evidence:**", "", f"```\n{f.evidence}\n```"]
        if f.impact:
            lines += ["", "**Impact:**", "", f.impact]
        if f.recommendation:
            lines += ["", "**Recommendation:**", "", f.recommendation]
        return "\n".join(lines)


def _chip(count: int, severity: Severity) -> str:
    """Severity-coloured chip style for a count column."""
    if count == 0:
        return "dim"
    return f"bold {SEVERITY_COLOR.get(severity, BRAND_TEXT_DIM)}"
