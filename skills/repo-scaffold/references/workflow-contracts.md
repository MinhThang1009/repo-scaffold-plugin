# Workflow contracts

Release and tool version validation uses ASCII numeric identifiers. SemVer
numeric prerelease identifiers cannot contain leading zeros; build metadata
may contain leading zeros. Link-health comparison exceptions validate both
the prospective manifest version and the previous tag before inspecting Git
tags or appending an exact URL to `.lycheeignore`. Preserve existing ignore
rules and keep repeated valid exceptions idempotent. A version-validation pass
does not establish release/tag authorization or remote link availability.

Read this reference before installing or modifying GitHub Actions workflows.
The code-scanning gate's polling controls must be finite and bounded before
network access or sleeping: integer attempts from 1 to 120 and delays from 0
to 60 seconds. The shipped 120-attempt, 10-second configuration remains valid.
The CLI and each wait helper reject malformed controls as inconclusive, not
passed or not applicable. These bounds do not attest that every network read
or the entire gate completed within a wall-clock deadline; retain explicit
request timeouts and the workflow job limit as separate controls.
One synchronous gate inspection shares a 500-request, 128 MiB response budget
and 1,200-second elapsed-check boundary across analysis polling, parent/ref
reads and alert polling. Nested calls reuse that context; independent calls
discard it on exit, including exceptions. Reads are capped by remaining bytes
before allocation, retries count even after transient failures, and late
responses cannot yield a successful verdict. A retry sleep must fit the
remaining elapsed budget. These repository safety limits preserve the shipped
configuration but are not GitHub platform limits or a hard interruption of a
blocked HTTP header/body read. Supported-host transport/cancellation behavior
and the job's outer timeout still need separate verification.
The gate explicitly closes owned HTTP error responses before preserving the
transient/non-transient classification. A cleanup failure is inconclusive and
not retryable; do not hide it by replaying requests. Freshness, official-doc,
community-health, toolchain-registry and action-pin clients also close owned
HTTP error responses before wrapping their existing domain errors. Cleanup
failure remains observable as a domain error rather than a successful receipt;
non-HTTP network errors do not imply an owned response stream. Closing synthetic
streams is resource-ownership evidence, not proof that every platform socket
was reclaimed or that a blocked network operation was interrupted.
Redirect refusal must close its owned response even when `Location` is
malformed before a redirect callback could run. API/registry clients reject
all supported 30x events before URL normalization or forwarding. The official
documentation client closes and discards redirect bodies without reading them;
they are not the final page, and urllib uses `Connection: close`. It retains
approved HTTPS hosts, native URL normalization and redirect-loop limits.
Only the final document body is read with the existing size cap. Cleanup or
malformed-redirect failures remain controlled domain errors, not approvals.
Native HTTP responses with a nonzero remaining `Content-Length` after the
bounded read are incomplete and cannot be parsed into trusted documents.
HTTP parser failures, including incomplete chunked bodies, remain controlled
domain errors. Complete fixed-length, chunked and close-delimited responses
remain supported; injected non-HTTP streams have no inferred HTTP length.
This checks native parser completion, not every invalid/ambiguous framing
header, TLS truncation or hard network deadline on every host.
Validate native raw `Content-Length` before reading a body. Values must be
ASCII decimal without signs, and duplicate/comma-list values must identify
the same canonical length; leading zeros do not change it. Bound interpretation
to 100 header fields/members and 8 KiB of length-field text, and compare the
canonical decimal size against the response cap before integer conversion.
These are repository resource limits, not HTTP platform limits. Native chunked
framing retains transfer-coding precedence, while invalid length fields still
fail closed. Complete fixed-length payloads must match the canonical length
even when the native parser did not understand a valid identical comma-list.

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
Bind workflow-installation inspection to the numeric repository ID retained
from approved discovery with `--expected-repository-id`. Its typed
`repository_id` must match at the consumer boundary. An unbound inspection
cannot authorize asset copies. Revalidate active state, visibility, Issues,
Actions permissions, selected policy URL/content and applicable inherited event
policies before returning the verdict; any drift or unavailable final read
is inconclusive. Follow valid organization/enterprise selected-policy URLs,
but a numeric repository-scoped URL cannot refer to a different target ID.
Repeat the exact inspection before each separate asset copy; no atomic API or
filesystem transaction is implied by these checks.
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
Action-pin, schema and batch synchronizers accept only a genuine Boolean
`write` control at library admission, before inventory, upstream lookup or
config reads. `False` is dry-run and reports pending paths without replacing
files; `True` may perform only the authorized scoped replacements. Strings,
numbers, null and collections are invalid, not alternate consent values.
CLI `--write` remains an explicit `store_true` flag. Type validity does not
establish user approval or multi-file atomicity; per-file path/state/readback
and every downstream publication gate remain separate requirements.
Snapshot callback release fields before replacement, drift comparison or batch
cache reuse. The supported tag must be ASCII, match the existing release-tag
policy and stay within 1,024 characters; the commit field must be a full
lowercase GitHub SHA-1 ID. Reject missing/wrong-type/invalid fields before any
file replacement. A malformed callback is inspection failure, not valid drift
or a pending install. These structural checks preserve YAML and canonical pin
shape; they do not prove that the commit belongs to the approved upstream or
that a tag/commit receipt is current, complete and correctly bound.
The native GitHub release resolver must bind each returned reference's `ref`
to the exact requested `refs/tags/<tag>` name. When peeling an annotated tag,
the returned tag object's `sha` must match the queried object ID before its
target is used. Missing, mismatched or wrong-type identities are inconclusive,
not substitute releases. Keep lightweight commits, nested annotated tags and
their existing depth cap supported. These bindings do not prove a stable
repository name/tag across the whole inspection or final write boundary.
Stable action-tag selection must not silently discard inventory entries whose
name is missing, empty or wrong-type, or a matching stable tag whose commit
is invalid. Such an inventory is inconclusive, not evidence that an older
usable candidate is latest. A named non-stable tag, such as a CodeQL bundle
or prerelease, remains outside this selector's applicability and may be
ignored without inspecting its commit. Validate matching stable tags against
the bounded ASCII release policy before converting their numeric components.
The existing pagination cap and complete terminal page remain required;
these structural checks alone do not establish a concurrent snapshot.
Repeated stable tag names may be reused only with the same commit identity.
Conflicting identities within a page or across pages are inconclusive, not
an order-dependent choice of pin. Identical repeated receipts do not by
themselves establish completeness or freshness of the overall inventory.

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

