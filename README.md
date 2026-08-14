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
- For a verified GitHub.com remote, adds deterministic documentation checks, pull-request and scheduled link checks, a CI workflow tailored to the detected stack, and a release workflow with provenance attestations when the repository is eligible, plus optional ones (release-please, repository-managed CodeQL advanced setup, dependency review, Dependabot and label-gated auto-merge, commitlint, stale, labeler).
- Configures the verified GitHub.com repository: repository description, classic branch protection, and labels. Existing repository or organization rulesets are inspected as effective policy but are not modified.
- Produces either English or Vietnamese project-facing content. An explicit request wins, followed by active project instructions and the established documentation convention; English is the default when no preference exists.

Content follows GitHub's community-standards format and is pulled from canonical sources where possible (LICENSE, `.gitignore`, and Code of Conduct via the GitHub API), with project-specific content generated from the repository itself. External GitHub Actions are pinned to immutable commit SHAs and kept current by Dependabot; shipped workflows do not delegate execution to a mutable container tag.

## 2. Requirements

- [`gh`](https://cli.github.com/) (GitHub CLI), authenticated to GitHub.com (`gh auth status --active --hostname github.com`) — used for every GitHub API call and configuration step.
- `git`.
- `actionlint` and ShellCheck are required for local workflow validation. CI obtains their reviewed versions, release metadata, archive layout, and asset digests from the centralized [CI toolchain policy](.github/ci-toolchain.json).
- Use a CPython feature release declared in the centralized [Python support policy](.github/python-support.json), with the hash-locked development dependencies, for deterministic tests, branch coverage, scaffold validation, and the fail-closed CodeQL default-setup preflight. The preflight bounds workflow inputs, GitHub CLI output, API calls, and total runtime. It also requires separate confirmation that no external or indirect process uploads CodeQL results; without either prerequisite, the plugin skips that mutation and reports the verification gap.
- Node.js with `npx` is required only to reproduce the markdownlint package pinned by the [CI toolchain policy](.github/ci-toolchain.json).
- Remote automation supports GitHub.com only. GitHub Enterprise Server and GHE.com repositories receive host-independent local community files, but bundled workflows, GitHub.com badges, and remote configuration are skipped.
- Without a remote, the plugin can generate host-independent local files. It defers workflows, badges, and GitHub configuration until a GitHub.com remote exists; it never creates that remote without confirmation.

## 3. Why it's useful

Every new repository needs the same production boilerplate in the correct GitHub format. This automates it intelligently: it reads the project to fill in real content instead of copying static templates.

## 4. How to use

Install the plugin (see below), then, in any repository, ask Codex:

- "scaffold this repo"
- "scaffold this repo in English"
- "set up the repo to production standard"
- "dựng repo chuẩn GitHub bằng tiếng Việt"

The skill activates automatically and walks through: survey → decisions → file generation → workflows → GitHub configuration → handoff → verification. It resolves one project language (`en` or `vi`) before generation and applies it consistently to documentation, templates, and release metadata. It never overwrites existing files without asking, leaves changes unstaged and uncommitted unless you explicitly request Git operations, and confirms outward-facing actions first.

## 5. Support

See [SUPPORT.md](SUPPORT.md) for usage help and the information to include in a report. For a local installation, contact the person or workspace that shared the plugin.

Report vulnerabilities according to [SECURITY.md](SECURITY.md), never through a public issue. Community participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md). See the [Privacy Policy](PRIVACY.md) and [Terms of Use](TERMS.md) before using or distributing the plugin.

## 6. Install

Public plugins are installed from the universal Plugin Directory shared by ChatGPT and Codex. This repository is not yet claiming a public-directory listing.

