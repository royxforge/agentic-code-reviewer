# Research documentation

## Research question

> Can a multi-step agentic LLM workflow detect real-world software bugs more
> reliably than a single-pass LLM code reviewer?

The architecture deliberately supports this investigation through a reusable
evaluation framework rather than hard-coded experiments.

## Systems under comparison

| System       | Pipeline                                             |
|--------------|------------------------------------------------------|
| A. Single-pass | `diff → LLM → findings`                            |
| B. Context     | `diff + deliberate repo context → LLM → findings`  |
| C. RAG         | `diff + retrieval hits → LLM → findings`           |
| D. Agentic     | planner → analysis agents → verification → aggregator |

All systems share the same structured finding schema and are run under
identical conditions (same model, same temperature, same dataset).

## Benchmark construction

A benchmark entry (`evaluation/benchmark.py`) reconstructs the repository state
**at the point where a bug was introduced**:

```json
{
  "entry_id": "...",
  "repository": "...",
  "commit": "<bug-introducing commit>",
  "parent_commit": "...",
  "bug_description": "...",      // held out
  "bug_fix": "...",              // held out
  "diff": "...",                 // what reviewers actually see
  "affected_files": [...],
  "base_files": {...},           // parent snapshot for context/RAG
  "language": "...",
  "category": "...",
  "severity": "..."
}
```

**Data sources**

- `fixture_small.json`  -  synthetic, self-contained cases for CI and smoke
  testing (positive + negative cases). Not historical data.
- `scripts/fetch_swebench.py`  -  imports real SWE-bench instances from
  HuggingFace, reconstructs the bug-introducing change, and writes
  leakage-controlled entries.

## Leakage prevention

- The reviewer receives only `diff` (and optionally `base_files`).
- `bug_description` and `bug_fix` are never passed to any reviewer by default
  (`hold_out: true`).
- No future repository state, fix commit, or issue text is provided.
- The evaluation simulates what a reviewer would have known at the time.
- Where data volume permits, splits are intended to be repository-level to
  reduce cross-contamination (see `docs/research.md`'s sibling scripts).

## Scoring

A finding is a **true positive** if it matches the gold bug:

1. same file (exact or within `affected_files`, or same directory as a weak
   match), **and**
2. identifier/token overlap between the finding's title/description and the
   gold bug description/fix.

All other findings are **false positives**. An entry is **detected** if at
least one true positive was produced. Negative entries (no gold bug) measure
the false-positive rate. This matching heuristic is deterministic and
documented in `evaluation/metrics.py`.

## Metrics

- **Detection**: precision, recall, F1, bug-detection rate, false-positive rate.
- **Severity**: severity-classification accuracy over matched findings.
- **Reliability**: completion rate, per-entry failures (timeouts, invalid
  output, agent failures) recorded in `failures.jsonl`.
- **Efficiency**: latency, token usage, estimated cost.

## Reproducibility

- Versioned prompts (`prompts/<stage>/vN.txt`); versions recorded in results.
- Immutable experiment directories (`experiments/<timestamp>_<name>/`) that are
  never overwritten, containing `config.yaml`, `predictions.jsonl`,
  `failures.jsonl`, `metrics.json`, `latency.json`, `cost.json`, `summary.md`.
- Config-driven ablation switches (`WORKFLOW_USE_RETRIEVAL`,
  `WORKFLOW_USE_VERIFIER`, `WORKFLOW_USE_SECURITY_AGENT`,
  `WORKFLOW_USE_TESTING_AGENT`) for controlled component removal.
- LLM output is inherently nondeterministic; temperature is configurable and
  documented as a limitation. Never compare runs that differ in model,
  prompt version, or dataset without recording it.

## Limitations

- Matching is heuristic; a finding that describes the bug with different words
  may be scored as a false negative. Manual review of borderline entries is
  recommended for published results.
- The fixture dataset is synthetic and unsuitable for drawing research
  conclusions.
- Human evaluation is not bundled; a protocol/rubric can be added, but results
  must come from actual human raters.
- Execution-based verification is disabled (security); some bug classes that
  only manifest at runtime are harder to confirm.
