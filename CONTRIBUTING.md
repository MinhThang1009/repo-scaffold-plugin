# Contributing to repo-scaffold

Thank you for helping improve `repo-scaffold`. This repository contains a shared
Agent Skills skill for Codex and Claude Code, community-health templates, GitHub
Actions templates including CodeQL advanced setup, a Python CodeQL preflight
helper, and regression tests.

## Before you start

- Read and follow the [Code of Conduct](CODE_OF_CONDUCT.md).
- For a security vulnerability, follow [SECURITY.md](SECURITY.md) instead of
  opening a public issue.
- Keep changes focused. Do not combine unrelated template, workflow, and parser
  changes in one pull request.
- Verify current GitHub, OpenAI, or Anthropic requirements against their official
  documentation when a change depends on external behavior.

## Report a bug or request a feature

Use the repository's
[issue template chooser](https://github.com/MinhThang1009/repo-scaffold-plugin/issues/new/choose):

- Bug reports should include the plugin version, Codex or Claude Code
  environment, target repository stack, reproduction steps, expected behavior,
  and actual behavior.
- Feature requests should explain the problem, proposed behavior, alternatives,
  and any GitHub, Codex, or Claude Code compatibility constraints.

Do not include tokens, credentials, private repository content, or other
sensitive information.

## Development setup

The plugin has no build step. Development checks require:

- A CPython release declared in [`.github/python-support.json`](.github/python-support.json)
- Coverage.py
- markdown-it-py, for CommonMark-compliant Markdown validation
- PyYAML
- pytest
- Git, including `hash-object` for independent fixture object IDs
- mutmut, on Linux, macOS, or Windows through WSL
- Ruff
- mypy
- Node.js 22 or later with `npx` for markdownlint
- actionlint
- ShellCheck
- `pip-tools`, only when regenerating a lock; record the version used in the
  pull request verification

Native reminder-query integration tests also use an available GitHub CLI and
Bash (Git Bash on Windows). They serve synthetic responses only on loopback,
isolate GitHub CLI configuration, and never require real credentials or write
to GitHub. When either tool is absent, report the corresponding applicability
skip rather than claiming the integration passed.

Workflow-blob fixtures use `git hash-object --stdin --no-filters` as an
independent object-format oracle. Up to 128 fixture IDs are memoized by exact
text; this is deterministic object-format data, not cached GitHub evidence or
authorization. Their workflow text is synthetic, not a
credential or password hash. Portable Windows pipe-backend contract tests
exercise control flow through modeled APIs on every platform; retain the
native Windows tests separately. Passing those contract tests on Linux does
not establish Windows ABI or native pipe behavior.
Each platform's quality run keeps the declared coverage floor. Do not exclude
a native backend solely because that quality runner uses another OS; pair
portable contract seams with the applicable native integration checks.

CI pins for markdownlint and standalone downloaded tools, plus the rolling
documentation bootstrap and minimum bundled-tooling Python runtimes, are
maintained in [`.github/ci-toolchain.json`](.github/ci-toolchain.json). Change
that policy only after reviewing the npm or upstream release and any asset
digest.

PSScriptAnalyzer is recommended when a change adds PowerShell snippets.

Install the fully resolved, hash-verified development toolchain from the
repository root:

```powershell
python -m pip install --require-hashes --requirement requirements-dev.txt
```

When changing a direct pin in `requirements-dev.in`, regenerate
`requirements-dev.txt` with the exact command recorded in the lockfile header
and record `pip-compile --version` in the pull request verification. Inspect the
complete transitive diff and verify the lock against every operating-system and
Python target in
`.github/python-support.json` before committing it.
Dependabot uses this conventional `.in` to `.txt` pair to regenerate the
hash-locked output for ordinary dependency updates. The same review and
verification requirements still apply to its generated diff. Updates shared
with `skills/repo-scaffold/assets/requirements-docs.txt` are grouped across both
locations so the bundled scaffold cannot drift from the repository toolchain.
The unconditional `colorama`, `exceptiongroup`, and `tomli` pins keep the
single lock installable across the supported operating systems and Python
releases even when Dependabot regenerates it on Linux with a newer interpreter.
Keep every platform-conditional package needed by the support matrix explicit
in `requirements-dev.in` because `pip-compile` resolves for its host platform.

Mutation testing uses a separate lock because mutmut requires operating-system
`fork` support. After changing
`requirements-mutation.in`, regenerate `requirements-mutation.txt` with the
same procedure: record `pip-compile --version` and preserve the lockfile
header's hash-mode options. Its unconditional
`toml` pin preserves mutmut's Python 3.10 dependency when the lock is generated
on a newer interpreter. Generate the mutation lock with CPython 3.13 so
LibCST's Python 3.13-only `PyYAML-ft` dependency and its hashes are retained.
Keep that provider explicitly pinned with `python_version == "3.13"` in
`requirements-mutation.in`; it uses the separate `yaml_ft` namespace and does
not replace the development toolchain's `yaml` package. The repository validator
rejects a missing provider or a marker that activates it on other runtimes.
A mutmut update must pass the runner's internal API
integration and behavioral tests; validators derive the reviewed version from
the direct input instead of duplicating it. Regenerating the lock alone remains
insufficient. Trusted scheduled and manual runs
plan every mutant, executes the exact assignment in 128 Linux workers, and merges
only a complete non-overlapping result set. A hash-validated mutation cache may
reuse state only for the same source, tests, mutation workflow and dependency
fingerprint, branch, runtime, and platform. The plan job saves generated state
after recording its input and state hashes. A failed shard run can then reuse
the plan. Completed verdicts are saved after aggregation and the score gate.
Shard artifacts contain only `.meta` result files with their paths relative to
`mutants/`. Generated sources remain in the shared plan artifact, avoiding their
repeated upload by all 128 shards and download by the aggregate job.
The aggregate job rejects a missing
artifact, an unassigned result, or a shard that did not finish before it exports
statistics. On a validated cache hit, preserved killed verdicts are carried into
the merge and only pending assignments execute again. This preserves a full
mutation run without accepting partial cache state or lowering the score gate.

If preparation invalidates any source/cache metadata pair, remove the existing
shard plan as well as stale statistics before reporting plan reuse. Regenerate
the complete plan; verified killed results from other sources remain reusable.
A remaining plan filename must not turn incomplete source state into a cache hit.

The merger admits at most 10,000 directory entries across its metadata walk.
It checks that budget while consuming each iterator, before retaining and
sorting the directory entries. Even unrelated files count toward the budget;
overflow closes the iterator and fails before metadata publication.

Cache metadata uses an owned unique temporary file for replacement. Publication
failures clean that temporary file without replacing the prior destination.
A cleanup failure is reported separately, retaining any earlier I/O failure;
an error after replacement does not imply rollback or an unchanged destination.

## Make a change

1. Create a focused branch from the default branch.
2. Use English for documentation, commit messages, issue and pull-request
   metadata, release notes, and other community-facing content. Follow the
   existing code conventions.
3. Use Conventional Commit messages, for example
   `fix(preflight): bound shell parsing`.
4. Add or update focused regression tests for behavior changes.
5. Update documentation when requirements, templates, or user-visible behavior
   change.

Do not weaken validation, suppress a valid warning, disable a test, or replace a
real check with a hardcoded result.

## Verify the change

The commitlint and code-scanning required jobs still run on `edited` events,
including metadata edits after a PR closes. Only an explicit `closed` PR state
returns a not-applicable result without validating commits or polling analyses.
Open PRs and merge groups retain the full gates, and unknown PR states fail.
Closed-PR noops use separate `commitlint-closed-pr` and
`code-scanning-gate-closed-pr` names, so their success cannot be counted under
the required contexts on a shared head SHA. The normal contexts remain
`commitlint` and `code-scanning-gate` for admission events.
Commitlint checks out the event's exact head SHA rather than an implicit ref.
This lifecycle handling does not change failed historical checks or attest that
a closed PR passed validation.

Run these commands from the repository root:

```powershell
python -m coverage erase
python -m coverage run -m pytest -q
python -m coverage report
python -m ruff format --check skills scripts tests
python -m ruff check skills scripts tests
python -m mypy --explicit-package-bases skills/repo-scaffold/scripts/check_community_health.py skills/repo-scaffold/scripts/audit_freshness.py skills/repo-scaffold/scripts/branch_protection_preflight.py skills/repo-scaffold/scripts/advanced_codeql_preflight.py skills/repo-scaffold/scripts/codeql_preflight.py skills/repo-scaffold/scripts/dependency_review_preflight.py skills/repo-scaffold/scripts/scorecard_preflight.py skills/repo-scaffold/scripts/ci_toolchain.py skills/repo-scaffold/scripts/pr_template_preflight.py skills/repo-scaffold/scripts/release_preflight.py skills/repo-scaffold/scripts/merge_settings_preflight.py skills/repo-scaffold/scripts/repository_settings_preflight.py skills/repo-scaffold/scripts/security_features_preflight.py skills/repo-scaffold/scripts/workflow_installation_preflight.py skills/repo-scaffold/scripts/sync_action_pins.py skills/repo-scaffold/scripts/validate_scaffold.py skills/repo-scaffold/scripts/markdown_body_preflight.py skills/repo-scaffold/scripts/render_pr_body_evidence.py scripts/audit_freshness.py scripts/audit_official_docs.py scripts/check_code_scanning_alerts.py scripts/merge_mutation_shards.py scripts/pr_template_preflight.py scripts/markdown_body_preflight.py scripts/prepare_mutation_cache.py scripts/python_support.py scripts/run_mutation_testing.py scripts/sync_action_pins.py scripts/sync_versioned_inputs.py scripts/update_pr_body.py scripts/render_pr_body_evidence.py scripts/validate_mutation_results.py scripts/validate_repository.py scripts/validate_workflows.py tests
python -m compileall -q skills/repo-scaffold/scripts scripts tests
python skills/repo-scaffold/scripts/ci_toolchain.py run-markdownlint
python scripts/validate_workflows.py
python scripts/validate_repository.py
```

The coverage command enforces the repository's 100% branch-coverage floor from
`.coveragerc`.
Run boundary checks on the declared minimum/latest runtimes with their actual
standard libraries. Approved documentation HTTP 308 redirects must preserve
GET/HEAD methods even when the minimum runtime's redirect handler lacks 308;
HTTPS/host, unsafe-method, body-ownership and loop limits still apply.
Legacy body publication reports cleanup failures without hiding a prior write
failure or claiming rollback. It registers cleanup only after allocation.

The shared GitHub preflight client admits stdout/stderr bytes before writing
temporary spools. Per-stream and inspection-total limits apply during capture,
not only after the CLI exits. Require EOF on both pipes and a completed CLI
before decoding; timeout, excess output and incomplete spool writes are errors,
not partial evidence. Native Windows uses pipe readiness rather than socket-only
`select`; POSIX uses selectable pipe descriptors. These checks do not certify
SDK-internal allocation, descendant-process termination, HTTP redirect origin
or underlying request counts. Preserve those independent audit requirements.

Gh-backed preflight metrics distinguish client attempts from wire requests.
`github_client_requests` is the counter charged at client admission.
`github_api_requests` remains a compatibility alias, explicitly tagged with
`github_api_requests_unit: client-request-attempts`. The transport count is
`github_http_requests: null` with state `not-measured`, never zero or passed
evidence. Neither redirects nor other SDK-internal requests can be counted
from the returned CLI body. These diagnostic metrics do not authorize writes
or prove an underlying HTTP-request quota or initial-response provenance.

Mutation testing runs daily and on manual dispatch because a complete run is
substantially more expensive than the required pull-request checks. The workflow
plans every mutant, runs 128 exact Linux shards, and merges only a complete,
non-overlapping assignment before it applies the gate. Mutmut requires
operating-system `fork` support, so run it on Linux or macOS, or in WSL on
Windows:

If the lock resolves LibCST to a source distribution because no compatible wheel
is available, provision a compatible Rust toolchain and platform native build tools
in an isolated build environment first. Derive compiler requirements from the
hash-verified pinned source, including its Cargo manifest; do not assume Linux
wheel availability also covers macOS Intel. The pip hash lock is not proof of a
successful source build and does not provision Rust or the system linker/SDK.
Record the actual toolchain and build/runtime result on the intended platform,
or report that check as deferred. Do not lower the score gate, change the pin,
or label metadata-only checks as runtime passes to bypass an unavailable build.

```bash
python -m pip install --require-hashes --requirement requirements-mutation.txt
python scripts/run_mutation_testing.py --max-children 4
mutmut export-cicd-stats
mutmut results --all true > mutants/mutation-results.txt
python scripts/validate_mutation_results.py
```

The repository already enforces 100% branch coverage. Mutmut therefore uses its
default call-based selection instead of the optional Coverage.py line prepass,
which keeps in-process trampoline association reliable without narrowing the
mutation scope.

The result validator fails on skipped, untested, suspicious, interrupted, or
crashed mutants and enforces a 100.00% mutation-score floor across killed,
timed-out, and surviving mutants. A timeout counts as detected because the
mutant made the bounded test process fail to terminate; it remains visible in
the summary. Generated mutant source, per-file metadata, test-association data,
and result summaries are retained for diagnosis. Do not suppress a valid mutant
merely to make the score pass; classify equivalent mutants during review and
raise the floor only from a completed, repeatable run.

`validate_workflows.py` runs actionlint, then runs the reviewed ShellCheck binary
separately against extracted Bash blocks. The Markdown checks cover all
project-owned `.md` files, README layout, unresolved scaffold markers, relative
links, and Markdown issue/PR templates. When a change adds PowerShell blocks,
also run PSScriptAnalyzer against each block.

## Open a pull request

Before creating or editing a pull request, run
`python scripts/pr_template_preflight.py --title "<title>"`. For a focused
security, deployment, or dependency-update review whose title has no mandatory
mapping, add `--template security`, `--template deployment`, or
`--template dependency-update`.
After preparing the UTF-8 body file, rerun the preflight with `--body-file <path>`;
add `--require-structure` so it also verifies the selected template's required
headings and checklist items before the GitHub mutation. Draft items may remain
unchecked; readiness checks are enforced separately by the template gate.

When a commit changes behavior or policy, include structured summary fields in
the commit body so the generated PR can preserve the review context:

```text
Why: the user or maintainer outcome
Root cause: the confirmed technical cause
Changes: the main implementation or policy changes
Verification: the focused tests or checks run
```

Fields require a colon and may contain paragraphs or bullet lists on following
lines. Record every material behavior change, including later fixes and reverts.
Commit subjects are not evidence of a root cause, and validation reported in a
commit is evidence for that revision rather than a guarantee for later commits.

For a long PR, a commit may provide a reviewed summary of the complete diff by
adding `PR-summary-base: <full-current-base-SHA>` before all four fields. That
summary covers the PR through its containing commit. The renderer includes
every later change, rejects a changed base, and fails if the description would
need to drop a topic to fit the paragraph or five-bullet budget. Review the
complete diff and update the cumulative summary when that happens. The summary
must cover the PR's primary behavior and safety-boundary changes, not only the
PR-body tooling or documentation that makes them reviewable.

This repository's `pr-body-sync` workflow renders the complete pull-request
body from the exact pull-request API evidence whenever a pull request is
opened, reopened, or receives a new commit. It derives commit subjects and
structured fields, and the complete changed-file inventory, bound to the
immutable base/head. Commit-derived purpose, cause, key changes, and verification
notes are author-reported and tagged with the full source SHA; the current
Checks-tab reminder is generated separately. The file inventory verifies
pagination and the PR file count; it does not establish
change themes or verify narrative claims against patches. Live check status
remains in GitHub's Checks tab. The workflow does not read a hard-coded PR-body
narrative. The selected trusted PR template supplies the headings and checklist
contract, while generated sections are replaced from current evidence. The
renderer matches exact heading aliases outside code, comments, and raw HTML,
preserving custom reviewer sections and combined verification/monitoring plans.
Its CLI checks the rendered structure against the selected trusted template
before replacing output, so partial bodies fail before the workflow can edit
GitHub. Use `--repository-root` when invoking it outside the target repository.
It preserves native Dependabot and same-repository Release Please bodies under
the template gate's authenticated author rules, after validating complete bound
PR evidence; bot names or release-like branches alone cannot claim an exemption.
The workflow bounds API responses and combined evidence, runs the Markdown and
PR-template preflights before editing, rechecks the head, base, title,
repository, and current body immediately before the mutation, then verifies the
complete body after GitHub accepts it. Missing, malformed, stale, incomplete,
or over-limit evidence fails closed. The GitHub PR commit endpoint supports at
most 250 commits and the files endpoint supports at most 3,000 files. Template
preflight validates structure and prose layout; review the resulting
description against the full diff to establish
content completeness. Human-authored acceptance criteria, risk notes, and
rollout or rollback plans remain part of that review.
GitHub's pull-request update endpoint has no conditional body-write parameter.
A concurrent human edit after the workflow's final read but before its update
can be overwritten; the workflow minimizes that interval and verifies the
accepted body afterward.

Use the default PR template for ordinary changes. Choose a specialized template
only when its review workflow applies:

- `feature.md` for a new or materially expanded capability;
- `bugfix.md` for a confirmed defect and its regression coverage;
- `documentation.md` for documentation-only or documentation-led changes;
- `security.md` for a safely disclosable security change. Follow `SECURITY.md`
  instead of opening a public PR for an undisclosed vulnerability.
- `deployment.md` for a deployment, rollout, or rollback plan; or
- `dependency-update.md` for a direct or transitive dependency update; or
- the default template for ordinary changes.

For a Conventional Commit PR title beginning with `feat`, `fix`, or `docs`, use
`feature.md`, `bugfix.md`, or `documentation.md`, respectively. The
`pr-template` gate enforces these mappings. Other supported title types use the
default template unless the change needs a focused security, deployment, or
dependency-update review.

Preserve the selected `repo-scaffold:pr-template` marker, all required
headings, and the required checklist. Add only applicable items from `If
applicable`, or omit that section entirely. The required `pr-template` gate
rejects a body without exactly one trusted marker or with an incomplete
required checklist. A draft PR may leave required items unchecked; before
marking it ready for review, tick each required item only after it is complete.

Include:

- the problem and why the change is needed;
- the files and behavior changed;
- the official source used for externally defined behavior;
- the exact verification commands and their results;
- remaining limitations or checks that could not be run.

Leave changes unstaged and uncommitted unless the repository maintainer
explicitly requests Git operations.

## Cut a release

Releases are automated from `main` through Release Please.

1. Use Conventional Commit titles for changes merged into `main` and wait for
   all required checks.
2. Review the Release Please pull request. It updates `CHANGELOG.md`,
   `version.txt`, `.release-please-manifest.json`, and
   `.codex-plugin/plugin.json` and `.claude-plugin/plugin.json` to the proposed
   SemVer.
3. Merge the release pull request after its checks pass. Release Please creates
   the tag and draft GitHub Release, then invokes the reusable release engine.
4. Verify that the release asset is attached, its provenance attestation passes,
   and the release is published:

   ```bash
   gh attestation verify repo-scaffold-plugin-vX.Y.Z.zip \
     --repo MinhThang1009/repo-scaffold-plugin \
     --signer-workflow MinhThang1009/repo-scaffold-plugin/.github/workflows/release.yml
   ```

The workflow requires a fine-grained PAT stored as `RELEASE_PLEASE_TOKEN`,
scoped only to this repository with **Contents: Read and write** and
**Pull requests: Read and write**. Add **Issues: Read and write** because Release
Please manages release pull request labels. Never place the token in a file,
commit, command output, issue, or chat message.

The reusable release engine builds the plugin archive with read-only contents
permission, transfers it to a separate attestation job that alone receives the
OIDC and attestation permissions, then allows the contents-write publish job to
publish only the matching draft Release. Neither privileged job checks out or
executes project code. The engine refuses to replace assets on an already
published Release; publish a new version instead.
