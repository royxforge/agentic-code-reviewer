"""Reusable confirmation modal for destructive actions.

``ConfirmScreen`` asks before an irreversible action (e.g. clearing the
review history). The safe choice  -  **Cancel**  -  is pre-focused, so a stray
``enter`` can never trigger the destructive path; the user must deliberately
Tab to the confirm button (or click it). ``escape`` / ``q`` cancel.
"""

from __future__ import annotations

from collections.abc import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from agentic_code_reviewer.ui.theme import (
    BRAND_BORDER,
    BRAND_SURFACE,
    BRAND_TEXT,
    BRAND_TEXT_DIM,
    DANGER,
)

_CSS = f"""
#confirm-box {{
    width: 62;
    height: auto;
    padding: 1 2;
    background: {BRAND_SURFACE};
    border: solid {BRAND_BORDER};
    border-top: heavy {DANGER};
}}
#confirm-title {{
    height: 1;
    text-style: bold;
    color: {BRAND_TEXT};
}}
#confirm-message {{
    height: auto;
    margin: 1 0;
    color: {BRAND_TEXT_DIM};
}}
#confirm-actions {{
    height: 3;
    align: right middle;
}}
#confirm-actions Button {{
    margin-left: 1;
}}
#confirm-ok {{
    background: {DANGER};
    color: #0a0a12;
    text-style: bold;
}}
"""


class ConfirmScreen(ModalScreen):
    """Ask before a destructive action; the safe choice is pre-focused.

    ``on_confirm`` runs *after* the modal has closed, so a ``notify`` fired
    from it is never hidden behind the modal.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("q", "cancel", "Cancel", show=False),
    ]

    DEFAULT_CSS = _CSS

    def __init__(
        self,
        title: str,
        message: str,
        *,
        confirm_label: str = "Confirm",
        on_confirm: Callable[[], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._title = title
        self._message = message
        self._confirm_label = confirm_label
        self._on_confirm = on_confirm

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(self._title, id="confirm-title")
            yield Static(self._message, id="confirm-message")
            with Horizontal(id="confirm-actions"):
                yield Button("Cancel", id="confirm-cancel")
                yield Button(self._confirm_label, id="confirm-ok")

    def on_mount(self) -> None:
        # Safety first: Enter (or the focused button) cancels by default.
        self.query_one("#confirm-cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-ok":
            self.app.pop_screen()
            if self._on_confirm is not None:
                self._on_confirm()
        elif event.button.id == "confirm-cancel":
            self.app.pop_screen()

    def action_cancel(self) -> None:
        self.app.pop_screen()
