<div align="center">

# repo-scaffold

An Agent Skills plugin for Codex and Claude Code that scaffolds a new repository
to production GitHub.com standard.

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

Provides one shared `repo-scaffold` skill, which Codex and Claude Code can use
when you ask to set up a new repository's standard files. It:

- Generates community-health and repository-maintenance files tailored to the project and enabled repository features: README, CONTRIBUTING, SECURITY, SUPPORT, CODE_OF_CONDUCT, LICENSE, CODEOWNERS, issue templates, a default PR template plus focused `feature.md`, `bugfix.md`, `documentation.md`, `security.md`, `deployment.md`, and `dependency-update.md` templates, Dependabot, CHANGELOG, `.editorconfig`, `.gitignore`, and `.gitattributes`.
- For a verified GitHub.com remote, adds deterministic documentation checks, weekly community-health upstream reminders, pull-request and scheduled link checks, a CI workflow tailored to the detected stack, and a release workflow with provenance attestations when the repository is eligible, plus optional ones (release-please, repository-managed CodeQL advanced setup, dependency review, Dependabot and label-gated auto-merge, commitlint, stale, labeler).
- Configures the verified GitHub.com repository: repository description, classic branch protection, and labels. Existing repository or organization rulesets are inspected as effective policy but are not modified.
- Produces either English or Vietnamese project-facing content. An explicit request wins, followed by active project instructions and the established documentation convention; English is the default when no preference exists.
- Creates one shared `AGENTS.md` instruction entry point for supported agents and
  a minimal `CLAUDE.md` adapter that imports it, so Claude Code and agents that
  consume `AGENTS.md` follow the same project guidance without duplicated rules.

Content follows GitHub's community-standards format and is pulled from canonical
sources where possible (LICENSE and `.gitignore` through the GitHub API, and the
Code of Conduct from the official Contributor Covenant repository), with
project-specific content generated from the repository itself. External GitHub
Actions are pinned to immutable commit SHAs. Dependabot keeps installed
workflows current; a weekly PR-only synchronizer mirrors reviewed releases to
scaffold workflow assets. A separate scheduled freshness audit compares action
pins, Release Please schemas, and direct Python pins with their authoritative
upstreams, then maintains one reminder issue until the drift is resolved.
An independent weekly official-documentation review validates the allowlisted
GitHub, OpenAI, and Claude Code source pages, their claim markers, and the
review interval recorded for each affected plugin document. It opens one
reminder Issue for human review rather than auto-editing prose.
Shipped workflows do not delegate execution to a mutable container tag.
CodeQL and Scorecard also support `workflow_dispatch` for an on-demand security
scan; run Scorecard from the default branch because it evaluates default-branch
repository policy.

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

Install the plugin (see below), then, in any repository, ask your supported
agent:

- "scaffold this repo"
- "scaffold this repo in English"
- "set up the repo to production standard"
- "dựng repo chuẩn GitHub bằng tiếng Việt"

The skill activates automatically and walks through: survey → decisions → file generation → workflows → GitHub configuration → handoff → verification. It resolves one project language (`en` or `vi`) before generation and applies it consistently to documentation, templates, and release metadata. It never overwrites existing files without asking, leaves changes unstaged and uncommitted unless you explicitly request Git operations, and confirms outward-facing actions first.

For supported adapters, invocation, and generic Agent Skills use, read the
[agent compatibility guidance](skills/repo-scaffold/references/agent-compatibility.md)
or its [Vietnamese version](skills/repo-scaffold/references/agent-compatibility.vi.md).

## 5. Support

See [SUPPORT.md](SUPPORT.md) for usage help and the information to include in a report. For a local installation, contact the person or workspace that shared the plugin.

Report vulnerabilities according to [SECURITY.md](SECURITY.md), never through a public issue. Community participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md). See the [Privacy Policy](PRIVACY.md) and [Terms of Use](TERMS.md) before using or distributing the plugin.

## 6. Install

