# Scaffold generation contract

Read this reference before generating or updating files.

Fetch LICENSE and `.gitignore` canonical text through GitHub's API. Replace only
implementation tokens explicitly designated by the selected license, preserve
canonical legal text, and write `.gitignore` as UTF-8 without a BOM. Obtain the
latest stable Contributor Covenant from its official immutable release-branch
commit. Use an official Vietnamese source only when it exists for that exact
version; otherwise retain canonical English and report the gap.

Copy assets and bundled scripts from this skill byte-for-byte where applicable.
For English, use canonical assets. For Vietnamese, render the matching `.vi`
sidecar to its canonical target name, including `AGENTS.md`, community-health
files, issue and PR templates, `CITATION.cff`, release config, and
release-please config. Never leave a locale suffix on a generated target. Keep
the language-neutral `CLAUDE.md` adapter unchanged so it imports `AGENTS.md`.

Render every project-authored, human-facing surface in one
`SCAFFOLD_LANGUAGE`, either `en` or `vi`: documentation, templates, workflow
messages, labels, changelog headings, release notes, and commit, pull-request,
or release text created as part of an authorized scaffold. Resolve the language
from the user's explicit language request, then active project instructions,
then the dominant first-party human-facing documentation, then `en` as the
fallback. Ask when higher-priority signals conflict. Never leave an
English/Vietnamese hybrid, and do not infer English from identifiers or
technical literals.

The Vietnamese mappings are:

- `AGENTS.vi.md` → `AGENTS.md`
- `CONTRIBUTING.vi.md` → `CONTRIBUTING.md`
- `SECURITY.vi.md` → `SECURITY.md`
- `SUPPORT.vi.md` → `SUPPORT.md`
- `CHANGELOG.vi.md` → `CHANGELOG.md`
- `GOVERNANCE.vi.md` → `GOVERNANCE.md`
- `PULL_REQUEST_TEMPLATE.vi.md` → `.github/PULL_REQUEST_TEMPLATE.md`
- `PULL_REQUEST_TEMPLATE.vi/feature.md` → `.github/PULL_REQUEST_TEMPLATE/feature.md`
- `PULL_REQUEST_TEMPLATE.vi/bugfix.md` → `.github/PULL_REQUEST_TEMPLATE/bugfix.md`
- `PULL_REQUEST_TEMPLATE.vi/documentation.md` → `.github/PULL_REQUEST_TEMPLATE/documentation.md`
- `PULL_REQUEST_TEMPLATE.vi/security.md` → `.github/PULL_REQUEST_TEMPLATE/security.md`
- `PULL_REQUEST_TEMPLATE.vi/deployment.md` → `.github/PULL_REQUEST_TEMPLATE/deployment.md`
- `CITATION.vi.cff` → `CITATION.cff`
- `release-config.vi.yml` → `.github/release.yml`
- `release-please-config.vi.json` → `release-please-config.json`
- `ISSUE_TEMPLATE/bug_report.vi.yml` → `.github/ISSUE_TEMPLATE/bug_report.yml`
- `ISSUE_TEMPLATE/feature_request.vi.yml` → `.github/ISSUE_TEMPLATE/feature_request.yml`
- `ISSUE_TEMPLATE/config.vi.yml` → `.github/ISSUE_TEMPLATE/config.yml`

Only emit capability-dependent content after verified capability exists: issue
forms and reminder workflows need Issues; Discussions links need Discussions;
GitHub.com badges need a verified GitHub.com repository and the installed
workflow/license. Omit a dependent section rather than creating a dead link.

Replace only documented `{{REPO_SCAFFOLD_*}}` markers in their known context.
Record the exact markers in each source before rendering, then scan only the
rendered output for those markers. Do not use a generic double-brace scan:
project documentation may legitimately contain template expressions. Parse every
rendered YAML/JSON file, construct YAML data with a serializer instead of string
concatenation, and escape Markdown display text or URL components at their sink.

Encode values for their destination instead of doing blind text replacement. Use
`{{REPO_SCAFFOLD_DEFAULT_BRANCH_GLOB_JSON_ESCAPED}}` only in workflow branch
filters. Replace every `${{` with `[$*]{{` after escaping `!` as `\\!` and `+`
as `\\+` in the exact branch name. JSON-encode the resulting pattern and remove only
the JSON string's surrounding quotes. This prevents a valid branch name such as
`${{true}}` from opening a GitHub Actions expression while preserving its
literal branch-filter meaning. Use `{{REPO_SCAFFOLD_DEFAULT_BRANCH}}` only for
plain Markdown display text, with HTML and Markdown escaping appropriate to the
sink.

For Dependabot, retain the fixed root `pip` update for
`requirements-docs.txt` and the fixed root `github-actions` update with
`patterns: ["*"]`. Generate further package updates only from confirmed
first-party manifests, use GitHub's exact ecosystem identifiers and
repository-relative directories, and give every generated block
`commit-message.prefix: "chore(deps)"`. Do not emit a duplicate root `pip` block.
Do not infer an ecosystem from language alone, and retain both fixed
blocks when no additional supported application location exists.

Before changing `pull-request-title-pattern` in an existing release-please
configuration, update each existing release PR title to the selected language
and template before treating that PR as ready for review.