For local development, first add this checkout to a personal marketplace as described in the [official plugin packaging guide](https://developers.openai.com/plugins/build/plugins#build-your-own-curated-plugin-list). If that marketplace is named `personal`, install the plugin and then start a new Codex thread in the target repository:

```powershell
codex plugin add repo-scaffold@personal
```

`personal` is a local marketplace name, not a global default. A personal marketplace catalog lives at `~/.agents/plugins/marketplace.json`; its plugin source path must point to this checkout.

## 7. Update

After the local marketplace source and plugin version have been updated, reinstall the plugin and start a new Codex thread:

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

Key implementation and release files are shown below. Community policy and
template files are omitted for brevity.

```text
repo-scaffold/
├── .coveragerc
├── .release-please-manifest.json
├── .markdownlint-cli2.jsonc
├── .codex-plugin/
│   └── plugin.json
├── .github/
│   ├── CODEOWNERS
│   ├── ci-toolchain.json
│   ├── dependabot.yml
│   ├── labeler.yml
│   ├── python-support.json
│   ├── release.yml
│   └── workflows/
│       ├── ci.yml
│       ├── codeql.yml
│       ├── commitlint.yml
│       ├── dependency-review.yml
│       ├── labeler.yml
│       ├── links.yml
│       ├── mutation-testing.yml
│       ├── release-please.yml
│       ├── release.yml
│       ├── scorecard.yml
│       └── stale.yml
├── .editorconfig
├── .gitattributes
├── CHANGELOG.md
├── PLUGIN_SUBMISSION.md
├── PRIVACY.md
├── README.md
├── LICENSE
├── release-please-config.json
├── requirements-dev.lock
├── requirements-dev.txt
├── requirements-mutation.lock
├── requirements-mutation.txt
├── TERMS.md
├── version.txt
├── scripts/
│   ├── prepare_mutation_cache.py
│   ├── python_support.py
│   ├── run_mutation_testing.py
│   ├── validate_mutation_results.py
│   ├── validate_repository.py
│   └── validate_workflows.py
└── skills/
    └── repo-scaffold/
        ├── SKILL.md
        ├── scripts/
        │   ├── ci_toolchain.py      # centralized CI bootstrap/tool pin policy
        │   ├── codeql_preflight.py  # fail-closed CodeQL/reusable-workflow inspection
        │   └── validate_scaffold.py # rendered Markdown and template contract
        ├── references/
        │   ├── readme.md          # README structure guidance
        │   └── github-setup.md    # exact gh configuration commands
        └── assets/                # community-health files + config files (labeler, release configs)
            └── workflows/         # ci, docs, links, release engine/dispatcher, release-please, CodeQL,
                                   # Scorecard, dependency-review, auto-merge, commitlint, stale, labeler
```

SKILL.md → Resources lists every generated file.

## 10. Development validation

The plugin has no compilation step. Its validation tests require a CPython release from the [Python support policy](.github/python-support.json) and the hash-locked development toolchain, including Coverage.py, markdown-it-py for CommonMark parsing, PyYAML, and pytest. Run the repository checks from its root:

```powershell
python -m pip install --require-hashes --requirement requirements-dev.lock
python -m coverage erase
python -m coverage run -m pytest -q
python -m coverage report
python -m ruff format --check skills scripts tests
python -m ruff check skills scripts tests
python -m mypy skills/repo-scaffold/scripts/codeql_preflight.py skills/repo-scaffold/scripts/ci_toolchain.py skills/repo-scaffold/scripts/validate_scaffold.py scripts/prepare_mutation_cache.py scripts/python_support.py scripts/run_mutation_testing.py scripts/validate_mutation_results.py scripts/validate_repository.py scripts/validate_workflows.py tests
python -m compileall -q skills/repo-scaffold/scripts scripts tests
python skills/repo-scaffold/scripts/ci_toolchain.py run-markdownlint
python scripts/validate_workflows.py
python scripts/validate_repository.py
```

Workflow validation runs actionlint and then runs the reviewed ShellCheck binary
separately against extracted Bash blocks from installed and templated workflows.
Markdownlint covers every project-owned Markdown file.
Coverage measures both first-party script trees with branch coverage and enforces
the 100% floor in `.coveragerc` from the CI quality job.
Repository validation checks the centered and numbered README contract, unresolved
scaffold markers, Markdown issue and pull-request templates, relative links,
JSON/YAML uniqueness and syntax, plugin metadata, issue forms, Dependabot
configuration, release-attestation isolation and permission flow, and the exact
release archive shape, including every runtime script referenced by the skill.
The pinned action tags and
release-please schema are external facts, so verify them against their upstream
repositories during release audits.

The [Python support policy](.github/python-support.json) is the single source of
truth for CI. GitHub Actions tests every declared feature release on Ubuntu and
the minimum/latest boundaries on Windows. The quality job consumes the policy's
latest value. A non-required weekly `3.x` canary tests the latest stable Python,
then fails on undeclared-version drift so support changes require a reviewed
policy update. Repository validation rejects policy, workflow, scaffold, and
documentation drift. The quality job also runs formatting, lint, type, compile,
workflow, metadata, link, and release-archive checks.
The [CI toolchain policy](.github/ci-toolchain.json) separately centralizes the
rolling documentation bootstrap runtime, minimum Python for bundled tooling,
the markdownlint npm pin, and reviewed standalone-tool release metadata and
digests. Workflows and setup guidance consume that policy instead of embedding
those values, and a non-required scheduled/manual canary reports npm, upstream
release, or digest drift for review.
Dependabot checks the pinned Python development tools and GitHub Actions weekly.
`requirements-dev.txt` records reviewed direct pins; `requirements-dev.lock`
resolves every transitive dependency and records PyPI SHA-256 hashes used by CI.
Mutation testing extends that toolchain through the separate, hash-verified
`requirements-mutation.lock`. Its monthly and manually dispatched workflow runs
mutmut on Linux, rejects incomplete runs, enforces the evidence-backed mutation
score floor documented in `CONTRIBUTING.md`, and retains generated mutants plus
metadata for diagnosis. Native Windows is not supported by mutmut; contributors
can use WSL for the same check.

## 11. Releases

This repository uses Release Please with Conventional Commits. Each push to
`main` updates a release pull request. Merging that pull request updates
`CHANGELOG.md`, `version.txt`, and `.codex-plugin/plugin.json`, creates the tag
and draft GitHub Release, then invokes the reusable release engine.

The engine verifies the tag target, builds
`repo-scaffold-plugin-<filesystem-safe-tag>.zip` from the immutable commit,
generates signed SLSA build provenance in a separate no-checkout job, attaches
the asset to the draft, and publishes only after attestation succeeds. The
archive contains `.codex-plugin/`, `skills/`, `README.md`, and `LICENSE` under a
`repo-scaffold/` directory. The workflow requires a fine-grained PAT stored as
`RELEASE_PLEASE_TOKEN`; see [CONTRIBUTING.md](CONTRIBUTING.md) for the release
process and token scope.

After downloading an asset, verify its provenance and signer workflow:

```bash
gh attestation verify repo-scaffold-plugin-vX.Y.Z.zip \
  --repo MinhThang1009/repo-scaffold-plugin \
  --signer-workflow MinhThang1009/repo-scaffold-plugin/.github/workflows/release.yml
```

## 12. Maintainers and contributing

This plugin is maintained by Minh Thang. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow, required checks, and pull-request expectations. Project roles and decisions are described in [GOVERNANCE.md](GOVERNANCE.md). The [submission dossier](PLUGIN_SUBMISSION.md) records the public-listing copy, test cases, and external publication prerequisites.

When proposing a change, include the source you verified, the files affected, and the command or manual check used to validate the result.

## 13. License

MIT. See [LICENSE](LICENSE).
