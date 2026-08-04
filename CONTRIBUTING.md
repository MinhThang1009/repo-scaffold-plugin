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
- PyYAML
- pytest
- Ruff
- mypy
- Node.js with `npx` for markdownlint
- actionlint
- ShellCheck

CI pins for standalone downloaded tools and the rolling documentation bootstrap
runtime are maintained in [`.github/ci-toolchain.json`](.github/ci-toolchain.json).
Change that policy only after reviewing the upstream release and asset digest.

PSScriptAnalyzer is recommended when a change adds PowerShell snippets.

Install the pinned development toolchain from the repository root:

```powershell
python -m pip install --requirement requirements-dev.txt
```

## Make a change

1. Create a focused branch from the default branch.
2. Follow the existing English documentation and code conventions.
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
python -m pytest -q
python -m ruff format --check skills scripts tests
python -m ruff check skills scripts tests
python -m mypy skills/repo-scaffold/scripts/ci_toolchain.py skills/repo-scaffold/scripts/codeql_preflight.py skills/repo-scaffold/scripts/validate_scaffold.py scripts/python_support.py scripts/validate_repository.py scripts/validate_workflows.py tests
python -m compileall -q skills/repo-scaffold/scripts scripts tests
npx --yes markdownlint-cli2@0.23.2 "**/*.md" "#.git/**" "#build/**" "#dist/**" "#node_modules/**"
python scripts/validate_workflows.py
python scripts/validate_repository.py
```

`validate_workflows.py` runs actionlint with ShellCheck enabled. The Markdown
checks cover all project-owned `.md` files, README layout, unresolved scaffold
markers, relative links, and Markdown issue/PR templates. When a change adds
PowerShell blocks, also run PSScriptAnalyzer against each block.

## Open a pull request

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
