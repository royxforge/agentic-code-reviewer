# Contributing to agentic-code-reviewer

Thanks for contributing. This guide covers the workflow, tooling, and
expectations for changes to **agentic-code-reviewer** (`acr`).

## Development setup

Requirements: Python 3.11 or newer and `git`.

```bash
git clone <your-fork-url>
cd agentic-code-reviewer

# Create and activate a virtual environment
python -m venv .venv
# Windows (PowerShell):
#   .venv\Scripts\Activate.ps1
# macOS / Linux:
#   source .venv/bin/activate

# Install the package in editable mode with all extras
pip install -e ".[all]"
```

The `acr` command is then available from the virtual environment.

## Running checks

Use the same gates the CI uses:

```bash
# Full test suite (376+ tests)
python -m pytest tests/ -p no:cacheprovider

# Lint
ruff check src/ tests/ scripts/

# Static types
python -m mypy src/agentic_code_reviewer --ignore-missing-imports

# A keyless end-to-end run with the mock provider
acr review-local . --provider mock
```

Keep all three gates green before opening a pull request.

## Project layout

- `src/agentic_code_reviewer/orchestration/` - the review workflow (plan,
  analyze in parallel, verify, aggregate, correlate).
- `src/agentic_code_reviewer/agents/` - one reviewer agent per category, plus
  the deterministic verifier, aggregator, and dead-code checker.
- `src/agentic_code_reviewer/analysis/` - deterministic layers: rules, AST
  checks, taint engine, repository snapshot, severity model, aggregation.
- `src/agentic_code_reviewer/ui/` - the Textual terminal UI.
- `src/agentic_code_reviewer/cli/` - the Typer CLI and exporters.
- `tests/` - unit and integration tests, including Textual screen tests.

## Adding or changing a review category

1. Add the category to `ALLOWED_CHECKS` in
   `src/agentic_code_reviewer/models/review.py`.
2. Create the agent in `src/agentic_code_reviewer/agents/` (subclass of
   `AnalysisAgent`) and register it in `_ANALYSIS_AGENTS` in
   `src/agentic_code_reviewer/orchestration/workflow.py`.
3. Add a prompt under `src/agentic_code_reviewer/prompts/<category>/v1.txt`.
   The prompt must treat diff and repository content as untrusted data.
4. Add deterministic patterns in `src/agentic_code_reviewer/analysis/rules.py`
   or `category_rules.py` so the verifier can confirm legitimate findings.
5. Add tests: at least 3 positive, 2 negative, and 1 edge case per category,
   following `tests/unit/test_new_categories.py`.

## Rules for findings

- Every finding must point at a changed line with evidence. The verifier
  rejects claims that contradict repository state.
- Style nitpicks are prohibited. No formatting, naming-preference, or
  "I would write this differently" findings unless there is a concrete
  defect or risk.
- The reviewer never executes application code. Do not add dynamic testing,
  arbitrary imports of reviewed modules, or repository-script execution.
- New behavior must be configurable via settings; the mock provider must
  keep working so keyless runs and benchmarks stay reproducible.

## Pull request checklist

- [ ] `pytest tests/` passes.
- [ ] `ruff check src/ tests/ scripts/` is clean.
- [ ] `mypy src/agentic_code_reviewer` reports no issues.
- [ ] New categories follow the positive / negative / edge test rule.
- [ ] README or docs are updated if user-facing behavior changed.
- [ ] No em dashes (U+2014) in documentation or docstrings; use a hyphen
      (`-`) or restructure the sentence.

## Code of conduct

All contributors are expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
