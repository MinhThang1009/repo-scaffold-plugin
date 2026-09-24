# Workflow contracts

Read this reference before installing or modifying GitHub Actions workflows.

Install workflows only for a verified GitHub.com repository. Give every job the
least privilege, set `persist-credentials: false` for checkout unless needed,
pin every external action to a verified full SHA and job/service container images
to a verified full SHA-256 digest, bound supplied workflow input count and total
bytes, and keep generated workflows
valid for `pull_request` (or a trusted `pull_request_target` equivalent) and
`merge_group` whenever their check can be required.
Use `cancel-in-progress: false` for required-check concurrency.
For a GitHub.com project with a runnable test or lint command, workflow setup
must end with a configured CI workflow or an explicit user decision to defer it.
Every applicable approved asset must pass the workflow-installation preflight
with its exact `--workflow` inputs, and every documented companion must be
installed and verified. Optional assets may remain not applicable, but their
omission must be recorded rather than silently skipped. The generic CI asset's
test job must run on `matrix.os`, whose reviewed policy may include
`ubuntu-latest`, `windows-latest`, and `macos-latest`.
External `docker://` workflow references must use a full SHA-256 digest; the
workflow-installation preflight rejects mutable container tags.
Docker container actions are external action requirements, and selected policy
approval fails closed unless the reference is explicitly representable and
allowed.
Selected-actions preflight must follow GitHub's validated `selected_actions_url`
so organization and enterprise policy overrides cannot be replaced by a more
permissive repository endpoint.
Any asset declaring `pull_request_target` also requires a separate review of the
applicable GitHub Actions workflow-execution policy. GitHub's default policy for
public repositories is scheduled to block that event on November 2, 2026 unless
an applicable policy explicitly allows it. The workflow-installation preflight
reads inherited Actions policies and fails closed unless an active event rule
allows the event for every supplied workflow path. Inspect the [official
guidance](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target)
when the preflight reports that proof is unavailable; selected-actions approval
does not prove event-policy eligibility, and this plugin does not change remote
Actions policy.
Local reusable-workflow call paths must use canonical repository-relative POSIX
paths without traversal, backslash, or control characters. Calls must be
supplied from the same workflow directory as their caller; a matching basename
from another directory is ambiguous.
Each supplied called workflow must declare `workflow_call`, and local call loops
are rejected. The supplied graph must stay within GitHub's 10 workflow levels
and 50 unique nested workflows per top-level caller.
Local `./...` action references must use canonical repository-relative paths;
traversal, backslash, and control-character forms are rejected.

The reviewed runtime policy is the single source of truth. Do not duplicate
supported versions in prose or workflow YAML, retain the scheduled compatibility
canary, load `.github/ci-toolchain.json` through the bundled
`ci_toolchain.py run-markdownlint` tooling, retain the scheduled/manual drift
canary, reconcile one durable reminder issue when a concrete policy canary
detects drift, and must not install an unreviewed release automatically.
When CI downloads the reviewed standalone ShellCheck or actionlint archive,
the download must use HTTPS, verify the policy digest, and use bounded retries
so transient release-service failures do not turn into avoidable gate failures.
The link checker must also use bounded retries with backoff for transient
upstream HTTP failures while continuing to fail on unresolved links.
Maintenance readers must bound repository-controlled workflow, release-config,
and claim-source files before decoding them, then fail closed on oversized or
invalid UTF-8 input. The action-pin synchronizer also caps its workflow inventory
at 500 files and 64 MiB in total, so a large repository cannot exhaust the
maintenance runner while preparing a PR. Because its manual trigger can select a
branch or tag, its checkout must pin the synchronizer to
`${{ github.event.repository.default_branch }}` with
`persist-credentials: false` before running repository code; the PAT-backed PR
mutation must never consume code from the selected ref. Its concurrency group
must be repository-scoped and non-cancelling because every run updates the same
maintenance branch. Its static PR body must live at
`.github/action-pin-sync-pr-body.md`, pass
`python scripts/markdown_body_preflight.py --body-file
.github/action-pin-sync-pr-body.md`, and be supplied to the action with
`body-path` so the checked file is the file sent to GitHub.

Keep `scheduled compatibility canary`, `do not duplicate supported versions`,
and `scheduled/manual drift canary` as enforceable policy outcomes.

- Documentation: install the documentation contract with markdownlint and
  `validate_scaffold.py`; obtain its runtime from `ci-toolchain.json`.
- PR template: trust only the base SHA on `pull_request_target`; never execute
  PR head code, and require one trusted marker plus all required headings/items.
  Dependabot exemptions require a bot user type. A Release Please exemption
  requires its branch prefix, a head repository matching the base repository,
  and either a bot user type or the base repository owner's account, because the
  configured Release Please token can open PRs as that owner. Both exemptions
  must run Markdown body preflight first; a branch name alone is never an
  exemption.
- Branch protection: required-check producers must be unique, executable, and
  event-compatible. A trusted `pull_request_target` producer may satisfy
  protected default-branch pull-request coverage only when its exact default
  branch filter, unchanged workflow blob at the representative PR's base, and
  applicable Actions event policy are verified. Merge new or changed target
  workflows before running this preflight. Job or step `if` and
  `continue-on-error` controls that can skip or mask the gate must fail closed.
