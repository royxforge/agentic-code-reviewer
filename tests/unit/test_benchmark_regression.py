"""Tests for the benchmark regression comparison and its CI gate."""

import json

import pytest
import typer
from typer.main import get_command

from agentic_code_reviewer.cli import _run_regression_gate, app
from agentic_code_reviewer.errors import BenchmarkError
from agentic_code_reviewer.evaluation.metrics import SystemMetrics, to_table_row
from agentic_code_reviewer.evaluation.regression import (
    DEFAULT_TOLERANCE,
    compare_metrics,
    load_metrics,
)


def _rows(system: str = "agentic", **metrics) -> dict[str, dict]:
    """A minimal one-system metrics mapping holding just the metrics given."""
    return {system: {"system": system, **metrics}}


def _fake_metrics(**overrides) -> SystemMetrics:
    """A representative SystemMetrics; override aggregates for a comparison."""
    base = dict(
        system="agentic",
        entries=4,
        tp=3,
        fp=1,
        fn=1,
        detected=3,
        severity_correct=2,
        total_latency=4.0,
        total_cost=0.004,
        total_tokens=4000,
        successes=4,
    )
    base.update(overrides)
    return SystemMetrics(**base)


class TestDirectionAwareness:
    """Quality falling and cost/latency rising are both regressions."""

    def test_quality_drop_is_a_regression(self):
        report = compare_metrics(_rows(f1=0.68), _rows(f1=0.5))

        assert not report.ok
        assert [(d.metric, d.regressed) for d in report.regressions] == [("f1", True)]
        assert report.regressions[0].delta == pytest.approx(-0.18)

    def test_quality_gain_is_not_a_regression(self):
        report = compare_metrics(_rows(f1=0.68), _rows(f1=0.9))

        assert report.ok
        assert [d.metric for d in report.deltas if d.improved] == ["f1"]

    def test_latency_rise_is_a_regression(self):
        report = compare_metrics(_rows(mean_latency_s=1.0), _rows(mean_latency_s=2.5))

        assert not report.ok
        assert [d.metric for d in report.regressions] == ["mean_latency_s"]

    def test_latency_drop_is_an_improvement(self):
        report = compare_metrics(_rows(mean_latency_s=1.0), _rows(mean_latency_s=0.4))

        assert report.ok
        assert [d.metric for d in report.deltas if d.improved] == ["mean_latency_s"]

    def test_cost_and_token_rises_are_regressions(self):
        report = compare_metrics(
            _rows(mean_cost_usd=0.01, total_tokens=1000),
            _rows(mean_cost_usd=0.09, total_tokens=5000),
        )

        assert {d.metric for d in report.regressions} == {"mean_cost_usd", "total_tokens"}

    def test_false_positive_rate_rise_is_a_regression(self):
        report = compare_metrics(
            _rows(false_positive_rate=0.2), _rows(false_positive_rate=0.5)
        )

        assert [d.metric for d in report.regressions] == ["false_positive_rate"]

    def test_moves_within_tolerance_are_not_regressions(self):
        # Equality with the tolerance must not count (float artefact guard).
        report = compare_metrics(
            _rows(f1=0.68, mean_latency_s=1.0),
            _rows(f1=0.68 - DEFAULT_TOLERANCE, mean_latency_s=1.0 + DEFAULT_TOLERANCE),
        )

        assert report.ok
        assert report.regressions == []


class TestReportShape:
    def test_missing_system_fails_the_gate(self):
        report = compare_metrics(
            {"agentic": {"system": "agentic", "f1": 0.7}, "rag": {"system": "rag", "f1": 0.5}},
            {"agentic": {"system": "agentic", "f1": 0.7}},
        )

        assert not report.ok
        assert report.missing_systems == ["rag"]
        assert "missing from this run" in report.format()

    def test_new_system_is_reported_but_does_not_fail(self):
        report = compare_metrics(
            {"agentic": {"system": "agentic", "f1": 0.7}},
            {"agentic": {"system": "agentic", "f1": 0.7}, "rag": {"system": "rag", "f1": 0.5}},
        )

        assert report.ok
        assert report.extra_systems == ["rag"]
        assert "new systems not in baseline" in report.format()

    def test_null_baseline_metric_is_skipped(self):
        """A null baseline value carries no information to regress from."""
        report = compare_metrics(
            {"agentic": {"system": "agentic", "precision": None}},
            {"agentic": {"system": "agentic", "precision": 0.1}},
        )

        assert report.deltas == []

    def test_metric_that_became_unavailable_counts_as_regression(self):
        report = compare_metrics(
            _rows(f1=0.7), {"agentic": {"system": "agentic", "f1": None}}
        )

        assert not report.ok
        (delta,) = report.regressions
        assert delta.unavailable is True
        assert "unavailable" in delta.format()

    def test_metrics_filter_restricts_comparison(self):
        report = compare_metrics(
            _rows(f1=0.7, mean_latency_s=1.0),
            _rows(f1=0.1, mean_latency_s=99.0),
            metrics={"f1"},
        )

        assert {d.metric for d in report.deltas} == {"f1"}

    def test_negative_tolerance_is_rejected(self):
        with pytest.raises(ValueError):
            compare_metrics(_rows(f1=0.7), _rows(f1=0.7), tolerance=-1.0)

    def test_report_is_ascii_only(self):
        report = compare_metrics(
            {"agentic": {"system": "agentic", "f1": 0.7}, "rag": {"system": "rag", "f1": 0.5}},
            {"agentic": {"system": "agentic", "f1": 0.1}},
        )

        report.format().encode("ascii")

    def test_labels_distinguish_tolerated_from_improved(self):
        report = compare_metrics(
            _rows(mean_latency_s=1.0),
            _rows(mean_latency_s=1.0 + DEFAULT_TOLERANCE / 2),
        )

        (delta,) = report.deltas
        assert not delta.regressed
        assert not delta.improved
        assert delta.label == "tolerated "


