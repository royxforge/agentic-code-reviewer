"""Experiment tracking.

Each experiment gets its own timestamped directory under ``experiments/`` that
is never overwritten. Artifacts follow a fixed layout so results are comparable:

    experiment/
    ├── config.yaml        # the exact configuration used
    ├── predictions.jsonl  # one JSON line per (entry, system) run
    ├── failures.jsonl     # entry/system runs that failed
    ├── metrics.json       # aggregated metrics per system
    ├── latency.json       # per-entry latency
    ├── cost.json          # per-entry cost and token usage
    └── summary.md         # human-readable results table
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from agentic_code_reviewer.evaluation.metrics import SystemMetrics, format_results_table, to_table_row


class ExperimentTracker:
    def __init__(self, name: str, root: str | Path = "experiments") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.dir = self.root / f"{stamp}_{_slug(name)}"
        self.dir.mkdir(parents=True, exist_ok=False)
        self._predictions: list[dict[str, Any]] = []
        self._failures: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    def save_config(self, config: dict[str, Any]) -> None:
        (self.dir / "config.yaml").write_text(
            _to_yaml(config), encoding="utf-8"
        )

    def record_prediction(self, prediction: dict[str, Any]) -> None:
        self._predictions.append(prediction)
        with (self.dir / "predictions.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(prediction, default=str) + "\n")

    def record_failure(self, entry_id: str, system: str, error: str) -> None:
        self._failures.append({"entry_id": entry_id, "system": system, "error": error})
        with (self.dir / "failures.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"entry_id": entry_id, "system": system, "error": error}) + "\n")

    def finish(self, metrics: list[SystemMetrics], summary: str = "") -> None:
        rows = [to_table_row(m) for m in metrics]
        (self.dir / "metrics.json").write_text(
            json.dumps({m.system: to_table_row(m) for m in metrics}, indent=2),
            encoding="utf-8",
        )
        latency = {
            m.system: {
                entry_id: score.latency_seconds
                for entry_id, score in m.per_entry.items()
            }
            for m in metrics
        }
        (self.dir / "latency.json").write_text(json.dumps(latency, indent=2), encoding="utf-8")
        cost = {
            m.system: {
                entry_id: {"cost_usd": score.cost_usd, "tokens": score.tokens}
                for entry_id, score in m.per_entry.items()
            }
            for m in metrics
        }
        (self.dir / "cost.json").write_text(json.dumps(cost, indent=2), encoding="utf-8")

        md = [
            f"# Experiment  -  {self.dir.name}",
            "",
            f"Generated: {datetime.now().isoformat(timespec='seconds')}",
            "",
            "## Results",
            "",
            format_results_table(rows),
            "",
        ]
        if summary:
            md += ["## Notes", "", summary, ""]
        if self._failures:
            md += [
                "## Failures",
                "",
                f"{len(self._failures)} failed run(s) recorded in failures.jsonl.",
                "",
            ]
        (self.dir / "summary.md").write_text("\n".join(md), encoding="utf-8")

    @property
    def path(self) -> Path:
        return self.dir


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name).strip("_")[:60] or "experiment"


def _to_yaml(data: dict[str, Any]) -> str:
    import yaml

    return yaml.safe_dump(data, sort_keys=False)
