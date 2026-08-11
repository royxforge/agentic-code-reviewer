"""Help screen: in-app reference for the launcher, keybindings and CLI."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Markdown, Static

from agentic_code_reviewer.config.runtime_config import config_path
from agentic_code_reviewer.ui.theme import (
    BRAND_ACCENT,
    BRAND_BORDER,
    BRAND_SURFACE,
    BRAND_TEXT_DIM,
)
from agentic_code_reviewer.ui.widgets import AppHeader

_HELP_MD = """\
# ⚡ Agentic Code Reviewer  -  help

**`acr`** is a multi-step, agentic LLM code reviewer. It plans, analyzes the
change across **17 evidence-backed categories**, retrieves relevant context,
verifies claims, and aggregates a report  -  then shows you the findings in a
full-screen dashboard.

## Launcher menu

Typing **`acr`** (or `acr <path>`) opens this menu:

| Key | Action |
|---|---|
| `1` / `2` | Review this directory / review another path |
| `3` | **Providers & API keys**  -  configure everything from the CLI |
| `4` | Review history (stored locally) |
| `5` | This help screen |
| `6` / `q` / `ctrl+c` | Quit |
| `↑`/`↓` + `enter` | Navigate and select |

## All configuration from the CLI

All credentials are configured from the **Providers** screen (or
`acr get-started` in the terminal) and stored in:

```
{config_path}
```

Environment variables still win when set, but you never need them.

## Keybindings across screens

| Screen | Keys |
|---|---|
| Launcher | `1`-`6`, arrows + enter, `q` |
| Providers | `↑`/`↓` pick provider, type fields, `ctrl+s` save, `q` back |
| History | `↑`/`↓` browse, `r` refresh, `q` back |
| Pipeline | `q` quit, `ctrl+p` command palette |
| Results | `j`/`k` move, `c` categories, `e` export, `q` quit |
| Categories | `enter` filter category, `backspace` back, `e` export |

## CLI commands

```
acr                     launcher menu on the current directory
acr <path>              launcher menu on any git project
acr review-local .      review local changes (markdown report)
acr review-local . --format json --fail-on high   CI quality gate
acr review --repo o/r --pr 123
acr review-commit --repo o/r --commit <sha>
acr benchmark --dataset benchmarks/datasets/fixture_small.json
acr get-started         text wizard: provider + API key
acr providers           show provider status
acr history             show recent reviews
acr init                scaffold .reviewer.yaml repo policy
acr tui review-local .  direct TUI review (skips the launcher)
acr --help / acr help   full reference
```

## Providers

- **openai**  -  OpenAI, needs `sk-…` key
- **anthropic**  -  Anthropic, needs `sk-ant-…` key
- **gemini**  -  Google AI Studio, needs `AIza…` key
- **openai_compatible**  -  any OpenAI-compatible endpoint
  (vLLM, LM Studio, Groq, OpenRouter, DeepSeek…), needs a base URL
- **ollama**  -  local server, no key
- **mock**  -  deterministic test double, no key
"""

_CSS = f"""
#help-scroll {{
    height: 1fr;
    padding: 0 2;
    background: {BRAND_SURFACE};
    border: solid {BRAND_BORDER};
    border-top: heavy {BRAND_ACCENT};
    margin: 0 1;
}}
#help-scroll Markdown {{
    background: {BRAND_SURFACE};
}}
#help-foot {{
    height: 1;
    padding: 0 2;
    color: {BRAND_TEXT_DIM};
    text-align: center;
}}
"""


class HelpScreen(Screen):
    """In-app reference."""

    BINDINGS = [
        Binding("q", "back", "Back"),
        Binding("escape", "back", "Back", show=False),
    ]

    DEFAULT_CSS = _CSS

    def compose(self) -> ComposeResult:
        yield AppHeader(title="Agentic Code Reviewer", subtitle="help & shortcuts")
        with Vertical():
            with VerticalScroll(id="help-scroll"):
                yield Markdown(_HELP_MD.format(config_path=config_path()))
            yield Static("q / escape returns to the launcher", id="help-foot")
        # ``show_command_palette=False``: Textual's footer renders the palette
        # key a SECOND time (right side) when it is also bound  -  disable that.
        yield Footer(show_command_palette=False)

    def action_back(self) -> None:
        self.app.pop_screen()