The reusable release engine admits only its documented caller paths. Direct
`workflow_dispatch` must select `release.yml` from the default branch;
`workflow_call` from a branch push must come from `release-please.yml` on the
default branch; a tag call must come from `release-tag.yml` for the pushed `v*`
tag and pass that tag and commit. The build, attestation, and publish jobs all
enforce this gate before any write or OIDC permission is granted. GitHub keeps
the caller's `github` context in reusable workflows, so the gate binds the
event, ref, and workflow file to the approved caller paths
([reusable workflow documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations)).
The optional tag-triggered release asset still requires trusted tag creators
because its workflow definition is taken from the pushed tag commit.

Keep `scheduled compatibility canary`, `do not duplicate supported versions`,
and `scheduled/manual drift canary` as enforceable policy outcomes.

- Documentation: install the documentation contract with markdownlint and
  `validate_scaffold.py`; obtain its runtime from `ci-toolchain.json`.
  Copy the reviewed Markdownlint policy in `scaffold-generation.md` to the
  canonical root path, or preserve and review an existing effective project
  configuration. Default CLI rules alone conflict with the shipped centered
  README/template/import contracts. Run the actual linter on rendered targets;
  a YAML/template pass is not proof that documentation CI will pass.
  The required `docs-contract` job runs with `always()` and first verifies that
  policy preparation succeeded and emitted a valid nonempty runtime selector.
  Failed, cancelled, skipped or indeterminate preparation and missing runtime
  output must fail that check before checkout, dependency installation or lint.
  Do not convert a preparation failure into a skipped required check: GitHub can
  accept skipped jobs as successful status checks.
- PR template: trust only the base SHA on `pull_request_target`; never execute
  PR head code, and require one trusted marker plus all required headings/items.
  Dependabot exemptions require a bot user type. A Release Please exemption
  requires its branch prefix, a head repository matching the base repository,
  and either a bot user type or the base repository owner's account, because the
  configured Release Please token can open PRs as that owner. Both exemptions
  must run Markdown body preflight first; a branch name alone is never an
  exemption.
- PR body sync: trust only base-branch tooling, collect bounded commits and
  changed-file evidence for the exact head SHA, render a concise body summary,
  keep live check status in GitHub's Checks tab, run both body preflights,
  revalidate the PR immediately before `gh pr edit`, and verify the exact
  post-mutation body. Do not use a checked-in narrative body source or execute
  pull-request head code.
  Bind the PR number and numeric base/head repository IDs at all three API
  boundaries, require active base state and typed inventory counts before
  equality, and reject duplicate JSON members. Matching names, hashes or
  Boolean/integer equality must not authorize a body write on unbound evidence.
  Commits that need richer generated prose may provide `Why:`, `Root cause:`,
  `Changes:`, and `Verification:` fields in their commit body; missing fields
  must use an explicit evidence fallback rather than inferred claims.
  Generated summaries should state the review outcome and confirmed cause in
  prose, keep key changes to a few themes, and leave detailed file and check
  listings to GitHub's Commits, Files changed, and Checks views.
  Parse explicit field boundaries and multiline values; ordinary prose is not
  structured evidence. Keep reverts and later fixes in scope. A reviewed
  `PR-summary-base:` commit may consolidate earlier work at the exact base SHA,
  but subsequent commits must remain represented. Reject truncated inventories
  and over-budget summaries before updating GitHub. Attribute verification to
  its source revision, and preserve human-authored risk, acceptance, and rollout
  sections. A template/Markdown pass is not a content-completeness verdict.

