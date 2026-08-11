from pathlib import Path

from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.evaluation.benchmark import BenchmarkLoader
from agentic_code_reviewer.evaluation.experiments import ExperimentTracker
from agentic_code_reviewer.evaluation.runner import run_benchmark
from tests.integration.helpers import AdaptiveMock

FIXTURE = Path(__file__).resolve().parents[2] / "benchmarks" / "datasets" / "fixture_small.json"


def _settings() -> Settings:
    return Settings(LLM_PROVIDER="mock", EMBEDDING_PROVIDER="local", LOG_LEVEL="WARNING")


class _WorkflowWithClient:
    """Workflow wrapper that injects the adaptive mock client."""

    def __init__(self, settings):
        self.settings = settings

    def run(self, request):
        from agentic_code_reviewer.orchestration.workflow import Workflow

        return Workflow(self.settings, client=AdaptiveMock()).run(request)


def test_runner_computes_metrics(tmp_path, monkeypatch):
    settings = _settings()
    entries = BenchmarkLoader.load(FIXTURE)
    assert len(entries) == 3

    # Deterministic clients everywhere (baselines + agentic workflow).
    import agentic_code_reviewer.evaluation.baselines as baselines_mod

    monkeypatch.setattr(baselines_mod, "_build_client", lambda s: AdaptiveMock(), raising=False)
    monkeypatch.setattr("agentic_code_reviewer.evaluation.runner.Workflow", _WorkflowWithClient)

    tracker = ExperimentTracker("test", root=str(tmp_path))
    metrics = run_benchmark(entries, ["single-pass", "agentic"], settings, tracker, limit=2)

    for system in ("single-pass", "agentic"):
        m = metrics[system]
        # Both positive entries (SQL injection, off-by-one) are detected; the
        # negative entry is not included (limit=2).
        assert m.detected == 2
        assert m.tp == 2
        assert m.fp == 0
        assert m.precision == 1.0
        assert m.recall == 1.0
        assert m.f1 == 1.0
        assert m.completion_rate == 1.0
        assert m.total_cost >= 0
        assert m.total_tokens > 0

    # Artifacts were written and never overwrite an existing experiment.
    assert tracker.path.exists()
    assert (tracker.path / "metrics.json").exists()
    assert (tracker.path / "predictions.jsonl").exists()


def test_runner_full_dataset_with_negative_case(tmp_path, monkeypatch):
    settings = _settings()
    entries = BenchmarkLoader.load(FIXTURE)

    import agentic_code_reviewer.evaluation.baselines as baselines_mod

    monkeypatch.setattr(baselines_mod, "_build_client", lambda s: AdaptiveMock(), raising=False)
    monkeypatch.setattr("agentic_code_reviewer.evaluation.runner.Workflow", _WorkflowWithClient)

    tracker = ExperimentTracker("full", root=str(tmp_path))
    metrics = run_benchmark(entries, ["agentic"], settings, tracker, limit=None)

    agentic = metrics["agentic"]
    assert agentic.entries == 3
    assert agentic.detected == 2
    # The clean refactor produces no findings, hence no false positives.
    assert agentic.fp == 0
    assert agentic.precision == 1.0
    assert agentic.recall == 1.0