class TestLoadMetrics:
    def test_round_trip_from_tracker_artifact(self, tmp_path):
        rows = {
            m.system: to_table_row(m) for m in [_fake_metrics(), _fake_metrics(system="rag")]
        }
        path = tmp_path / "metrics.json"
        path.write_text(json.dumps(rows, indent=2), encoding="utf-8")

        loaded = load_metrics(path)

        assert set(loaded) == {"agentic", "rag"}
        assert loaded["agentic"]["total_tokens"] == 4000
        assert loaded["agentic"]["precision"] == pytest.approx(0.75)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(BenchmarkError, match="not found"):
            load_metrics(tmp_path / "nope.json")

    def test_invalid_json_raises(self, tmp_path):
        path = tmp_path / "metrics.json"
        path.write_text("{not json", encoding="utf-8")

        with pytest.raises(BenchmarkError, match="not valid JSON"):
            load_metrics(path)

    def test_empty_object_raises(self, tmp_path):
        path = tmp_path / "metrics.json"
        path.write_text("{}", encoding="utf-8")

        with pytest.raises(BenchmarkError, match="no systems"):
            load_metrics(path)

    def test_non_object_row_raises(self, tmp_path):
        path = tmp_path / "metrics.json"
        path.write_text(json.dumps({"agentic": 1.0}), encoding="utf-8")

        with pytest.raises(BenchmarkError, match="not an object"):
            load_metrics(path)


class TestCliRegressionGate:
    """The gate is the CI contract: an exit code, not just a printed warning."""

    def _write(self, tmp_path, metrics: SystemMetrics):
        rows = {metrics.system: to_table_row(metrics)}
        path = tmp_path / "metrics.json"
        path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        return path

    def test_matching_run_does_not_exit(self, tmp_path):
        metrics = _fake_metrics()

        _run_regression_gate(
            self._write(tmp_path, metrics),
            {"agentic": metrics},
            tolerance=DEFAULT_TOLERANCE,
            fail_on_regression=True,
        )

    def test_missing_baseline_with_gate_flag_is_a_usage_error(self):
        with pytest.raises(typer.BadParameter, match="requires --baseline"):
            _run_regression_gate(
                None, {"agentic": _fake_metrics()}, tolerance=DEFAULT_TOLERANCE,
                fail_on_regression=True,
            )

    def test_no_baseline_without_gate_flag_is_a_no_op(self):
        _run_regression_gate(
            None, {"agentic": _fake_metrics()}, tolerance=DEFAULT_TOLERANCE,
            fail_on_regression=False,
        )

    def test_quality_drop_exits_1(self, tmp_path):
        baseline = self._write(tmp_path, _fake_metrics(tp=3, detected=3))
        # Fewer true positives: precision, recall, F1 and detection rate all fall.
        candidate = _fake_metrics(tp=1, fp=2, detected=1)

        with pytest.raises(typer.Exit) as excinfo:
            _run_regression_gate(
                baseline, {"agentic": candidate}, tolerance=DEFAULT_TOLERANCE,
                fail_on_regression=True,
            )

        assert excinfo.value.exit_code == 1

    def test_regression_without_gate_flag_only_reports(self, tmp_path, capsys):
        baseline = self._write(tmp_path, _fake_metrics(tp=3, detected=3))

        _run_regression_gate(
            baseline,
            {"agentic": _fake_metrics(tp=1, fp=2, detected=1)},
            tolerance=DEFAULT_TOLERANCE,
            fail_on_regression=False,
        )

        # The comparison is still surfaced on stdout for a human to read.
        assert "Regression check vs baseline" in capsys.readouterr().out

    def test_latency_creep_exits_1_with_a_passing_baseline(self, tmp_path):
        clean = _fake_metrics()
        slow = _fake_metrics(total_latency=40.0)  # mean_latency_s 1.0 -> 10.0
        baseline = self._write(tmp_path, clean)

        with pytest.raises(typer.Exit):
            _run_regression_gate(
                baseline, {"agentic": slow}, tolerance=DEFAULT_TOLERANCE,
                fail_on_regression=True,
            )

    def test_tolerance_widens_the_gate(self, tmp_path):
        baseline = self._write(tmp_path, _fake_metrics(detected=3))  # rate 0.75
        candidate = _fake_metrics(detected=1)  # rate 0.25, a 0.5 drop

        with pytest.raises(typer.Exit):
            _run_regression_gate(
                baseline, {"agentic": candidate}, tolerance=DEFAULT_TOLERANCE,
                fail_on_regression=True,
            )
        _run_regression_gate(
            baseline, {"agentic": candidate}, tolerance=0.6, fail_on_regression=True
        )

    def test_benchmark_and_evaluate_expose_the_gate_options(self):
        """Both commands that run experiments must offer the same gate flags."""
        commands = get_command(app).commands

        for name in ("benchmark", "evaluate"):
            params = {p.name for p in commands[name].params}
            assert {"baseline", "max_drop", "fail_on_regression"} <= params, name