Reminder reconciliation treats an upstream checker exit status of `2` as
indeterminate evidence. It must fail before Issue mutation and preserve the
failure for review instead of treating the result as stale state.

- Branch protection: required-check producers must be unique, executable, and
  event-compatible and backed by regular workflow files; Check Run evidence
  must resolve to one Actions workflow run with the producer's exact path,
  controlling SHA, and an eligible event. Closed-PR noops must use a separate
  non-required check name, not success under an admission context. The shipped
  literal closed-state name expression has a stable fallback for open PRs and
  merge groups; other dynamic producer names remain inconclusive. A
  trusted `pull_request_target` producer may satisfy
  protected default-branch pull-request coverage only when its exact default
  branch filter, unchanged workflow blob at the representative PR's base, and
  applicable Actions event policy are verified. Merge new or changed target
  workflows before running this preflight. Job or step `if` and
  `continue-on-error` controls that can skip or mask the gate must fail closed.
  When an effective merge queue applies, require a recent successful `merge_group`
  Check Run from the same workflow blob and GitHub App as the selected PR check.
  Before parsing remote YAML, verify its bounded exact UTF-8 bytes against the
  advertised GitHub SHA-1 blob object ID. Do not normalize line endings or BOM.
  Reusable files require regular entries at the immutable commit's Git tree;
  Contents API responses can dereference symlinks and are not regular-file
  proof. Failed or mismatched bytes must not enter the workflow-signal cache.
  These object checks do not attest an executed workflow or external approval.
  Bound the complete root tree entry count before classifying paths. Every
  entry must be an object with a genuine nonempty path; unknown entries must
  fail closed before any blob read rather than be discarded as non-workflows.
  Preserve exclusion of known paths outside the direct-workflow scope.
  Reusable workflow resolution must preserve a full literal commit pin before
  cache reuse or downstream file inspection. Foreign returned IDs remain
  inconclusive even if their bytes hash correctly. Symbolic refs retain their
  supported lookup behavior; this check is not runtime-source attestation.
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
  This checkout does not pin the workflow definition itself: GitHub [uses the
  workflow version associated with the selected ref](https://docs.github.com/en/actions/concepts/workflows-and-actions/workflows).
  A manual write-enabled workflow is therefore restricted to trusted
  dispatchers and trusted workflow refs. A default-branch checkout binds the
  checked-out scripts and files, but it does not isolate secrets from an
  untrusted workflow definition on the selected ref. If the target repository
  has lower-trust writers, do not install or dispatch a workflow that grants
  write-token or repository-secret access to those refs.
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
  pagination loop. Before projecting issue numbers, the shipped query must
  require explicit Boolean `incomplete_results: false`, non-negative integer
  `total_count`, and exactly `min(total_count, 2)` items with unique positive
  integer issue numbers. Partial, missing, malformed, or inconsistent evidence
  must fail before mutation. The checked `[.items[].number] | join(" ")` result
  is one shell-safe line; a verified zero-match result is the literal `none`.
  Empty stdout must fail before Issue mutation, even when `gh api` returns
  success without a JSON body. Retain the canonical empty-output failure guard
  immediately after lookup, before array initialization. Skip collection only
  for `none`, and never pass that sentinel as an Issue argument.
  A checked line-oriented projection must instead use
  its matching `mapfile` collector. Retain exactly one lookup invocation and no
  extra `gh api` arguments; an unconditional projection must fail validation.
  Standalone reminder installation must enforce this check even without a
  code-scanning gate. The lookup
  must execute in the same unconditional reconciliation path; do not seed or
  reuse issue output/number state before it. A declared freshness workflow that
  fails executable-lifecycle inspection is invalid, not an absent optional
  companion, and must fail installation authorization. The lookup
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
  Freshness may add exactly one literal `--language en` or `--language vi`
  audit argument and use the reviewed human-facing translations in
  `scaffold-generation.md`. Omission preserves English output. This option
  changes neither JSON/status protocol nor shell guards, paths, token bindings,
  or the stable job name. Reject unsupported or duplicate language options.
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

The Scorecard scan requires a branch ref before matching the default branch's
short name. A manual dispatch can target a tag, and a same-name tag is not the
default branch. Missing ref-type evidence must not pass that guard. This
namespace check does not attest an executed workflow definition or replace
the repository's workflow and token authorization policies.

Before making any context required, confirm a real, unique producer, expected
event coverage (including a trusted `pull_request_target` equivalent only for
the verified default branch), no Check Run/commit-status collision, and the
exact GitHub App identity. A skipped job or a workflow filename is not
sufficient evidence.
