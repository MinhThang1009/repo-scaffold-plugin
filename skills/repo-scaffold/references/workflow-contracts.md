# Workflow contracts

Read this reference before installing or modifying GitHub Actions workflows.

Install workflows only for a verified GitHub.com repository. Give every job the
least privilege, set `persist-credentials: false` for checkout unless needed,
pin every external action to a verified full SHA, and keep generated workflows
valid for `pull_request` and `merge_group` whenever their check can be required.
Use `cancel-in-progress: false` for required-check concurrency.

The reviewed runtime policy is the single source of truth. Do not duplicate
supported versions in prose or workflow YAML, retain the scheduled compatibility
canary, load `.github/ci-toolchain.json` through the bundled
`ci_toolchain.py run-markdownlint` tooling, retain the scheduled/manual drift
canary, and must not install an unreviewed release automatically.

Keep `scheduled compatibility canary`, `do not duplicate supported versions`,
and `scheduled/manual drift canary` as enforceable policy outcomes.

- Documentation: install the documentation contract with markdownlint and
  `validate_scaffold.py`; obtain its runtime from `ci-toolchain.json`.
- PR template: trust only the base SHA on `pull_request_target`; never execute
  PR head code, and require one trusted marker plus all required headings/items.
- Links, community-health, and freshness: keep network/upstream checks advisory;
  reminder workflows run only on trusted scheduled/manual events and maintain one
  idempotent issue when Issues are enabled.
- CI: create or adapt a stack-valid workflow with real commands and a stable
  aggregate gate. Do not require it while the scaffold sentinel remains. Use one
  machine-readable runtime policy and dependency caching appropriate to the stack.
- Release: keep build, eligible attestation, and publication as separate jobs.
  Never expose OIDC or write permissions to a project build step. Do not combine
  release-please with a competing tag dispatcher.
- Optional dependency review, CodeQL advanced setup, Scorecard, auto-merge,
  commitlint, stale, labeler, and release notes require their documented
  eligibility, permissions, and user approval. Skip an option rather than
  installing a known-failing gate.

Before making any context required, confirm a real, unique producer, expected
event coverage, no Check Run/commit-status collision, and the exact GitHub App
identity. A skipped job or a workflow filename is not sufficient evidence.
