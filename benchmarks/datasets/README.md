# Benchmark datasets

This directory holds evaluation datasets for the Agentic Code Reviewer.

## Current contents

- **`fixture_small.json`**  -  synthetic, self-contained cases used for CI and
  smoke testing. These are **not** historical bugs. They exist so the
  evaluation framework can be exercised deterministically (with
  `LLM_PROVIDER=mock`) without network access.
  - `fixture-sql-injection`  -  positive case (security / high)
  - `fixture-off-by-one`  -  positive case (correctness / medium)
  - `fixture-clean-refactor`  -  negative case (no bug; measures false positives)

## Real historical data

Real historical bugs are fetched by the importer:

```bash
python scripts/fetch_swebench.py --output benchmarks/datasets/swebench_review.jsonl --limit 50
```

The importer pulls SWE-bench instances from HuggingFace, reconstructs the
bug-introducing change as a diff, and writes leakage-controlled entries
(`hold_out: true`  -  bug descriptions are never passed to reviewers). See
`docs/research.md` for the dataset-construction and leakage-prevention
methodology.

Downloaded datasets are gitignored; only this README and the fixture file are
committed. **Never commit real benchmark data without confirming licensing.**
