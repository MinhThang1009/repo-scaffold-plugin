# Contributing to repo-scaffold

Thank you for helping improve `repo-scaffold`. This repository contains a Codex
skill, community-health templates, GitHub Actions templates including CodeQL
advanced setup, a Python CodeQL preflight helper, and regression tests.

## Before you start

- Read and follow the [Code of Conduct](CODE_OF_CONDUCT.md).
- For a security vulnerability, follow [SECURITY.md](SECURITY.md) instead of
  opening a public issue.
- Keep changes focused. Do not combine unrelated template, workflow, and parser
  changes in one pull request.
- Verify current GitHub or OpenAI requirements against their official
  documentation when a change depends on external behavior.

## Report a bug or request a feature

Use the repository's
[issue template chooser](https://github.com/MinhThang1009/repo-scaffold-plugin/issues/new/choose):

- Bug reports should include the plugin version, Codex environment, target
  repository stack, reproduction steps, expected behavior, and actual behavior.
- Feature requests should explain the problem, proposed behavior, alternatives,
  and any GitHub or Codex compatibility constraints.

Do not include tokens, credentials, private repository content, or other
sensitive information.

## Development setup

The plugin has no build step. Development checks require:

- A CPython release declared in [`.github/python-support.json`](.github/python-support.json)
- Coverage.py
- markdown-it-py, for CommonMark-compliant Markdown validation
- PyYAML
- pytest
- mutmut, on Linux or Windows through WSL
- Ruff
- mypy
- Node.js with `npx` for markdownlint
- actionlint
- ShellCheck
- `pip-tools`, only when regenerating a lock; record the version used in the
  pull request verification

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

Mutation testing uses a separate Linux/WSL-only lock. After changing
`requirements-mutation.in`, regenerate `requirements-mutation.txt` with the
same procedure: record `pip-compile --version` and preserve the lockfile
header's hash-mode options. Its unconditional
`toml` pin preserves mutmut's Python 3.10 dependency when the lock is generated
on a newer interpreter. A mutmut update must pass the runner's internal API
integration and behavioral tests; validators derive the reviewed version from
the direct input instead of duplicating it. Regenerating the lock alone remains
insufficient. Trusted scheduled and manual runs
record complete or interrupted progress under an integrity manifest and reuse
only previously killed mutants when production, support, and the complete test
suite are unchanged. Survivor, timeout, and pending results are always reset.
Any test change forces a full run because a new module can introduce fixtures or
import-time side effects that alter existing tests. A completed run on the same
repository and commit can be resumed explicitly with `resume_run_id`; the
workflow verifies its source and downloads only its unexpired
`mutation-results` artifact. Use the `clean` workflow-dispatch input without a
resume run before claiming a final mutation score so every mutant is
independently rerun without cached results.

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

Run these commands from the repository root:

```powershell
python -m coverage erase
python -m coverage run -m pytest -q
python -m coverage report
python -m ruff format --check skills scripts tests
python -m ruff check skills scripts tests
python -m mypy --explicit-package-bases skills/repo-scaffold/scripts/check_community_health.py skills/repo-scaffold/scripts/audit_freshness.py skills/repo-scaffold/scripts/codeql_preflight.py skills/repo-scaffold/scripts/ci_toolchain.py skills/repo-scaffold/scripts/sync_action_pins.py skills/repo-scaffold/scripts/validate_scaffold.py scripts/audit_freshness.py scripts/audit_official_docs.py scripts/check_code_scanning_alerts.py scripts/prepare_mutation_cache.py scripts/python_support.py scripts/run_mutation_testing.py scripts/sync_action_pins.py scripts/sync_versioned_inputs.py scripts/validate_mutation_results.py scripts/validate_repository.py scripts/validate_workflows.py tests
python -m compileall -q skills/repo-scaffold/scripts scripts tests
python skills/repo-scaffold/scripts/ci_toolchain.py run-markdownlint
python scripts/validate_workflows.py
python scripts/validate_repository.py
```

The coverage command enforces the repository's 100% branch-coverage floor from
`.coveragerc`.

Mutation testing runs monthly and on manual dispatch because a complete run is
substantially more expensive than the required pull-request checks. Mutmut
requires operating-system `fork` support, so run it on Linux or in WSL on
Windows:

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

Use the default PR template for ordinary changes. Choose a specialized template
only when its review workflow applies:

- `feature.md` for a new or materially expanded capability;
- `bugfix.md` for a confirmed defect and its regression coverage;
- `documentation.md` for documentation-only or documentation-led changes; or
- `security.md` for a safely disclosable security change. Follow `SECURITY.md`
  instead of opening a public PR for an undisclosed vulnerability.

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
   `.codex-plugin/plugin.json` to the proposed SemVer.
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
