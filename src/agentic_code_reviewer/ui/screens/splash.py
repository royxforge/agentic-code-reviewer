"""Boot splash screen: the brand moment when ``acr`` launches.

Bare ``acr`` (or ``acr <path>``) shows this screen for a beat before the
launcher dashboard  -  a block-letter ``ACR`` logo, the full name, the
version, and an animated loading line. It auto-dismisses after
:data:`SPLASH_SECONDS` and can be skipped instantly with any key, exactly
like the boot animations of other modern CLI agents.

The screen never appears in headless runs (tests, screenshot capture): the
launcher app checks ``is_headless`` and skips straight to the dashboard, so
nothing that drives ``HomeApp`` programmatically needs to handle it.
"""

from __future__ import annotations

from collections.abc import Callable

from textual.app import ComposeResult
from textual.events import Key
from textual.screen import Screen
from textual.widgets import Static

from agentic_code_reviewer import __version__
from agentic_code_reviewer.ui.theme import (
    BRAND_ACCENT,
    BRAND_ACCENT_2,
    BRAND_BG,
    BRAND_PANEL,
    BRAND_TEXT,
    BRAND_TEXT_DIM,
)

# How long the splash stays up before auto-dismissing (seconds).
SPLASH_SECONDS = 1.8

# Block-letter ACR logo (3 chars wide x 6 rows, fixed-pitch friendly).
_LOGO = r"""
        █████╗  ██████╗ ██████╗
       ██╔══██╗██╔════╝ ██╔══██╗
       ███████║██║      ██████╔╝
       ██╔══██║██║      ██╔══██╗
       ██║  ██║╚██████╗ ██║  ██║
       ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝
"""

# Cycling loading labels (shown one per ~0.45s under the spinner).
_LOADING_LABELS = [
    "warming the engines",
    "loading providers",
    "preparing the pipeline",
    "arming 23 review categories",
    "ready",
]

_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

_CSS = f"""
#splash-root {{
    align: center middle;
    width: 100%;
    height: 100%;
    background: {BRAND_BG};
}}
#splash-logo {{
    color: {BRAND_ACCENT};
    text-style: bold;
    width: auto;
}}
#splash-name {{
    margin-top: 1;
    color: {BRAND_TEXT};
    text-style: bold;
    width: auto;
}}
#splash-sub {{
    margin-top: 1;
    color: {BRAND_TEXT_DIM};
    width: auto;
}}
#splash-version {{
    margin-top: 1;
    color: {BRAND_ACCENT_2};
    width: auto;
}}
#splash-spinner {{
    margin-top: 2;
    color: {BRAND_TEXT};
    width: auto;
}}
#splash-progress {{
    margin-top: 1;
    width: auto;
}}
#splash-hint {{
    margin-top: 2;
    color: {BRAND_TEXT_DIM};
    width: auto;
}}
"""


class SplashScreen(Screen):
    """Full-screen boot animation for the launcher."""

    DEFAULT_CSS = _CSS

    def __init__(
        self,
        on_dismiss: Callable[[], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._on_dismiss = on_dismiss
        self._frame = 0
        self._tick_ms = 90  # spinner + progress advance interval

    def compose(self) -> ComposeResult:
        with Static(id="splash-root"):
            yield Static(_LOGO, id="splash-logo")
            yield Static("AGENTIC CODE REVIEWER", id="splash-name")
            yield Static(
                "multi-agent LLM code review with layered deterministic verification",
                id="splash-sub",
            )
            yield Static(f"v{__version__}", id="splash-version")
            yield Static("", id="splash-spinner")
            yield Static("", id="splash-progress")
            yield Static("press any key to skip", id="splash-hint")

    def on_mount(self) -> None:
        self.set_interval(self._tick_ms / 1000, self._advance)
        self.set_timer(SPLASH_SECONDS, self._finish)

    def _advance(self) -> None:
        """Advance the spinner + progress bar one frame.

        Note: this must NOT be named ``_animate`` -  Textual's ``Widget``
        owns an instance attribute ``self._animate`` (the bound CSS animator)
        that shadows any method with that name, silently nulling the callback.

        The screen guard mirrors ``_finish``: a tick can land in the tiny
        window between ``pop_screen`` and timer teardown, when the splash's
        widgets are no longer mounted (``query_one`` would raise).
        """
        if self.app.screen is not self:
            return
        self._frame += 1
        frame = _SPINNER_FRAMES[self._frame % len(_SPINNER_FRAMES)]
        label = _LOADING_LABELS[min(self._frame // 5, len(_LOADING_LABELS) - 1)]
        self.query_one("#splash-spinner", Static).update(
            f"[b {BRAND_ACCENT}]{frame}[/]  [b {BRAND_TEXT}]{label}[/]"
        )
        # Filled-width progress bar that reaches full just before dismissal.
        total_ticks = int(SPLASH_SECONDS * 1000 / self._tick_ms)
        pct = min(1.0, self._frame / total_ticks)
        bar_len = 24
        filled = int(bar_len * pct)
        bar = "━" * filled + "─" * (bar_len - filled)
        self.query_one("#splash-progress", Static).update(
            f"[{BRAND_PANEL}]┃[/][{BRAND_ACCENT_2}]{bar}[/][{BRAND_PANEL}]┃[/]"
        )

    def _finish(self) -> None:
        """Pop the splash and hand control back to the launcher.

        Named ``_finish`` on purpose: Textual's ``Screen`` already defines an
        async ``action_dismiss``, and overriding it with a sync method breaks
        the contract (mypy) for no benefit.
        """
        if self.app.screen is not self:
            return
        self.app.pop_screen()
        if self._on_dismiss is not None:
            self._on_dismiss()

    def on_key(self, event: Key) -> None:
        event.stop()
        self._finish()
