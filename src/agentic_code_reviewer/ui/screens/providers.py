"""Providers screen: configure LLM providers and API keys from the TUI.

Everything here is persisted to the user config file (``config.yaml`` in the
config dir) via :class:`RuntimeConfig`. Environment variables still take
precedence when set (``apply_runtime_config`` guarantees it), but credentials
for OpenAI / Anthropic / Gemini / OpenAI-compatible / Ollama are configured
entirely here.

Left pane: every provider with a status chip (active / configured / environment /  - ).
Right pane: an editing form for the highlighted provider. ``ctrl+s`` or the
Save button writes the changes; ``q`` / ``escape`` returns to the launcher.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Input, Static

from agentic_code_reviewer.config.runtime_config import (
    PROVIDER_FIELDS,
    RuntimeConfig,
)
from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.ui.theme import (
    BRAND_ACCENT,
    BRAND_ACCENT_2,
    BRAND_BORDER,
    BRAND_ELEVATED,
    BRAND_PANEL,
    BRAND_SURFACE,
    BRAND_TEXT,
    BRAND_TEXT_DIM,
)
from agentic_code_reviewer.ui.widgets import AppHeader

_LABELS = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "gemini": "Google Gemini",
    "openai_compatible": "OpenAI-compatible",
    "ollama": "Ollama (local)",
    "mock": "Mock (test double)",
}

# Which form fields a provider uses, in display order.
# logical name -> (label, placeholder)
_SPECS: dict[str, dict[str, tuple[str, str]]] = {
    "openai": {
        "api_key": ("API key", "sk-…  (leave as-is to keep the stored key)"),
        "model": ("Model", "gpt-4o-mini"),
    },
    "anthropic": {
        "api_key": ("API key", "sk-ant-…  (leave as-is to keep the stored key)"),
        "model": ("Model", "claude-sonnet-4-5"),
    },
    "gemini": {
        "api_key": ("API key", "AIza…  (leave as-is to keep the stored key)"),
        "model": ("Model", "gemini-2.5-flash"),
    },
    "openai_compatible": {
        "base_url": ("Base URL", "http://localhost:8000/v1"),
        "api_key": ("API key (optional)", "…"),
        "model": ("Model", "your model name"),
    },
    "ollama": {
        "base_url": ("Base URL", "http://localhost:11434"),
        "model": ("Model", "qwen2.5-coder:7b"),
    },
    "mock": {},
}

_CSS = f"""
#providers-header {{
    height: 3;
    padding: 0 1;
    background: {BRAND_SURFACE};
    border-bottom: solid {BRAND_BORDER};
}}
#providers-title {{ height: 1; text-style: bold; color: {BRAND_TEXT}; }}
#providers-sub {{ height: 1; color: {BRAND_TEXT_DIM}; }}
#provider-table {{ height: 1fr; border: none; border-right: solid {BRAND_BORDER}; }}
#provider-form-pane {{ width: 52%; border-left: solid {BRAND_BORDER}; }}
#provider-help-scroll {{ height: auto; }}
#provider-help {{ height: auto; padding: 1; color: {BRAND_TEXT_DIM}; }}
#provider-fields {{ height: auto; padding: 0 1; }}
#provider-fields Input {{
    margin: 0 0 1 0;
}}
#provider-fields Label {{
    height: 1;
    color: {BRAND_TEXT_DIM};
}}
#provider-save-row {{ height: 3; align: left middle; padding: 0 1; }}
#provider-note {{ height: auto; padding: 0 1; color: {BRAND_TEXT_DIM}; }}
.pane-title {{
    height: 1;
    background: {BRAND_PANEL};
    color: {BRAND_ACCENT_2};
    text-style: bold;
    padding: 0 1;
}}
#provider-table > .datatable--cursor {{ background: {BRAND_ELEVATED}; }}
#provider-table > .datatable--header {{
    background: {BRAND_PANEL};
    color: {BRAND_ACCENT};
}}
#provider-table:focus {{ border: none; }}
"""


class ProvidersScreen(Screen):
    """Pick a provider and edit its credentials  -  persisted to the user config."""

    BINDINGS = [
        Binding("ctrl+s", "save", "Save"),
        Binding("q", "back", "Back"),
        Binding("escape", "back", "Back", show=False),
    ]

    DEFAULT_CSS = _CSS

    def __init__(self, settings: Settings | None = None, **kwargs) -> None:
        # ``settings`` is accepted for API compatibility; the screen reads the
        # live value from ``self.app.settings`` so a provider change made
        # here is reflected immediately.
        super().__init__(**kwargs)
        self._active = "openai"
        # row index -> provider key (row keys are stable per row).
        self._row_providers: dict[int, str] = {}

    def compose(self) -> ComposeResult:
        yield AppHeader(title="Agentic Code Reviewer", subtitle="providers & API keys")
        yield Static("", id="providers-header")
        with Horizontal():
            with Vertical():
                yield Static("◆ PROVIDERS", classes="pane-title")
                yield DataTable(id="provider-table", cursor_type="row", zebra_stripes=True)
            with Vertical(id="provider-form-pane"):
                yield Static("◆ EDIT", classes="pane-title")
                with VerticalScroll(id="provider-help-scroll"):
                    yield Static("", id="provider-help")
                with Vertical(id="provider-fields"):
                    yield Static("◆ API KEY", classes="pane-title", id="label-api_key")
                    yield Input(id="field-api_key", password=True)
                    yield Static("◆ MODEL", classes="pane-title", id="label-model")
                    yield Input(id="field-model")
                    yield Static("◆ BASE URL", classes="pane-title", id="label-base_url")
                    yield Input(id="field-base_url")
                with Horizontal(id="provider-save-row"):
                    yield Button("Save & apply", id="provider-save", variant="primary")
                yield Static("", id="provider-note")
        # ``show_command_palette=False``: Textual's footer renders the palette
        # key a SECOND time (right side) when it is also bound  -  disable that.
        yield Footer(show_command_palette=False)

    # ------------------------------------------------------------------
    def on_mount(self) -> None:
        self._render_table()
        self._render_form()
        table = self.query_one("#provider-table", DataTable)
        if table.row_count:
            table.focus()
            table.move_cursor(row=0)

    def _status_of(self, key: str) -> tuple[str, str]:
        """(status, style) for one provider: active > environment > configured >  - ."""
        settings = self.app.settings if hasattr(self.app, "settings") else None
        stored = RuntimeConfig.load()
        if settings is not None:
            active_key = str(settings.llm_provider).replace("-", "_")
            if key == active_key:
                return "● active", "bold #3fb950"
            env_set = False
            # ``model_dump`` avoids ``getattr(model, str, default)``  -  mypy
            # mis-types that call against pydantic's ``__getattr__``.
            current = settings.model_dump()
            for field in PROVIDER_FIELDS[key].values():
                if current.get(field):
                    env_set = True
                    break
            if env_set:
                return "environment", "cyan"
        if stored.provider_configured(key):
            return "configured", "cyan"
        return " - ", "dim"

    def _render_table(self) -> None:
        table = self.query_one("#provider-table", DataTable)
        table.clear(columns=True)
        table.add_columns("Provider", "Status")
        self._row_providers.clear()
        for key in PROVIDER_FIELDS:
            status, style = self._status_of(key)
            table.add_row(
                f"[bold {BRAND_TEXT}]{key}[/]",
                f"[{style}]{status}[/]",
                key=f"prov-{key}",
            )
            self._row_providers[table.row_count - 1] = key
        header = self.query_one("#providers-header", Static)
        active = (
            str(getattr(self.app.settings, "llm_provider", "?"))
            if hasattr(self.app, "settings")
            else "?"
        )
        header.update(
            f"PROVIDERS  -  {len(PROVIDER_FIELDS)} available · "
            f"active: [bold #34d399]{active}[/]\n"
            "[dim]select a row to edit · ctrl+s saves · keys live in your user config[/dim]"
        )

    # ------------------------------------------------------------------
    def _render_form(self) -> None:
        """Show/edit the fields for the active provider (fixed inputs, toggled)."""
        specs = _SPECS.get(self._active, {})
        settings = self.app.settings if hasattr(self.app, "settings") else None
        stored = RuntimeConfig.load().credentials(self._active)

        for logical, (_label, placeholder) in specs.items():
            field_name = PROVIDER_FIELDS[self._active][logical]
            default = ""
            if settings is not None and field_name in Settings.model_fields:
                default = str(getattr(settings, field_name, "") or "")
            field = self.query_one(f"#field-{logical}", Input)
            field.display = True
            field.password = logical == "api_key"
            field.placeholder = (
                default if logical == "model" and default else placeholder
            )
            field.value = stored.get(logical, "") or ""
            self.query_one(f"#label-{logical}", Static).display = True
        for hidden in ("api_key", "model", "base_url"):
            if hidden not in specs:
                self.query_one(f"#field-{hidden}", Input).display = False
                self.query_one(f"#label-{hidden}", Static).display = False
        self.query_one("#provider-help", Static).update(
            f"[dim]{_LABELS.get(self._active, self._active)}[/]"
            + ("  -  no credentials needed" if not specs else "")
            + "  [dim]· stored in your user config[/]"
        )

    # ------------------------------------------------------------------
    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        key = self._row_providers.get(event.cursor_row)
        if key:
            self._active = key
            self._render_form()

    # ------------------------------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Wire the ``Save & apply`` button to the same path as ``ctrl+s``."""
        if event.button.id == "provider-save":
            self.action_save()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter in any field also saves (natural form behaviour)."""
        self.action_save()

    # ------------------------------------------------------------------
    def action_save(self) -> None:
        """Persist the active provider. Empty fields remove any stored value
        (``RuntimeConfig.set_provider`` deletes unset fields)  -  the fields are
        prefilled with stored values, so only an explicit clear deletes."""
        specs = _SPECS.get(self._active, {})
        values: dict[str, str] = {}
        for logical in specs:
            field = self.query_one(f"#field-{logical}", Input)
            value = (field.value or "").strip()
            if value:
                values[logical] = value

        if self._active in ("openai", "anthropic", "gemini") and not values.get("api_key"):
            self.app.notify(
                f"{_LABELS[self._active]} needs an API key",
                title="Provider not saved",
                severity="error",
            )
            return
        if self._active == "openai_compatible" and not values.get("base_url"):
            self.app.notify(
                "OpenAI-compatible needs a base URL",
                title="Provider not saved",
                severity="error",
            )
            return

        config = RuntimeConfig.load()
        config.set_provider(self._active, **values)
        config.save()
        if hasattr(self.app, "refresh_settings"):
            self.app.refresh_settings()
        self._render_table()
        self._render_form()
        self.app.notify(
            f"Saved {_LABELS.get(self._active, self._active)}",
            title="Provider configured",
            severity="information",
        )

    # ------------------------------------------------------------------
    def action_back(self) -> None:
        self.app.pop_screen()
