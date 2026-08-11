"""Typed, non-blocking event stream emitted by the :class:`Workflow`.

The workflow is deliberately UI-agnostic: it never imports the TUI package.
Instead it accepts an optional ``event_sink`` callback and emits immutable
:class:`WorkflowEvent` objects at meaningful points (stage start/done/failure,
usage deltas, findings, plan, context, final result). Consumers  -  like the
Textual dashboard  -  subscribe to this stream to render live progress.

Events carry only display-friendly primitives; they never carry secrets.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class WorkflowEvent:
    """A single observable moment in the lifecycle of a review.

    ``kind`` selects the shape of the event:

    - ``stage_start``    ``stage`` is about to run.
    - ``stage_done``     ``stage`` finished OK in ``seconds``.
    - ``stage_failed``   ``stage`` failed with ``message`` after ``seconds``.
    - ``stage_skipped``  ``stage`` was skipped (e.g. verifier, no findings).
    - ``usage``          cumulative token/cost/call counters after a stage.
    - ``plan``           the planner produced ``payload`` (a ReviewPlan).
    - ``context``        ``payload`` is the number of context chunks selected.
    - ``finding``        ``payload`` is a single ReviewFinding (streamed live).
    - ``decomposed``     the diff was decomposed into ``payload`` groups.
    - ``log``            free-form progress text in ``message``.
    - ``finish``         ``payload`` is the final WorkflowResult.
    """

    kind: str
    stage: str = ""
    seconds: float = 0.0
    message: str = ""
    payload: Any = None
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def stage_start(cls, stage: str) -> WorkflowEvent:
        return cls(kind="stage_start", stage=stage)

    @classmethod
    def stage_done(cls, stage: str, seconds: float) -> WorkflowEvent:
        return cls(kind="stage_done", stage=stage, seconds=seconds)

    @classmethod
    def stage_failed(cls, stage: str, seconds: float, message: str) -> WorkflowEvent:
        return cls(kind="stage_failed", stage=stage, seconds=seconds, message=message)

    @classmethod
    def stage_skipped(cls, stage: str, message: str = "") -> WorkflowEvent:
        return cls(kind="stage_skipped", stage=stage, message=message)


# A sink is a plain callable  -  no interface ceremony needed.
EventSink = Callable[[WorkflowEvent], None]

NOOP_SINK: EventSink = lambda _event: None  # noqa: E731
