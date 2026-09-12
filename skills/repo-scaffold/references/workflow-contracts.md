# Workflow contracts

Read this reference before installing or modifying GitHub Actions workflows.

Install workflows only for a verified GitHub.com repository. Give every job the
least privilege, set `persist-credentials: false` for checkout unless needed,
pin every external action to a verified full SHA and job/service container images
to a verified full SHA-256 digest, and keep generated workflows
valid for `pull_request` and `merge_group` whenever their check can be required.
Use `cancel-in-progress: false` for required-check concurrency.
External `docker://` workflow references must use a full SHA-256 digest; the
workflow-installation preflight rejects mutable container tags.

The reviewed runtime policy is the single source of truth. Do not duplicate
supported versions in prose or workflow YAML, retain the scheduled compatibility
canary, load `.github/ci-toolchain.json` through the bundled
`ci_toolchain.py run-markdownlint` tooling, retain the scheduled/manual drift
canary, reconcile one durable reminder issue when a concrete policy canary
detects drift, and must not install an unreviewed release automatically.

Keep `scheduled compatibility canary`, `do not duplicate supported versions`,
and `scheduled/manual drift canary` as enforceable policy outcomes.

- Documentation: install the documentation contract with markdownlint and
  `validate_scaffold.py`; obtain its runtime from `ci-toolchain.json`.
- PR template: trust only the base SHA on `pull_request_target`; never execute
  PR head code, and require one trusted marker plus all required headings/items.
- Branch protection: required-check producers must be unique, executable, and
  event-compatible; job or step `if` and `continue-on-error` controls that can
  skip or mask the gate must fail closed.
