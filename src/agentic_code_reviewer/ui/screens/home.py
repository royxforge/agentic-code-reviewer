"""Launcher home screen  -  the split-dashboard entry point of ``acr``.

Bare ``acr`` (or ``acr <path>``) opens this screen instead of dropping straight
into a review. The left pane is the keyboard-first action menu; the right pane
is a live status dashboard: the active provider/model, the most recent local
reviews, and engine stats. Everything is configurable from the CLI.

Navigation: up/down + enter (or the number keys) select a menu item; ``q`` /
``ctrl+c`` quits. All sub-screens return here with ``q`` / ``escape``; the
right pane refreshes on resume so provider/history changes show immediately.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import ScreenResume
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Input, Label, ListItem, ListView, Static

from agentic_code_reviewer.config.runtime_config import (
    PROVIDER_FIELDS,
    RuntimeConfig,
    active_provider_status,
)
from agentic_code_reviewer.models.review import ALLOWED_CHECKS
from agentic_code_reviewer.ui.theme import (
    BRAND_ACCENT,
    BRAND_ACCENT_2,
    BRAND_BORDER,
    BRAND_ELEVATED,
    BRAND_PANEL,
    BRAND_SURFACE,
    BRAND_TEXT,
    BRAND_TEXT_DIM,
    SEVERITY_PILL_BG,
    STAGE_ORDER,
)
from agentic_code_reviewer.ui.widgets import AppHeader, dispatch_action

# (id, icon, label, hint)  -  the launcher menu in display order.
_MENU = [
    ("review-cwd", "▶", "Review this directory", ""),
    ("review-path", "◈", "Review another path…", "pick any git project"),
    ("providers", "⚙", "Providers & API keys", "stored in user config"),
    ("history", "▤", "Review history", "stored locally"),
    ("help", "?", "Help & shortcuts", "commands, keybindings"),
    ("quit", "×", "Quit", "ctrl+c"),
]

# Action dispatched on the app for each menu id.
_ACTION = {
    "review-cwd": "review_cwd",
    "review-path": "open_path_prompt",
    "providers": "open_providers",
    "history": "open_history",
    "help": "open_help",
    "quit": "quit",
}

# severity name -> colour (for the recent-reviews severity counts).
_SEV = {sev.value: color for sev, color in SEVERITY_PILL_BG.items()}


def _shorten_path(path: str, limit: int = 20) -> str:
    """Trim a path so it fits the narrow hint column (tail-first)."""
    if len(path) <= limit:
        return path
    return "…" + path[-(limit - 1):]

_CSS = f"""
#home-dash {{
    height: 1fr;
    padding: 1 1;
}}
#home-left {{
    width: 48%;
    border-right: solid {BRAND_BORDER};
    padding-right: 1;
}}
#home-right {{
    width: 1fr;
    padding-left: 1;
}}
.home-pane-title {{
    height: 1;
    background: {BRAND_PANEL};
    color: {BRAND_ACCENT_2};
    text-style: bold;
    padding: 0 1;
    margin-bottom: 1;
}}
#home-menu {{
    height: auto;
    border: none;
    background: $background;
}}
#home-menu:focus {{
    border: none;
}}
ListItem {{
    height: auto;
    padding: 0 1;
    border-left: blank;
    background: $background;
}}
ListItem > Horizontal {{
    height: 1;
    padding: 0 0;
}}
ListItem:hover {{
    background: {BRAND_PANEL};
}}
.home-num {{
    width: 4;
    text-align: right;
    color: {BRAND_TEXT_DIM};
}}
.home-icon {{
    width: 4;
    text-align: center;
    color: {BRAND_ACCENT_2};
}}
.home-label {{
    width: 30;
    color: {BRAND_TEXT};
    text-style: bold;
}}
.home-hint {{
    width: 1fr;
    content-align: right middle;
    color: {BRAND_TEXT_DIM};
}}
#home-menu > ListItem.-active {{
    background: {BRAND_ELEVATED};
    border-left: heavy {BRAND_ACCENT};
}}
#home-menu > ListItem.-active .home-num {{
    color: {BRAND_ACCENT};
    text-style: bold;
}}
#home-menu > ListItem.-active .home-icon {{
    color: {BRAND_ACCENT};
}}
#home-hint {{
    height: 1;
    margin-top: 1;
    color: {BRAND_TEXT_DIM};
}}
#home-provider {{
    height: 4;
    margin-bottom: 1;
    padding: 0 1;
    background: {BRAND_PANEL};
    border: solid {BRAND_BORDER};
    border-left: heavy {BRAND_ACCENT_2};
}}
#home-recent {{
    height: auto;
    margin-bottom: 1;
    padding: 0 1;
    color: {BRAND_TEXT_DIM};
}}
#home-engine {{
    height: auto;
    padding: 0 1;
    color: {BRAND_TEXT_DIM};
}}
#path-box {{
    width: 70;
    height: auto;
    padding: 1 2;
    background: {BRAND_SURFACE};
    border: solid {BRAND_BORDER};
    border-top: heavy {BRAND_ACCENT};
}}
#path-box Label {{
    height: 1;
    color: {BRAND_TEXT};
    text-style: bold;
}}
#path-box Input {{
    margin: 1 0;
}}
#path-actions {{
    height: 3;
    align: right middle;
}}
#path-actions Button {{
    margin-left: 1;
}}
WelcomeScreen {{ align: center middle; }}
#welcome-box {{
    width: 76;
    height: auto;
    padding: 1 2;
    background: {BRAND_SURFACE};
    border: solid {BRAND_BORDER};
    border-top: heavy {BRAND_ACCENT};
}}
#welcome-box Label {{
    height: 1;
    color: {BRAND_TEXT};
    text-style: bold;
}}
#welcome-body {{
    height: auto;
    padding: 1 0;
    color: {BRAND_TEXT_DIM};
}}
#welcome-box Button {{
    margin-top: 1;
}}
#welcome-hint {{
    margin-top: 1;
    color: {BRAND_TEXT_DIM};
    text-style: italic;
}}
"""


class HomeScreen(Screen):
    """Split dashboard: action menu (left) + live status (right)."""

    # ``app.``-prefixed: screen bindings resolve only on the screen in Textual 8.2.8.
    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("q", "app.quit", "Quit"),
        Binding("ctrl+c", "app.quit", "Quit", show=False),
    ]

    # Vim-style menu navigation: forward to the focused ListView (same pattern
    # as the results/categories screens).
    def action_cursor_down(self) -> None:
        focused = self.app.focused
        if focused is not None and hasattr(focused, "action_cursor_down"):
            focused.action_cursor_down()

    def action_cursor_up(self) -> None:
        focused = self.app.focused
        if focused is not None and hasattr(focused, "action_cursor_up"):
            focused.action_cursor_up()

    DEFAULT_CSS = _CSS

    def __init__(self, initial_path: str = ".", **kwargs) -> None:
        super().__init__(**kwargs)
        self.initial_path = initial_path

    def compose(self) -> ComposeResult:
        yield AppHeader(title="Agentic Code Reviewer", subtitle="launcher")
        with Horizontal(id="home-dash"):
            with Vertical(id="home-left"):
                yield Static("◆ QUICK ACTIONS", classes="home-pane-title")
                yield ListView(*self._menu_items(), id="home-menu")
                yield Static("↑/↓ or j/k navigate · enter select · 1-6 jump · q quit", id="home-hint")
            with VerticalScroll(id="home-right"):
                yield Static("◆ PROVIDER", classes="home-pane-title")
                yield Static("", id="home-provider")
                yield Static("◆ RECENT REVIEWS", classes="home-pane-title")
                yield Static("", id="home-recent")
                yield Static("◆ ENGINE", classes="home-pane-title")
                yield Static("", id="home-engine")
        # ``show_command_palette=False``: Textual's footer renders the palette
        # key a SECOND time (right side) when it is also bound  -  disable that.
        yield Footer(show_command_palette=False)

    def _menu_items(self) -> list[ListItem]:
        items: list[ListItem] = []
        for idx, (mid, icon, label, _hint) in enumerate(_MENU):
            hint = _hint
            if mid == "review-cwd":
                hint = _shorten_path(self.initial_path)
            items.append(
                ListItem(
                    Horizontal(
                        Static(f"{idx + 1:02d}", classes="home-num"),
                        Static(icon, classes="home-icon"),
                        Static(f"{label}", classes="home-label"),
                        Static(hint, classes="home-hint"),
                    ),
                    id=f"item-{mid}",
                )
            )
        return items

    # ------------------------------------------------------------------
    def on_mount(self) -> None:
        self._render_dashboard()
        self.query_one("#home-menu", ListView).focus()

    # NOTE: Textual 8.2.8 dispatches ``ScreenResume`` to the *private*
    # ``_on_screen_resume``  -  there is no public resume hook in this version.
    # Overriding it is the only way to refresh the status pane when the
    # launcher becomes active again (e.g. after configuring a provider or
    # running a review). Do not "clean up" this override.
    def _on_screen_resume(self, event: ScreenResume) -> None:
        super()._on_screen_resume(event)
        if self.is_mounted:
            self._render_dashboard()

    def _provider_info(self) -> tuple[str, str, bool]:
        settings = getattr(self.app, "settings", None)
        if settings is None:
            return "mock", "", True
        return active_provider_status(settings)

    def _render_provider(self) -> None:
        """Status card: ready provider + model, or a pointer to configure one."""
        provider, model, ready = self._provider_info()
        card = self.query_one("#home-provider", Static)
        if ready:
            note = (
                "[dim]keyless test provider · switch in Providers (press 3)[/]"
                if provider == "mock"
                else "[dim]credentials stored in your user config[/]"
            )
            card.update(
                f"[bold #34d399]● READY[/]\n"
                f"[b {BRAND_TEXT}]{provider}[/]"
                + (f"  [dim]{model}[/]" if model else "")
                + "\n"
                + note
            )
        else:
            card.update(
                f"[bold #fbbf24]⚠ NOT CONFIGURED[/]\n"
                f"[b {BRAND_TEXT}]{provider}[/]"
                + (f"  [dim]{model}[/]" if model else "")
                + "\n[dim]press [b]3[/b] to add an API key[/]"
            )

    def _render_dashboard(self) -> None:
        try:
            self._render_provider()
            self._render_recent()
            self._render_engine()
        except Exception:  # noqa: BLE001 - the dashboard must never crash the launcher
            pass  # status pane stays empty on unreadable config/history

    def _render_recent(self) -> None:
        """Most recent local reviews with severity counts."""
        entries = RuntimeConfig.load().load_history(limit=4)
        pane = self.query_one("#home-recent", Static)
        if not entries:
            pane.update("[dim]No reviews yet  -  press [b]1[/b] to run one.[/]")
            return
        lines = []
        for e in reversed(entries):
            repo = str(e.get("repository", "?"))
            if len(repo) > 26:
                repo = repo[:24] + "…"
            counts = " ".join(
                f"[b {_SEV[k]}]{n}{k[0].upper()}[/]"
                for k in ("critical", "high", "medium", "low", "info")
                if (n := int(e.get(k, 0) or 0))
            ) or "[dim]0[/]"
            model = str(e.get("model", "") or "")
            tail = f"  [dim]{model}[/]" if model else ""
            lines.append(f"[b {BRAND_ACCENT}]{repo}[/]  {counts}{tail}")
        pane.update("\n".join(lines))

    def _render_engine(self) -> None:
        """Static engine stats."""
        self.query_one("#home-engine", Static).update(
            f"[b {BRAND_ACCENT}]{len(STAGE_ORDER)}[/] pipeline stages\n"
            f"[b {BRAND_ACCENT}]{len(ALLOWED_CHECKS)}[/] review categories\n"
            f"[b {BRAND_ACCENT}]{len(PROVIDER_FIELDS)}[/] providers · configure from the CLI"
        )

    # ------------------------------------------------------------------
    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item is None:
            return
        mid = event.item.id.removeprefix("item-") if event.item.id else ""
        action = _ACTION.get(mid)
        if action:
            dispatch_action(self.app, action)

    def on_key(self, event) -> None:
        if event.key in ("1", "2", "3", "4", "5", "6"):
            idx = int(event.key) - 1
            if 0 <= idx < len(_MENU):
                event.stop()
                self.query_one("#home-menu", ListView).index = idx
                mid = _MENU[idx][0]
                action = _ACTION.get(mid)
                if action:
                    dispatch_action(self.app, action)


class PathPromptScreen(ModalScreen):
    """Modal asking for a git project path to review."""

    DEFAULT_CSS = _CSS

    def compose(self) -> ComposeResult:
        with Vertical(id="path-box"):
            yield Label("◆  Review a project path")
            yield Input(
                placeholder="path to a git repository  (e.g. ../portfolio)",
                id="path-input",
            )
            with Horizontal(id="path-actions"):
                yield Button("Review", id="path-go", variant="primary")
                yield Button("Cancel", id="path-cancel", variant="default")
        # ModalScreen has no header/footer; the box is the whole view.

    def on_mount(self) -> None:
        self.query_one("#path-input", Input).focus()

    def _submit(self) -> None:
        value = (self.query_one("#path-input", Input).value or "").strip()
        self.app.pop_screen()
        if value:
            self.app.action_review_path(value)  # type: ignore[attr-defined]

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "path-go":
            self._submit()
        elif event.button.id == "path-cancel":
            self.app.pop_screen()

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.app.pop_screen()


class WelcomeScreen(ModalScreen):
    """First-run modal: auto-shown when no provider is configured yet.

    Bare ``acr`` (or ``acr <path>``) opens this before the launcher dashboard
    so a new user is guided into provider setup instead of staring at a
    NOT CONFIGURED card. One-time: it never reappears once a provider is
    stored (or provided via the environment).
    """

    # ``q`` skips like escape; the launcher behind keeps its own quit binding.
    BINDINGS = [
        Binding("escape", "skip", "Skip", show=False),
        Binding("q", "skip", "Skip", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="welcome-box"):
            yield Label("◆  WELCOME TO AGENTIC CODE REVIEWER")
            yield Static(
                "No provider is configured yet  -  pick how you want to get started.\n"
                "Keys are stored in your user config, so this is a one-time setup.",
                id="welcome-body",
            )
            yield Button("⚙  Configure a provider", id="welcome-configure", variant="primary")
            yield Button("▶  Explore with the mock provider", id="welcome-mock")
            yield Button("Skip for now", id="welcome-skip")
            yield Static("1 configure · 2 mock · 3 skip · q / escape dismisses", id="welcome-hint")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "welcome-configure":
            self._open_providers()
        elif bid == "welcome-mock":
            self._use_mock()
        elif bid == "welcome-skip":
            self.action_skip()

    def on_key(self, event) -> None:
        if event.key in ("1", "2", "3"):
            event.stop()
            if event.key == "1":
                self._open_providers()
            elif event.key == "2":
                self._use_mock()
            else:
                self.action_skip()

    def _open_providers(self) -> None:
        """Close the modal and open the Providers wizard."""
        self.app.pop_screen()
        dispatch_action(self.app, "open_providers")

    def _use_mock(self) -> None:
        """Activate the keyless mock provider so the UI is instantly usable."""
        config = RuntimeConfig.load()
        config.set_provider("mock")
        config.save()
        self.app.pop_screen()
        if hasattr(self.app, "refresh_settings"):
            self.app.refresh_settings()
        self.app.notify(
            "Mock provider active  -  explore the UI with no API key",
            title="Welcome",
            severity="information",
        )

    def action_skip(self) -> None:
        self.app.pop_screen()
