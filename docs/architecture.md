# Architecture

## Overview

The system is a pipeline of typed stages over a single explicit
`:class:`ReviewState``. Each stage consumes the state and produces one validated
pydantic model; no stage passes unstructured text to the next.

```
ReviewRequest (diff + changed files + repo snapshot)
    │
    ▼
┌────────────┐  fatal on failure   ┌──────────────────┐
│  Planner   │────────────────────▶│ Change Analyzer  │
└────────────┘                     └──────────────────┘
        │                                   │
        │ token-aware decomposition         ▼
        ▼                          Context (retrieval or selector)
   diff groups                        │
        │                             ▼
        └────────────┬───────── Correctness ─┐
                     │         Security      │   run in parallel
                     │         ErrorHandling │   (read-only on state)
                     │         Testing       │
                     └───────── Regression  ─┘
                            │
                            ▼
                   Evidence Verifier (deterministic)
                            │
                            ▼
                   Aggregator (dedupe/filter/rank + LLM summary)
                            │
                            ▼
                       Final Review (markdown)
```

## Key decisions

### No orchestration framework
A lightweight internal state machine (see `orchestration/workflow.py`) is
sufficient: the stages are known, the data flow is a DAG, and the failure modes
are bounded. Introducing LangGraph-style orchestration would add a dependency
without adding capability; the workflow is deliberately small and readable.

### Typed stage contracts
Every stage input/output is a pydantic model (`models/schemas.py`,
`models/findings.py`). LLM output is never trusted blindly: it is parsed and
validated (`llm/structured_output.py`), repaired once on failure, and the
failure is recorded if recovery is impossible.

### Parallelism with shared state
Analysis agents run concurrently (security, correctness, error handling,
testing, regression across diff groups) because they only **read** the state
(plan, change summary, context). The orchestrator merges their results and usage
records afterwards. `ReviewState` is never mutated from inside a worker.

### Verification is evidence-based, not execution-based
Repository code is **untrusted input and is never executed**. The verifier
confirms findings by checking the diff itself: is the file changed, is the line
on a changed line, does a conservative category pattern appear on that line, do
the identifiers overlap? This yields a four-level status
(`verified / strongly_inferred / potential / unverified`) and keeps false
positives down.

### Retrieval is replaceable
`retrieval/retriever.py` exposes a tiny interface (index a snapshot, retrieve
top-k). The default implementation is an in-memory cosine index over
symbol-aware chunks with embeddings from the configured provider, falling back
to a deterministic local TF-IDF embedder when no provider key exists  -  so RAG
works offline and a real vector database can be swapped in without touching the
workflow.

### Context is deliberate
Context budgets are explicit (`CONTEXT_CHAR_BUDGET`, `DIFF_TOKEN_BUDGET`).
Retrieval queries emphasize the changed surface (changed files + plan risk
areas). Without a retriever the system degrades to symbol chunks of changed
files  -  never to "everything".

### Failure handling
- Planner failure is **fatal** (reviewing without a plan is worse than no
  review).
- Analysis-agent failures are recorded and degrade only that check.
- Verifier failure degrades verification statuses (findings default to
  unverified and are filtered out downstream).
- Aggregator LLM failure falls back to a deterministic review built from
  verified candidates.
- LLM calls retry transient failures (timeout/rate-limit) with exponential
  backoff and never retry authentication failures; retries are bounded.

## Observability

Every review gets a `review_id` correlation id bound via a context variable; all
log records carry it. Logs are structured JSON (`LOG_FORMAT=json`) and include
stage status, latency, token usage, and estimated cost  -  enough to reconstruct
a failed review. Secrets are never logged (see `observability/logging.py`).

## Cost tracking

Every stage records token usage and an estimated USD cost from a per-model
pricing table (`llm/cost.py`). Cost figures are estimates for observability,
not billing truth.
