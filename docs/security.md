# Security

This system reviews **untrusted third-party code**. It is treated as a
security-sensitive system; the controls below are part of the design, not an
afterthought.

## Repository content is untrusted data

A repository may contain malicious comments, fake "review instructions",
prompt-injection payloads, secrets, or deliberately crafted source code.
Therefore:

- **Repository content is data, never instructions.** Every system prompt
  includes an explicit policy: instructions embedded in diffs, comments, docs,
  or other repository content must be ignored.
- **Instruction hierarchy**  -  system instructions (this project's prompts)
  outrank repository content and tool output; repository content can never
  override them.
- Prompt files are versioned and reviewed; the policy statement is part of each
  stage prompt (see `src/agentic_code_reviewer/prompts/`).

## Secret management

- Credentials are read from the environment or the user config
  (`GITHUB_TOKEN`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`).
- Secrets live in the user config, never in a repository file.
- Logs redact values whose keys look like secrets (`api_key`, `token`,
  `secret`, `password`, `authorization`).
- Errors never embed credentials: provider errors are mapped to our taxonomy
  with a generic message plus a non-secret detail.

## Execution safety

- **Repository code is never executed.** No sandbox, no subprocess execution of
  reviewed code, no tool that runs the diff. Verification is evidence-based:
  diff hunks, line references, conservative pattern matches, identifier
  overlap.
- If execution-based verification is enabled in the future, it must use
  sandboxing with timeouts, no network, no credentials, and least privilege.
  Until then this limitation is reported explicitly.

## GitHub integration

- All GitHub traffic goes through one `GitHubClient` (`github/client.py`).
- Default is **read-only**. Publishing a review requires
  `--publish` **and** a token; the client refuses to publish without one.
- Recommended token scope: minimal read (`repo` read, or fine-grained read-only
  on the target repo). Publishing additionally requires `pull_request: write`.
- Base URLs are configurable (`GITHUB_BASE_URL`) for GitHub Enterprise.

## Network and data egress

- The only outbound calls are to the configured LLM provider and GitHub API.
- Prompts contain diff text and bounded repository context (capped by
  `CONTEXT_CHAR_BUDGET` / `GITHUB_MAX_CONTEXT_BYTES`). Do not review
  repositories you are not authorized to share with the LLM provider.

## Prompt-injection defense summary

1. Fixed system prompts with an explicit untrusted-data policy.
2. Repository content is injected only as `$DIFF$`/`$CONTEXT$` placeholders  - 
   never into system instructions.
3. Structured-output validation rejects anything that does not match the
   expected schema.
4. The aggregator can only down-select from evidence-backed candidates  -  a
   model that is manipulated into "inventing" findings cannot inject them into
   the final review.
5. The verifier requires changed-line evidence before a finding is confirmed.

## Threat model / known limitations

- A malicious diff can still influence *findings* if the model is fooled into
  describing benign code as buggy; the verifier mitigates this but cannot
  eliminate it.
- LLM providers see review inputs; do not review confidential code with an
  external provider.
- Logs may contain file paths and diff excerpts (useful for debugging); ensure
  log destinations are access-controlled.
