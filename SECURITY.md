# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.1.x   | Yes       |

Only the latest release receives security fixes. Backports are considered on
request.

## Reporting a vulnerability

Please do **not** open a public issue for a security vulnerability.

Report it privately through the repository's **Security advisories** page
(GitHub: the "Security" tab, then "Report a vulnerability"). Include:

- A short description of the vulnerability and its impact.
- The affected version(s) and configuration.
- Steps to reproduce, including the smallest diff or repository content that
  triggers it.
- Any proposed fix, if you have one.

You can expect an acknowledgement within 5 business days and a detailed
response (including next steps or a fix timeline) shortly after. We ask that
you keep the issue confidential until a fix is released.

## Security design

- **No runtime execution**: the reviewer never imports, executes, or runs
  reviewed application code. No dynamic testing, no importing arbitrary
  application modules, no running repository scripts during analysis.
- **Untrusted data**: diffs, source code, comments, documentation, repository
  files, and commit messages are treated as untrusted data in every prompt.
  Repository content can never override reviewer instructions.
- **Prompt-injection protection**: repository content cannot redefine the
  reviewer's behavior; LLM output is schema-validated and repaired before it
  can influence the review.
- **Secrets handling**: API keys are read from the environment or CLI
  configuration and are never logged or written to review artifacts. Log
  records redact secret-looking fields.
- **Non-UTF8 robustness**: git output and file content are decoded with
  `errors="replace"` so malformed bytes can never crash a review.

## Reporting a non-security bug

Use the normal issue tracker for bugs that are not security vulnerabilities.
Include the review output, the diff, and any error messages.
