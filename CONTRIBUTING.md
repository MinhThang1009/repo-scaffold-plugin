# Contributing to repo-scaffold

Thank you for helping improve `repo-scaffold`. This repository contains a Codex
skill, community-health templates, GitHub Actions templates, a Python CodeQL
preflight helper, and its regression tests.

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

- Python 3.10 or newer
- PyYAML
- pytest
- Ruff
- mypy
- actionlint

ShellCheck and PSScriptAnalyzer are recommended for the shell and PowerShell
snippets.

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
python -m mypy skills/repo-scaffold/scripts/codeql_preflight.py scripts/validate_workflows.py tests/test_codeql_preflight.py
python -m compileall -q skills/repo-scaffold/scripts scripts tests
python scripts/validate_workflows.py
```

When available, also run ShellCheck against extracted Bash blocks and
PSScriptAnalyzer against every PowerShell block.

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

Only release a commit on `main` after CI is green.

1. Set the intended SemVer base in `.codex-plugin/plugin.json`, then use the
   `plugin-creator` cachebuster helper to refresh the single
   `+codex.<cachebuster>` suffix.
2. Move the relevant entries in `CHANGELOG.md` from `Unreleased` to a dated
   version section and validate the plugin and repository.
3. Merge the release commit to `main` and wait for `ci-success`.
4. Create an annotated tag equal to `v` plus the exact manifest version and push
   that tag:

```powershell
$version = (Get-Content -Raw .codex-plugin/plugin.json |
  ConvertFrom-Json).version
git tag -a "v$version" -m "Release v$version"
git push origin "v$version"
```

The tag dispatcher verifies that the tag resolves to the event commit and that
its value matches the manifest. The reusable release engine builds the plugin
archive with read-only contents permission, transfers it to a separate publish
job, creates or updates only a draft Release, and then publishes it. It refuses
to replace assets on an already published Release; publish a new version instead.