- Links, community-health, and freshness: keep network/upstream checks advisory;
  reminder workflows run only on trusted scheduled/manual events with a
  five-field POSIX cron schedule with an optional valid IANA timezone, and a
  valid manual trigger shape. An empty `workflow_dispatch` is allowed; when
  inputs are declared, it must contain at most 25 named input mappings using
  only supported fields and input types. Maintain one idempotent issue when
  Issues are enabled. Before every Issue `create` or `edit` that passes a
  Markdown body file, run
  `python scripts/markdown_body_preflight.py --body-file <same-path>` and fail
  before the GitHub mutation when the checker rejects hard-wrapped prose.
  Reminder jobs and reconciliation steps must not use `if`, `needs`,
  `strategy`, `continue-on-error`, `environment`, `timeout-minutes`,
  `background`, `parallel`, `wait`, `wait-all`, or `cancel` controls that can
  skip or mask the preflight; the policy-drift job may retain its required
  schedule condition, dependencies, and concurrency declaration.
  Reconciliation shells must retain `errexit`, `nounset`, and `pipefail`; they
  may not disable them before or after the body preflight.
  Serialize each reminder's shared
  repository state with a repository-scoped, non-cancelling concurrency group.
  Since `workflow_dispatch` can target a branch or tag, every manually
  dispatched Issue-writing reminder must check out
  `${{ github.event.repository.default_branch }}` with
  `persist-credentials: false` before running repository code. This keeps the
  checker and tracker on the trusted default branch.
  The preflight evaluates each job's effective `issues: write` permission, so a
  checkout in a different job cannot satisfy the requirement; job-level reusable
  workflows are rejected, and repository-local actions require their trusted
  checkout to precede execution.
  Community-health tracker registry paths must stay inside the repository and
  reject traversal, control characters, links, or reparse points before they
  are read. Directory inventories
  are bounded to 10,000 entries so large repositories fail closed.
  The community-health checker accepts only exit statuses 0, 1, and 2; any
  other status must fail closed before clean/stale branching or Issue mutation.
  Shipped reminders use stable, distinct repository prefixes:
  `repo-scaffold-community-health-${{ github.repository }}`,
  `repo-scaffold-official-docs-${{ github.repository }}`,
  `repo-scaffold-freshness-${{ github.repository }}`, and
  `repo-scaffold-ci-policy-drift-${{ github.repository }}`. A renamed manual
  branch workflow therefore cannot race its scheduled run.
  The version-1 freshness tracker registry supports only known top-level fields
  and exact `path`/`locks` requirement-source fields; lock paths cannot
  reference requirement sources, and unknown fields must fail closed instead of
  being ignored. The freshness checker bounds each tracked workflow read to
  5 MiB and reports an indeterminate check when a file exceeds that cap. It
  resolves independent upstream inputs with a bounded worker pool, preserving
  deterministic findings and fail-closed errors. It also caps the tracked
  workflow inventory at 500 files and 64 MiB, and the distinct action
  repositories it resolves at 500, so large or hostile repositories cannot
  force unbounded local reads or upstream lookups.
  The tracker registry is also capped at 500 tracked input paths, including
  requirement locks. Each requirements file may contain at most 512 unique
  direct pins, and all tracked requirement sources and locks together at most
  4096 pins. Requirements and tracked JSON policy inputs are bounded to 1 MiB
  before parsing.
  Every reminder mutation must use an explicit repository binding, and every
  freshness `create` or `edit` mutation must use a durable `--body-file` (with a
  non-empty `--title` for `create`). The reconciliation job must have effective
  `issues: write` permission with read-only contents access, and must be named
  `freshness-audit` with a 15-minute timeout. If the reconciliation job
  declares job-level permissions, it must retain effective `contents: read`
  and `issues: write` access. Freshness API lookups and Issue
  mutations must bind directly to the runner's `$GITHUB_REPOSITORY` value, with
  `github.com/` explicit for `gh issue --repo`; hard-coded repositories and
  overrides of that variable must fail closed. The lookup must use the GitHub
  Issue Search API as a bounded GET for open Issues, with `is:issue`, `in:body`,
  the freshness marker, and `per_page=2`; it returns at most the first two
  matching issue numbers so reruns remain idempotent without an unbounded
  pagination loop. It must use `[.items[].number] | join(" ")` so the bounded
  result is one shell-safe line, exactly one lookup invocation, and no extra
  `gh api` arguments. The lookup
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
  The audit step may use only the canonical fallback-report and checker-status
  `printf` commands; arbitrary output or file redirection must fail closed.
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
  canonical full-SHA references and inputs, including the exact default-branch
  checkout ref and `persist-credentials: false`, plus `python-version: 3.x`.
  Both preparation actions must appear exactly once,
  before exactly three canonical run steps (audit, summary, and
  reconciliation), so the checker has its repository files and Python runtime.
  Any auxiliary direct job must be an inert `steps: []` mapping with no execution
  configuration.
  Any
  repository, path, token, cache, or other input override must fail closed. An
  arbitrary or omitted checkout ref must also fail closed for manually
  dispatched Issue-writing workflows.
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
  permits only the canonical freshness command set in the reconciliation job,
  including only the reviewed `printf` invocations; unreviewed executables,
  script interpreters, command substitutions, and
  path-qualified programs must fail closed. Shell parameter expansions may use only
  the canonical report, repository, status, and issue-number references. Issue
  mutations may use only their
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
  top-level fields. Exception paths must be canonical POSIX paths without
  traversal or control characters. Exceptions must also carry a bounded review date and
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
event coverage (including a trusted `pull_request_target` equivalent only for
the verified default branch), no Check Run/commit-status collision, and the
exact GitHub App identity. A skipped job or a workflow filename is not
sufficient evidence.