- Links, community-health, and freshness: keep network/upstream checks advisory;
  reminder workflows run only on trusted scheduled/manual events with a
  five-field POSIX cron schedule with an optional valid IANA timezone, and a
  valid manual trigger shape. An empty `workflow_dispatch` is allowed; when
  inputs are declared, it must contain at most 25 named input mappings using
  only supported fields and input types. Maintain one idempotent issue when
  Issues are enabled. Serialize each reminder's shared
  repository state with a repository-scoped, non-cancelling concurrency group.
  The version-1 freshness tracker registry supports only known top-level fields
  and exact `path`/`locks` requirement-source fields; lock paths cannot
  reference requirement sources, and unknown fields must fail closed instead of
  being ignored.
  Every reminder mutation must use an explicit repository binding, and every
  freshness `create` or `edit` mutation must use a durable `--body-file` (with a
  non-empty `--title` for `create`). The reconciliation job must have effective
  `issues: write` permission with read-only contents access, and must be named
  `freshness-audit` with a 15-minute timeout. If the reconciliation job
  declares job-level permissions, it must retain effective `contents: read`
  and `issues: write` access. Freshness API lookups and Issue
  mutations must bind directly to the runner's `$GITHUB_REPOSITORY` value, with
  `github.com/` explicit for `gh issue --repo`; hard-coded repositories and
  overrides of that variable must fail closed. The lookup must be a paginated
  GET of open Issues, filter non-PR bodies for the freshness marker, and return
  issue numbers so reruns remain idempotent. It must use the canonical
  marker-filtering JQ expression and no extra `gh api` arguments. The lookup
  result must be captured and flow into the Issue number passed to a `close` or
  `edit` mutation, directly or through an issue-number array; logging or testing
  the result alone is insufficient. The reconciliation shell must start with
  `set -euo pipefail` and may not later disable any of those options. The
  reconciliation job and its steps may not use `if`, `needs`, `strategy`,
  `environment`, `concurrency`, `snapshot`, `cache-mode`, or
  `continue-on-error`; steps also may not use `background`, `parallel`, `wait`,
  `wait-all`, `cancel`, or `timeout-minutes`, which could silently skip,
  duplicate, or mask the reminder. The checker exit status must drive the
  clean/stale split: close the
  existing issue when clean, and edit or create the report when stale before
  failing. `CHECKER_EXIT` must be bound to the audit step's `checker_exit`
  output, and the audit output must derive from the checker's exit status.
  The audit must disable `errexit` while running the checker, capture its
  status, and restore `errexit` before publishing that output.
  The JSON and Markdown checker outputs must be exactly
  `$RUNNER_TEMP/freshness.json` and `$RUNNER_TEMP/freshness.md`; the reconciliation
  must validate the same marker with `grep` before branching on checker status or
  mutating an Issue, and reject multiple open marker issues.
  A `close` or `edit` Issue argument must be the unmodified lookup result or its
  first array element; shell defaults and parameter transformations must fail closed.
  The audit step's `GITHUB_TOKEN` and reconciliation step's `GH_TOKEN` must both
  bind to `${{ github.token }}`; runner output and temporary-report paths may
  not be overridden, including through shell assignments or parameter-expansion
  writes. `PYTHONPATH`,
  `PYTHONHOME`, and `PYTHONSTARTUP` may not be supplied to the checker. Shell
  assignments to process and GitHub CLI configuration variables such as
  `GIT_SSH_COMMAND`, `GH_CONFIG_DIR`, `HOME`, and `LD_PRELOAD` are also
  rejected. No other workflow, job, or step environment variables may be
  supplied.
  GitHub expressions are rejected inside freshness `run` blocks; only the exact
  YAML token bindings and repository-scoped concurrency expressions are allowed.
  The bound `GITHUB_TOKEN` and `GH_TOKEN` must not be referenced from a freshness
  `run` block; `gh` must inherit them only through the exact YAML bindings.
  The reminder job must run on `ubuntu-latest` with Bash as its effective shell;
  non-Bash runner or shell overrides, workflow/job containers, and services must
  fail closed. Its reviewed checkout and Python setup actions must retain the
  canonical full-SHA references and inputs, `persist-credentials: false` and
  `python-version: 3.x`; any
  repository, ref, path, token, cache, or other input override must fail closed.
  The job summary must publish only the checked Markdown report with
  `cat "$RUNNER_TEMP/freshness.md" >> "$GITHUB_STEP_SUMMARY"`.
  Within the
  reconciliation job, the audit must complete before the lookup, and the lookup
  must complete before any Issue mutation. Pipeline, background, and
  short-circuit operators (`|`, `&`, `|&`, `&&`, and `||`) are rejected around
  these phases. Shell negation (`!`) is also rejected in freshness commands.
  Freshness must retain the optional audit Markdown output as the body file in
  the same job; direct REST issue
  mutations through `gh api`, state-changing calls through known direct HTTP
  clients (`curl`, `wget`, and PowerShell REST cmdlets), path-qualified `gh`
  executables, and shell wrappers, aliases, or function definitions that can
  hide or shadow GitHub commands are ambiguous and must fail closed. The audit
  must not alter command lookup through `PATH`, `BASH_ENV`, `ENV`, or the
  shell's command hash. The audit must run from the
  checkout root with `--repository-root .`; if a `--tracker-registry` override
  is present, it must name `.github/freshness-trackers.json`. Directory-changing
  commands and workflow, job, or step `working-directory` overrides must fail
  closed. Job-level reusable-workflow calls must also fail closed so all
  freshness commands and Issue mutations remain directly inspectable. Freshness
  permits only the canonical freshness command set in the reconciliation job;
  unreviewed executables, script interpreters, command substitutions, and
  path-qualified programs must fail closed. Issue mutations may use only their
  reviewed `--repo`, `--comment`, `--title`, and `--body-file` options; extra
  mutation flags and unreviewed exit statuses must fail closed. `printf` formats
  must be literal and may not use shell expansion, `%n`, or `-v`. Local title
  and issue number state must use the canonical initialization and cannot be
  reseeded or reordered. Freshness
  must retain the Release Please schema tracker and CI-toolchain policy tracker
  shipped with the scaffold so installed inputs receive the same reminder
  coverage as action pins.
  The checked-in registry must retain every shipped workflow, release, allowlist,
  and requirement input; do not empty a category to suppress a check.
  Code-scanning allowlists must use only the `schema-version` and `allowlist`
  top-level fields. Exceptions must also carry a bounded review date and
  be tracked by freshness; do not install the code-scanning gate without its
  matching allowlist and freshness reminder.
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
