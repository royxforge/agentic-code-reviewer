# AI Code Editor Prompt — Knowledgebase + Token-Usage Reduction

> Paste the block below into your AI code editor (Cursor, Claude Code, Copilot,
> Windsurf, ...) pointed at this repository. It describes exactly what was
> implemented, how it works, and how to verify it. If you are applying it to a
> fresh checkout, the instructions are self-contained; if the changes are
> already present, it doubles as a review checklist.

---

```text
You are working in the `agentic-code-reviewer` repository (Python 3.11+,
pydantic, typer, Textual). Two pieces of work are in scope: (1) a persistent
KNOWLEDGEBASE feature, and (2) TOKEN-USAGE REDUCTION across the LLM workflow.

Follow the project's existing conventions: typed pydantic models, versioned
prompt templates under src/agentic_code_reviewer/prompts/<stage>/vN.txt with
$TOKEN$ substitution (never embed prompt text in Python), structured outputs,
best-effort degradation (a broken optional feature must never break a review),
and no new third-party dependencies unless truly required.

Run the checks before finishing: `pytest`, `ruff check src/ tests/`,
`mypy src/agentic_code_reviewer`. The suite is fully offline (mock clients).

------------------------------------------------------------------
PART 1 — KNOWLEDGEBASE
------------------------------------------------------------------

Goal: persistent, cross-run repository knowledge injected into analysis
prompts, so the reviewer remembers past verified findings, repo conventions,
and docs instead of re-deriving everything each run. Retrieval must cost ZERO
extra tokens and work offline: use deterministic token-overlap scoring, never
an LLM call or an embedding endpoint.

Create a new package src/agentic_code_reviewer/knowledge/ with:

1. models.py
   - `KnowledgeKind` StrEnum: rule, finding, doc.
   - `KnowledgeEntry` (pydantic): kind, title, content, repository ("" =
     applies to all repos), category (optional review category, e.g.
     "security"), source_file, source ("manual" | "auto_capture" | "docs"),
     tags, created_at (UTC iso). A content-derived `entry_id()` (sha256 of
     kind/repo/category/title/content, first 16 hex) for dedup. An
     `applies_to(repository)` helper.

2. store.py
   - `KnowledgeStore(directory=None)`; default directory =
     config_dir()/knowledge (config_dir comes from
     agentic_code_reviewer.config.runtime_config). JSONL persistence, one file
     per repo slug (`_global.jsonl` for the "" repository). All file I/O is
     best-effort and never raises (a corrupt file is skipped).
   - `entries(repository)` -> all entries (global + repo-scoped).
   - `add(entry)` (dedup by entry_id), `add_many(entries)` -> count written,
     `remove(repository, entry_id)` -> bool.
   - `search(query, repository, *, top_k, category)` -> top-k entries ranked by
     token overlap between the query and (title + content + tags), with a
     +2.0 boost when entry.category == the requested category.

3. capture.py
   - `finding_to_entry(finding, repository)` -> KnowledgeEntry(kind=finding).
   - `capture_review_findings(store, findings, repository, *, min_severity=
     Severity.MEDIUM)` -> count written. Only findings whose
     evidence_status is CONFIRMED or STRONGLY_SUPPORTED and severity >=
     min_severity are captured (repo memory).

4. render.py
   - `render_knowledge(entries, *, max_chars=1200, repository="")` -> a compact
     `$KNOWLEDGE$` block: one bullet per entry ("- [category] title: content
     snippet"), truncated to max_chars; returns "(no knowledgebase entries for
     this repository)" when nothing applies. With an empty `repository`
     argument show everything (used by CLI/tests).

Settings (src/agentic_code_reviewer/config/settings.py), all env-alias style
like the existing fields:
   - knowledge_enabled: bool = True (KNOWLEDGE_ENABLED)
   - knowledge_dir: Path = Path("") (KNOWLEDGE_DIR; empty -> config dir)
   - knowledge_top_k: int = 4 (KNOWLEDGE_TOP_K)
   - knowledge_max_chars: int = 1200 (KNOWLEDGE_MAX_CHARS)
   - knowledge_auto_capture: bool = False (KNOWLEDGE_AUTO_CAPTURE)
   - analysis_max_tokens: int = 4096 (ANALYSIS_MAX_TOKENS)
   - use_prompt_caching: bool = True (USE_PROMPT_CACHING)

Workflow integration (src/agentic_code_reviewer/orchestration/workflow.py):
   - Workflow.__init__ accepts `knowledge_store: KnowledgeStore | None`; build
     one from settings.knowledge_dir when knowledge_enabled (else None).
   - After `_build_context` add `_load_knowledge(state)`: build a retrieval
     query from the plan query + the diff's ADDED lines (the added lines make
     relevance far better and the query is only used for local scoring, never
     sent to the LLM), search the store with top_k=knowledge_top_k, and put
     the entries on `state.knowledge_entries`. Never raise; degrade to [].
   - After aggregation add `_capture_knowledge(state)`: when
     knowledge_auto_capture and the store exist, call capture_review_findings
     on state.findings. Never raise.
   - ReviewState gains `knowledge_entries: list = field(default_factory=list)`.

Prompt assembly (src/agentic_code_reviewer/agents/base.py):
   - `AnalysisAgent._assemble` must pass `KNOWLEDGE=render_knowledge(...)` so
     every analysis agent receives the block (cap with settings.knowledge_max_chars).
   - Add a `$KNOWLEDGE$` token section to the TASK of every analysis prompt
     template (the ~22 files under prompts/ that the analysis agents use,
     including testing/v2 and api_contract/v2): right after the `$CONTEXT$`
     block, e.g.:
       Knowledgebase (relevant past verified findings, rules and decisions for this repository):

       $KNOWLEDGE$
   - Because render() raises when a template references an unsupplied token,
     update tests/unit/test_prompts.py: the requirement_alignment render test
     must pass KNOWLEDGE="", and add a test that a security prompt renders a
     supplied KNOWLEDGE value.

CLI (src/agentic_code_reviewer/cli/): add a `knowledge` typer sub-app
(registered on the main app) with:
   - `acr knowledge add --title ... --content ... [--repo] [--kind rule] [--category] [--tags a,b]`
   - `acr knowledge list [--repo] [--kind]`
   - `acr knowledge remove <entry-id>`
   - `acr knowledge search "<query>" [--repo] [--top-k]`
   - `acr knowledge index-docs <path> [--repo]` — split markdown/rst files into
     sections at '#' headings and add each as a doc entry.

------------------------------------------------------------------
PART 2 — TOKEN-USAGE REDUCTION
------------------------------------------------------------------

Goal: cut tokens on the 22 parallel analysis calls without changing review
quality. All of these must keep the existing test suite green (the tests use
the deterministic AdaptiveMock).

1. Category-focused diff (the biggest win): analysis agents currently each
   receive the FULL group diff. Add `diff_text_for_category(files, category,
   *, max_chars=None)` to src/agentic_code_reviewer/analysis/diff.py:
   - Uses CATEGORY_PATTERNS (from analysis/rules.py) for the category; keeps
     only DiffFile objects whose ADDED lines match any pattern.
   - Falls back to the FULL diff when the category has no patterns or nothing
     matched (never starve an agent of the change).
   - When max_chars is set, truncate conservatively by dropping whole files
     from the end, then a hard character cut.
   - Wire it in workflow._run_analysis_agents: pass
     `diff_text_for_category(group, agent.category)` instead of
     diff_to_text(group). The evidence verifier still checks findings against
     the full state.request.diff_text, so trimming only affects what the agent
     SEES, never what gets verified.

2. Compact plan / change-summary digests: `_assemble` currently passes full
   model_dump_json() for the plan and change summary to every agent. Add
   `compact_plan(plan)` and `compact_change_summary(summary)` (in
   agents/base.py) that emit only the fields the prompts actually read
   (plan: summary, risk_areas, required_checks, affected_components;
   change summary: purpose, api_changes, affected_callers, affected_tests,
   known_facts, inferences, backward_compatibility). Use them in _assemble.

3. Anthropic prompt caching: in src/agentic_code_reviewer/llm/anthropic_client.py,
   when settings.use_prompt_caching is true, send the system block as
   `[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]`
   so the identical system prompt is shared across the parallel agents (one
   full read + cache reads instead of 22 full reads). Keep NOT_GIVEN for an
   empty system. LLMUsage already tracks cached_tokens.

4. Per-agent output cap: `AnalysisAgent._collect` should default to
   `settings.analysis_max_tokens` (4096) instead of falling through to the
   global MAX_TOKENS (8192) when max_tokens is None.

------------------------------------------------------------------
ACCEPTANCE CHECKS
------------------------------------------------------------------
- New unit tests: knowledge store CRUD + dedup + repo scoping, search ranking
  + category boost, render_knowledge cap + repo filter, capture severity/
  evidence filter, diff_text_for_category (keeps relevant files, falls back to
  full, truncates), workflow-level: a relevant entry is injected into the
  security agent's prompt, and KNOWLEDGE_AUTO_CAPTURE=true persists a verified
  finding. Put them in tests/unit/test_knowledge.py.
- `pytest` (full suite, offline), `ruff check src/ tests/`, and
  `mypy src/agentic_code_reviewer` all pass.
- Update README (features list, Repository Structure, a "Knowledgebase" and a
  "Token Usage" section, Configuration Reference table) and CHANGELOG.md
  ([Unreleased] section).

Do not ship any of this unless all checks pass.
```