Public plugins are installed from the universal Plugin Directory shared by
ChatGPT and Codex. This repository is not yet claiming a public-directory
listing. Its release ZIP meets the archive layout for OpenAI's [Skills-only
submission flow](https://developers.openai.com/plugins/guides/submit-claude-plugin):
one `repo-scaffold/` root, a nonempty Claude manifest, and the shared
`skills/repo-scaffold/SKILL.md`. Submission and review still occur in the
OpenAI portal.

For a private or local Codex installation, add this repository's marketplace and
install the plugin, then start a new Codex thread in the target repository:

```powershell
codex plugin marketplace add MinhThang1009/repo-scaffold-plugin
codex plugin add repo-scaffold@repo-scaffold-plugins
```

The catalog is `.agents/plugins/marketplace.json`; its source path resolves from
the marketplace root. A personal marketplace at `~/.agents/plugins/marketplace.json`
is still appropriate when the checkout must remain local to one user.

Claude Code distribution is separate. Public third-party listings are submitted
through Anthropic's `claude-community` marketplace using its in-app form.
`claude-plugins-official` is a separately curated marketplace. Until a Claude
Code listing is approved, do not treat Codex Plugin Directory availability as a
Claude Code listing. For a private or local Claude Code installation, add this
repository as a marketplace and install the plugin:

```powershell
claude plugin validate --strict .
claude plugin marketplace add MinhThang1009/repo-scaffold-plugin
claude plugin install repo-scaffold@repo-scaffold-plugins
```

Restart Claude Code, then invoke `/repo-scaffold:repo-scaffold`, or ask Claude
to scaffold the current repository. For local development only, validate and
load the checkout directly with `claude --plugin-dir .`. The release archive
contains both host manifests and both marketplace catalogs. Claude Code
v2.1.128 or later can load that ZIP directly with `claude --plugin-dir`; on an
older Claude Code release, extract it before loading or adding it as a local
marketplace.

## 7. Update

After the local marketplace source and plugin version have been updated, reinstall the plugin and start a new Codex thread:

```powershell
codex plugin marketplace upgrade repo-scaffold-plugins
codex plugin remove repo-scaffold@repo-scaffold-plugins
codex plugin add repo-scaffold@repo-scaffold-plugins
claude plugin marketplace update repo-scaffold-plugins
claude plugin update repo-scaffold@repo-scaffold-plugins
```

Restart Codex or Claude Code after updating. Existing sessions keep the skill
version they loaded when the session started.

## 8. Uninstall

Remove the installed plugin from the marketplace that supplied it:

```powershell
codex plugin remove repo-scaffold@repo-scaffold-plugins
claude plugin uninstall repo-scaffold@repo-scaffold-plugins
```

If you no longer use this catalog, remove it separately with
`codex plugin marketplace remove repo-scaffold-plugins` or
`claude plugin marketplace remove repo-scaffold-plugins`.

## 9. Structure

Key implementation and release files are shown below. Community policy and
template files are omitted for brevity.

```text
repo-scaffold/
├── .agents/plugins/
│   └── marketplace.json
├── .coveragerc
├── .release-please-manifest.json
├── .markdownlint-cli2.jsonc
├── .claude-plugin/
│   ├── marketplace.json
│   └── plugin.json
├── .codex-plugin/
│   └── plugin.json
├── .github/
│   ├── CODEOWNERS
│   ├── ci-toolchain.json
│   ├── dependabot.yml
│   ├── labeler.yml
│   ├── official-docs-trackers.json
│   ├── python-support.json
│   ├── release.yml
│   └── workflows/
│       ├── ci.yml
│       ├── codeql.yml
│       ├── commitlint.yml
│       ├── dependency-review.yml
│       ├── freshness.yml
│       ├── labeler.yml
│       ├── links.yml
│       ├── mutation-testing.yml
│       ├── official-docs.yml
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
├── requirements-dev.in
├── requirements-dev.txt
├── requirements-mutation.in
├── requirements-mutation.txt
├── TERMS.md
├── version.txt
├── scripts/
│   ├── prepare_mutation_cache.py
│   ├── python_support.py
│   ├── run_mutation_testing.py
│   ├── audit_freshness.py
│   ├── audit_official_docs.py
│   ├── pr_template_preflight.py
│   ├── sync_action_pins.py
│   ├── sync_versioned_inputs.py
│   ├── validate_mutation_results.py
│   ├── validate_repository.py
│   └── validate_workflows.py
└── skills/
    └── repo-scaffold/
        ├── SKILL.md
        ├── scripts/
        │   ├── audit_freshness.py      # registry-driven stale-input checker
        │   ├── check_community_health.py # versioned upstream drift checker
        │   ├── ci_toolchain.py      # centralized CI bootstrap/tool pin policy
        │   ├── codeql_preflight.py  # fail-closed CodeQL/reusable-workflow inspection
        │   ├── pr_template_preflight.py # PR template selection and validation
        │   ├── sync_action_pins.py  # immutable action-release resolver
        │   └── validate_scaffold.py # rendered Markdown and template contract
        ├── references/
        │   ├── readme.md          # README structure guidance
        │   └── github-setup.md    # exact gh configuration commands
        └── assets/                # community-health files + config files (labeler, release configs)
            └── workflows/         # ci, docs, community-health, links, release engine/dispatcher, release-please, CodeQL,
                                   # Scorecard, dependency-review, auto-merge, commitlint, stale, labeler
```

SKILL.md → Resources identifies the shipped resource groups and their purpose.

## 10. Development validation

The plugin has no compilation step. Its validation tests require a CPython release from the [Python support policy](.github/python-support.json) and the hash-locked development toolchain, including Coverage.py, markdown-it-py for CommonMark parsing, PyYAML, and pytest. Run the repository checks from its root:

```powershell
python -m pip install --require-hashes --requirement requirements-dev.txt
python -m coverage erase
python -m coverage run -m pytest -q
python -m coverage report
python -m ruff format --check skills scripts tests
python -m ruff check skills scripts tests
python -m mypy --explicit-package-bases skills/repo-scaffold/scripts/check_community_health.py skills/repo-scaffold/scripts/audit_freshness.py skills/repo-scaffold/scripts/codeql_preflight.py skills/repo-scaffold/scripts/ci_toolchain.py skills/repo-scaffold/scripts/pr_template_preflight.py skills/repo-scaffold/scripts/sync_action_pins.py skills/repo-scaffold/scripts/validate_scaffold.py scripts/audit_freshness.py scripts/audit_official_docs.py scripts/check_code_scanning_alerts.py scripts/pr_template_preflight.py scripts/prepare_mutation_cache.py scripts/python_support.py scripts/run_mutation_testing.py scripts/sync_action_pins.py scripts/sync_versioned_inputs.py scripts/validate_mutation_results.py scripts/validate_repository.py scripts/validate_workflows.py tests
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
documentation drift. Scheduled/manual canaries maintain one reminder Issue when
either reviewed policy needs attention. The quality job also runs formatting, lint, type, compile,
workflow, metadata, link, and release-archive checks.
The [CI toolchain policy](.github/ci-toolchain.json) separately centralizes the
rolling documentation bootstrap runtime, minimum Python for bundled tooling,
the markdownlint npm pin, and reviewed standalone-tool release metadata and
digests. Workflows and setup guidance consume that policy instead of embedding
those values, and a non-required scheduled/manual canary reports npm, upstream
release, or digest drift for review through the same durable reminder Issue.
Dependabot checks the pinned Python development tools and mirrored scaffold
documentation dependencies weekly.
`requirements-dev.in` records reviewed direct pins; `requirements-dev.txt`
resolves every transitive dependency and records PyPI SHA-256 hashes used by CI.
The conventional `.in` to `.txt` pairing lets Dependabot run `pip-compile` and
update both files in one PR. Platform-conditional packages required by the
supported matrix are pinned directly so a lock regenerated on Linux remains
installable with hashes on Windows. A weekly PR-only version-maintenance
synchronizer creates one draft PR with immutable GitHub Action pins and Release
Please schema URLs updated in lockstep across repository workflows,
configuration, and scaffold assets. It uses a dedicated fine-grained PAT stored
as `VERSION_SYNC_TOKEN`, so normal PR CI runs; it never auto-merges. Scope that
token to this repository only, with **Contents: Read and write**, **Pull
requests: Read and write**, and **Workflows: Read and write** because the
synchronizer may update workflow files. Keep it separate from
`RELEASE_PLEASE_TOKEN` to avoid granting release automation unnecessary workflow
write access. Python updates are
grouped by dependency across the root toolchain and
`skills/repo-scaffold/assets/requirements-docs.txt`; security updates for the
two mirrored documentation packages are grouped explicitly.
The non-required weekly [freshness workflow](.github/workflows/freshness.yml)
reads the reviewed [freshness tracker registry](.github/freshness-trackers.json)
and independently reports direct-PyPI-pin and lock-consistency drift, plus any
versioned input the PR synchronizer could not make current. It opens or updates
one marker Issue when attention is required and closes it only after a clean
scheduled/manual result. The
scaffold ships the same registry-driven checker and workflow to generated
repositories when Issues are available. Track only sources with an
authoritative owner and deterministic version resolver; community-health policy
tracking remains in its separate registry.
The non-required weekly [official-documentation workflow](.github/workflows/official-docs.yml)
uses [its explicit tracker registry](.github/official-docs-trackers.json) to
revalidate the authoritative source URLs and stable claim markers, then requires
a reviewed registry-date update at least every 90 days. It covers the plugin's
Codex, Claude Code, GitHub Actions, Agent Skills, Conventional Commits, and
Keep a Changelog claims. Generated repositories do not inherit those
plugin-specific claims.
Mutation testing extends that toolchain through the separate, hash-verified
`requirements-mutation.txt`. Mutmut versions are not duplicated in validators
or tests; a compatible Dependabot bump passes the runner integration tests,
while an incompatible internal API change fails those behavioral checks. Its
daily and manually dispatched workflow runs mutmut on Linux, rejects
incomplete runs, enforces the evidence-backed mutation score floor documented in
`CONTRIBUTING.md`, and retains generated mutants plus metadata for diagnosis. A
bounded mutation step records interrupted progress before the job ends; later
runs reset every non-killed result and may resume an explicitly verified
same-repository, same-commit artifact. Its cache is invalidated only by mutation
source, test, or mutation-control changes, so documentation and release metadata
do not discard safe interrupted progress. Native Windows is not supported by
mutmut; contributors can use WSL for the same check.

## 11. Releases

This repository uses Release Please with Conventional Commits. Each push to
`main` updates a release pull request. Merging that pull request updates
`CHANGELOG.md`, `version.txt`, `.codex-plugin/plugin.json`, and
`.claude-plugin/plugin.json`, creates the tag
and draft GitHub Release, then invokes the reusable release engine.

The engine verifies the tag target, builds
`repo-scaffold-plugin-<filesystem-safe-tag>.zip` from the immutable commit,
generates signed SLSA build provenance in a separate no-checkout job, attaches
the asset to the draft, and publishes only after attestation succeeds. The
archive contains `.agents/`, `.codex-plugin/`, `.claude-plugin/`, `skills/`, `README.md`, and
`LICENSE` under a
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
