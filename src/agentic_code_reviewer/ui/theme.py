"""Design system for the terminal dashboard.

Single source of truth for the visual identity: severity colours, the stage
lifecycle (ordered display, phase grouping), per-category colours, and the
spinner frames. Screens and widgets import from here so the look stays
consistent everywhere.

Visual direction  -  *neo-glass premium dark*:
deep violet-tinted blacks, layered flat surfaces, crisp hairlines, a heavy
electric-violet "glass cap" line on key surfaces, and bold pill chips. Textual
8.2.8 supports neither gradients nor border-radius, so the glass feel comes
from structure: elevation steps, hairline borders and a strong accent line.
"""

from __future__ import annotations

from rich.text import Text

from agentic_code_reviewer.models.findings import Severity, VerificationStatus

# ---- brand palette --------------------------------------------------------
# Neo-glass premium dark, anchored on electric violet (#a78bfa).
# ANSI-256 safe hex values.
BRAND_BG = "#0a0a12"        # app background (deepest, violet-tinted)
BRAND_SURFACE = "#12121e"   # cards / rows / log panes
BRAND_PANEL = "#1a1a2a"     # titled panes, bars
BRAND_ELEVATED = "#24243c"  # hover / focused surfaces
BRAND_BORDER = "#2b2b45"    # hairline borders
BRAND_TEXT = "#eceef6"
BRAND_TEXT_DIM = "#9d9fb4"
BRAND_ACCENT = "#a78bfa"    # electric violet  -  primary action / brand
BRAND_ACCENT_2 = "#c4b5fd"  # pale violet  -  secondary accent
DANGER = "#fb7185"          # destructive actions / critical severity

# Dark ink used on top of saturated pill backgrounds (readable contrast).
PILL_INK = "#0a0a12"

# ---- severity -------------------------------------------------------------
SEVERITY_COLOR = {
    Severity.CRITICAL: "bold #fb7185",
    Severity.HIGH: "bold #f87171",
    Severity.MEDIUM: "bold #fbbf24",
    Severity.LOW: "bold #34d399",
    Severity.INFO: "bold #7dd3fc",
}

# Solid background for filled severity pills (dark ink on top).
SEVERITY_PILL_BG = {
    Severity.CRITICAL: "#fb7185",
    Severity.HIGH: "#f87171",
    Severity.MEDIUM: "#fbbf24",
    Severity.LOW: "#34d399",
    Severity.INFO: "#7dd3fc",
}

# Terminal-safe geometric glyphs (avoid emoji that many fonts lack).
SEVERITY_ICON = {
    Severity.CRITICAL: "▲",
    Severity.HIGH: "●",
    Severity.MEDIUM: "◆",
    Severity.LOW: "◈",
    Severity.INFO: "·",
}

VERIFICATION_COLOR = {
    VerificationStatus.VERIFIED: "bold #34d399",
    VerificationStatus.STRONGLY_INFERRED: "bold #fbbf24",
    VerificationStatus.POTENTIAL: "bold #c4b5fd",
    VerificationStatus.UNVERIFIED: "dim",
}

# ---- stage lifecycle ------------------------------------------------------
# Ordered display for the pipeline tracker.
STAGE_ORDER = [
    "planner",
    "change_analyzer",
    "context",
    "correctness",
    "security",
    "error_handling",
    "testing",
    "regression",
    "performance",
    "maintainability",
    "observability",
    "data_integrity",
    "accessibility",
    "concurrency",
    "dependencies",
    "privacy",
    "i18n",
    "api_contract",
    "requirement_alignment",
    "dead_code",
    "verifier",
    "aggregator",
]

# Phase grouping for the tracker: (phase name, accent colour, stages).
PHASES: list[tuple[str, str, list[str]]] = [
    ("Planning", BRAND_ACCENT, ["planner", "change_analyzer", "context"]),
    (
        "Analysis",
        BRAND_ACCENT_2,
        [
            "correctness",
            "security",
            "error_handling",
            "testing",
            "regression",
            "performance",
            "maintainability",
            "observability",
            "data_integrity",
            "accessibility",
            "concurrency",
            "dependencies",
            "privacy",
            "i18n",
            "api_contract",
            "requirement_alignment",
            "dead_code",
        ],
    ),
    ("Synthesis", "#34d399", ["verifier", "aggregator"]),
]

SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

STAGE_LABEL = {
    "planner": "Planner",
    "change_analyzer": "Change analysis",
    "context": "Context retrieval",
    "correctness": "Correctness",
    "security": "Security",
    "error_handling": "Error handling",
    "testing": "Testing",
    "regression": "Regression",
    "performance": "Performance",
    "maintainability": "Maintainability",
    "observability": "Observability",
    "data_integrity": "Data integrity",
    "accessibility": "Accessibility",
    "concurrency": "Concurrency",
    "dependencies": "Dependencies",
    "privacy": "Privacy",
    "i18n": "i18n",
    "api_contract": "API contract",
    "requirement_alignment": "Requirement alignment",
    "dead_code": "Dead code",
    "verifier": "Evidence verification",
    "aggregator": "Aggregation",
}

# ---- per-category colour (findings table + live counters) ----------------
CATEGORY_COLOR: dict[str, str] = {
    "correctness": "#fbbf24",
    "security": "#fb7185",
    "error_handling": "#fdba74",
    "testing": "#34d399",
    "regression": "#22d3ee",
    "performance": "#7dd3fc",
    "maintainability": "#a1a3b8",
    "observability": "#93c5fd",
    "data_integrity": "#fbbf24",
    "accessibility": "#c4b5fd",
    "concurrency": "#fde047",
    "dependencies": "#4ade80",
    "privacy": "#f87171",
    "i18n": "#bae6fd",
    "api_contract": "#93c5fd",
    "requirement_alignment": "#fcd34d",
    "dead_code": "#a1a3b8",
}


def severity_text(value: str) -> Text:
    """Render a severity as a coloured uppercase label."""
    sev = Severity(value)
    color = SEVERITY_COLOR[sev]
    return Text(value.upper(), style=color)


def severity_pill(value: str, count: int | None = None) -> str:
    """Filled severity pill markup: dark ink on a saturated background.

    e.g. ``severity_pill("high", 3)`` → ``[b #0a0a12 on #f87171] 3 HIGH [/]``
    """
    sev = Severity(value)
    bg = SEVERITY_PILL_BG[sev]
    label = value.upper() if count is None else f"{count} {value.upper()}"
    return f"[b {PILL_INK} on {bg}] {label} [/]"