---

## What's already implemented in this repo

If you're reading this inside the repo, the feature is implemented and verified:
`405 tests passing`, `ruff` and `mypy` clean. Summary of what was done:

| Area | Files |
|---|---|
| Knowledge models / store / capture / render | `src/agentic_code_reviewer/knowledge/` |
| Dismiss-to-knowledge feedback loop | `knowledge/dismiss.py`, `cli/_dismissal_recorder`, `workflow._apply_dismissals` |
| Settings knobs | `config/settings.py` (`KNOWLEDGE_*`, `ANALYSIS_MAX_TOKENS`, `USE_PROMPT_CACHING`, `AGGREGATOR_MAX_FINDINGS`, `LLM_CACHE_ENABLED/DIR`) |
| Workflow wiring (load + auto-capture) | `orchestration/workflow.py`, `orchestration/state.py` |
| Prompt injection (`$KNOWLEDGE$`) | `agents/base.py`, 22 analysis prompt templates |
| Category-focused diff | `analysis/diff.py::diff_text_for_category`, `workflow._run_analysis_agents` |
| Compact plan/summary digests | `agents/base.py::compact_plan/compact_change_summary` |
| Anthropic prompt caching | `llm/anthropic_client.py` |
| LLM response cache | `llm/cache.py`, `llm/client.py::complete_with_retry` |
| Aggregator top-N cap | `agents/aggregator.py` |
| Cross-file taint | `analysis/taint.py::build_cross_file_summaries`, `agents/verifier.py` |
| CLI | `cli/knowledge.py` (`acr knowledge add/list/remove/search/index-docs`) |
| Tests | `tests/unit/test_knowledge.py`, `tests/unit/test_llm_cache.py`, `tests/unit/test_taint.py`, `tests/unit/test_prompt_regressions.py`, `tests/unit/test_aggregation.py`, `tests/unit/test_prompts.py` |
| Docs | `README.md`, `CHANGELOG.md` |
