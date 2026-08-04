<div align="center">

# repo-scaffold

A Codex plugin that scaffolds a new repository to production GitHub.com standard.

[![CI](https://github.com/MinhThang1009/repo-scaffold-plugin/actions/workflows/ci.yml/badge.svg)](https://github.com/MinhThang1009/repo-scaffold-plugin/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

## Table of Contents

- [1. What it does](#1-what-it-does)
- [2. Requirements](#2-requirements)
- [3. Why it's useful](#3-why-its-useful)
- [4. How to use](#4-how-to-use)
- [5. Support](#5-support)
- [6. Install](#6-install)
- [7. Update](#7-update)
- [8. Uninstall](#8-uninstall)
- [9. Structure](#9-structure)
- [10. Development validation](#10-development-validation)
- [11. Releases](#11-releases)
- [12. Maintainers and contributing](#12-maintainers-and-contributing)
- [13. License](#13-license)

## 1. What it does

Provides the `repo-scaffold` skill, which Codex activates when you ask to set up a new repository's standard files. It:

- Generates community-health and repository-maintenance files tailored to the project and enabled repository features: README, CONTRIBUTING, SECURITY, SUPPORT, CODE_OF_CONDUCT, LICENSE, CODEOWNERS, issue/PR templates, Dependabot, CHANGELOG, `.editorconfig`, `.gitignore`, and `.gitattributes`.
- For a verified GitHub.com remote, adds deterministic documentation checks, scheduled link checks, a CI workflow tailored to the detected stack, and a release workflow, plus optional ones (release-please, repository-managed CodeQL advanced setup, dependency review, Dependabot and label-gated auto-merge, commitlint, stale, labeler).
- Configures the verified GitHub.com repository: repository description, classic branch protection, and labels. Existing repository or organization rulesets are inspected as effective policy but are not modified.

Content follows GitHub's community-standards format and is pulled from canonical sources where possible (LICENSE, `.gitignore`, and Code of Conduct via the GitHub API), with project-specific content generated from the repository itself. External GitHub Actions are pinned to immutable commit SHAs and kept current by Dependabot; shipped workflows do not delegate execution to a mutable container tag.

## 2. Requirements

- [`gh`](https://cli.github.com/) (GitHub CLI), authenticated to GitHub.com (`gh auth status --active --hostname github.com`) — used for every GitHub API call and configuration step.
- `git`.
- `actionlint` and ShellCheck are required for local workflow validation.
- Python 3.10 or newer with PyYAML is required for deterministic scaffold validation and the fail-closed CodeQL default-setup preflight. The preflight bounds workflow inputs, GitHub CLI output, API calls, and total runtime. It also requires separate confirmation that no external or indirect process uploads CodeQL results; without either prerequisite, the plugin skips that mutation and reports the verification gap.
- Node.js with `npx` is required only to reproduce the pinned markdownlint check locally.
- Remote automation supports GitHub.com only. GitHub Enterprise Server and GHE.com repositories receive host-independent local community files, but bundled workflows, GitHub.com badges, and remote configuration are skipped.
- Without a remote, the plugin can generate host-independent local files. It defers workflows, badges, and GitHub configuration until a GitHub.com remote exists; it never creates that remote without confirmation.

## 3. Why it's useful

Every new repository needs the same production boilerplate in the correct GitHub format. This automates it intelligently: it reads the project to fill in real content instead of copying static templates.

## 4. How to use

Install the plugin (see below), then, in any repository, ask Codex:

- "scaffold this repo"
- "set up the repo to production standard"
- "dựng repo chuẩn github" / "tạo file chuẩn cho repo"

The skill activates automatically and walks through: survey → decisions → file generation → workflows → GitHub configuration → handoff → verification. It never overwrites existing files without asking, leaves changes unstaged and uncommitted unless you explicitly request Git operations, and confirms outward-facing actions first.

## 5. Support

See [SUPPORT.md](SUPPORT.md) for usage help and the information to include in a report. For a local installation, contact the person or workspace that shared the plugin.

Report vulnerabilities according to [SECURITY.md](SECURITY.md), never through a public issue. Community participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).

## 6. Install

Install it from your local Codex marketplace, then start a new Codex thread in the target repository:

```powershell
codex plugin add repo-scaffold@personal
```

The default personal marketplace is `~/.agents/plugins/marketplace.json` and points to `~/plugins/repo-scaffold`.

## 7. Update

After the marketplace source and plugin version have been updated, refresh the installed snapshot and start a new Codex thread:

```powershell
codex plugin remove repo-scaffold@personal
codex plugin add repo-scaffold@personal
```

The current thread keeps the skill version that it loaded when the thread started.

## 8. Uninstall

Remove the installed plugin, then start a new Codex thread:

```powershell
codex plugin remove repo-scaffold@personal
```

## 9. Structure

```text
repo-scaffold/
├── .release-please-manifest.json
├── .markdownlint-cli2.jsonc
├── .codex-plugin/
│   └── plugin.json
├── .github/
│   ├── CODEOWNERS
│   ├── dependabot.yml
│   ├── labeler.yml
│   ├── release.yml
│   └── workflows/
│       ├── ci.yml
│       ├── codeql.yml
│       ├── commitlint.yml
│       ├── dependency-review.yml
│       ├── labeler.yml
│       ├── links.yml
│       ├── release-please.yml
│       ├── release.yml
│       └── stale.yml
├── .editorconfig
├── .gitattributes
├── CHANGELOG.md
├── README.md
├── LICENSE
├── release-please-config.json
├── requirements-dev.txt
├── version.txt
├── scripts/
│   ├── validate_repository.py
│   └── validate_workflows.py
└── skills/
    └── repo-scaffold/
        ├── SKILL.md
        ├── scripts/
        │   ├── codeql_preflight.py  # fail-closed CodeQL/reusable-workflow inspection
        │   └── validate_scaffold.py # rendered Markdown and template contract
        ├── references/
        │   ├── readme.md          # README structure guidance
        │   └── github-setup.md    # exact gh configuration commands
        └── assets/                # community-health files + config files (labeler, release configs)
            └── workflows/         # ci, docs, links, release engine/dispatcher, release-please, CodeQL,
                                   # dependency-review, auto-merge, commitlint, stale, labeler
```

SKILL.md → Resources lists every generated file.

## 10. Development validation

The plugin has no compilation step. Its Python validation tests require Python 3.10 or newer, PyYAML, and pytest. Run the repository checks from its root:

```powershell
python -m pip install --requirement requirements-dev.txt
python -m pytest -q
python -m ruff format --check skills scripts tests
python -m ruff check skills scripts tests
python -m mypy skills/repo-scaffold/scripts/codeql_preflight.py skills/repo-scaffold/scripts/validate_scaffold.py scripts/validate_repository.py scripts/validate_workflows.py tests
python -m compileall -q skills/repo-scaffold/scripts scripts tests
npx --yes markdownlint-cli2@0.23.2 "**/*.md" "#.git/**" "#build/**" "#dist/**" "#node_modules/**"
python scripts/validate_workflows.py
python scripts/validate_repository.py
```

Workflow validation runs actionlint with ShellCheck enabled against both installed
and templated workflows. Markdownlint covers every project-owned Markdown file.
Repository validation checks the centered and numbered README contract, unresolved
scaffold markers, Markdown issue and pull-request templates, relative links,
JSON/YAML uniqueness and syntax, plugin metadata, issue forms, Dependabot
configuration, and the exact release archive shape. The pinned action tags and
release-please schema are external facts, so verify them against their upstream
repositories during release audits.

GitHub Actions runs the test suite on Ubuntu and Windows with the minimum and
latest supported Python feature releases. A separate quality job runs formatting,
lint, type, compile, workflow, metadata, link, and release-archive checks.
Dependabot checks the pinned Python development tools and GitHub Actions weekly.

## 11. Releases

This repository uses Release Please with Conventional Commits. Each push to
`main` updates a release pull request. Merging that pull request updates
`CHANGELOG.md`, `version.txt`, and `.codex-plugin/plugin.json`, creates the tag
and draft GitHub Release, then invokes the reusable release engine.

The engine verifies the tag target, builds
`repo-scaffold-plugin-<filesystem-safe-tag>.zip` from the immutable commit,
attaches it to the draft, and publishes only after the asset is present. The
archive contains `.codex-plugin/`, `skills/`, `README.md`, and `LICENSE` under a
`repo-scaffold/` directory. The workflow requires a fine-grained PAT stored as
`RELEASE_PLEASE_TOKEN`; see [CONTRIBUTING.md](CONTRIBUTING.md) for the release
process and token scope.

## 12. Maintainers and contributing

This plugin is maintained by Minh Thang. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow, required checks, and pull-request expectations. Project roles and decisions are described in [GOVERNANCE.md](GOVERNANCE.md).

When proposing a change, include the source you verified, the files affected, and the command or manual check used to validate the result.

## 13. License

MIT. See [LICENSE](LICENSE).
