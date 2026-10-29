# Scaffold generation contract

Read this reference before generating or updating files.

Fetch LICENSE and `.gitignore` canonical text through GitHub's API. Replace only
implementation tokens explicitly designated by the selected license, preserve
canonical legal text, and write `.gitignore` as UTF-8 without a BOM. Obtain the
latest stable Contributor Covenant from its official immutable release-branch
commit. Use an official Vietnamese source only when it exists for that exact
version; otherwise retain canonical English and report the gap.
The upstream discovery receipt must come from a complete, unambiguous tree
with at most 100,000 entries and a regular policy blob at the selected path.
Record its commit and blob SHA; directories, symlinks, duplicate paths and
missing object identities are inconclusive. Version components are bounded to
32 digits before integer conversion. A version/current result checks freshness,
not canonical-body completeness, enforcement approval or license compliance;
review those separately before installing the policy.
The canonical source may include website front matter. Parse and remove only
that metadata envelope; verify its version against the selected immutable
receipt and retain the policy body and attribution. Resolve only upstream
reporting/enforcement implementation notes using confirmed project choices.
Do not publish website metadata, copy unresolved notes, invent an enforcement
commitment or translate canonical legal text unofficially. The MIT license API's
implementation instructions designate `[year]` and `[fullname]`; other licenses
need their own reviewed implementation instructions, not MIT's token rules.

Copy assets and bundled scripts from this skill byte-for-byte where applicable.
Every installed workflow that requires a bundled script, including an imported
helper, must also receive the corresponding script at its repository-root
destination:

| Workflow asset | Bundled source | Generated destination |
| --- | --- | --- |
| `assets/workflows/code-scanning-gate.yml` | `assets/code-scanning-allowlist.json` | `.github/code-scanning-allowlist.json` |
| `assets/workflows/code-scanning-gate.yml` | `../../../scripts/check_code_scanning_alerts.py` | `scripts/check_code_scanning_alerts.py` |
| `assets/workflows/community-health.yml` | `../scripts/check_community_health.py` | `scripts/check_community_health.py` |
| `assets/workflows/community-health.yml` | `../scripts/markdown_body_preflight.py` | `scripts/markdown_body_preflight.py` |
| `assets/workflows/community-health.yml` | `assets/community-health-trackers.json` | `.github/community-health-trackers.json` |
| `assets/workflows/documentation.yml` | `../scripts/ci_toolchain.py` | `scripts/ci_toolchain.py` |
| `assets/workflows/documentation.yml` | `assets/ci-toolchain.json` | `.github/ci-toolchain.json` |
| `assets/workflows/documentation.yml` | `../scripts/validate_scaffold.py` | `scripts/validate_scaffold.py` |
| `assets/workflows/documentation.yml` | `../scripts/markdown_body_preflight.py` | `scripts/markdown_body_preflight.py` |
| `assets/workflows/documentation.yml` | `assets/requirements-docs.txt` | `requirements-docs.txt` |
| `assets/workflows/documentation.yml` | `assets/markdownlint-cli2.jsonc` | `.markdownlint-cli2.jsonc` |
| `assets/workflows/freshness.yml` | `../scripts/audit_freshness.py` | `scripts/audit_freshness.py` |
| `assets/workflows/freshness.yml` | `../scripts/markdown_body_preflight.py` | `scripts/markdown_body_preflight.py` |
| `assets/workflows/freshness.yml` | `assets/freshness-trackers.json` | `.github/freshness-trackers.json` |
| `assets/workflows/freshness.yml` | `assets/requirements-docs.txt` | `requirements-docs.txt` |
| `assets/workflows/freshness.yml` | `../scripts/ci_toolchain.py` | `scripts/ci_toolchain.py` |
| `assets/workflows/freshness.yml` | `assets/ci-toolchain.json` | `.github/ci-toolchain.json` |
| `assets/workflows/freshness.yml` | `../scripts/sync_action_pins.py` | `scripts/sync_action_pins.py` |
| `assets/workflows/pr-body-sync.yml` | `../scripts/render_pr_body_evidence.py` | `scripts/render_pr_body_evidence.py` |
| `assets/workflows/pr-body-sync.yml` | `../scripts/markdown_body_preflight.py` | `scripts/markdown_body_preflight.py` |
| `assets/workflows/pr-body-sync.yml` | `../scripts/pr_template_preflight.py` | `scripts/pr_template_preflight.py` |
| `assets/workflows/labeler.yml` | `assets/labeler.yml` | `.github/labeler.yml` |
| Pull-request preflight | `../scripts/pr_template_preflight.py` | `scripts/pr_template_preflight.py` |
| Pull-request preflight dependency | `../scripts/markdown_body_preflight.py` | `scripts/markdown_body_preflight.py` |

Workflow installation is an explicit generation decision. For a verified
GitHub.com project with a runnable test or lint command, install a configured CI
workflow before declaring the scaffold complete, unless the user explicitly
defers it. For every applicable approved asset, run the read-only
`workflow_installation_preflight.py` with the exact `--workflow` inputs, copy
the companion files in the table, and verify the installed files. Record each
optional asset as installed, not applicable, or explicitly deferred; never
silently omit an applicable workflow or leave the generic CI sentinel in the
finished project.

Documentation installation includes the reviewed Markdownlint companion at
`.markdownlint-cli2.jsonc`. It keeps the default lint rules, permits the known
centered-header HTML elements, semantic line wrapping, plain contact links and
non-H1 template/import entry points. Do not replace an existing project lint
policy without approval: inspect its effective configuration and adapt output
or request a reviewed policy change. Missing/uncopied policy is a verification
gap, not permission to disable lint. Remove whole lines for omitted optional
markers and redundant trailing blank lines in generated Markdown; do not alter
literal code examples or existing project-authored content while doing so.

The `pr-body-sync.yml` asset is the opt-in PR metadata workflow. Install it
with `scripts/render_pr_body_evidence.py`, `scripts/markdown_body_preflight.py`,
and `scripts/pr_template_preflight.py`. It renders the body from the exact PR
commits and structured commit fields at the current head SHA. Purpose, cause,
and key-change text is labeled author-reported with its source commit SHA. The
complete changed-file inventory is used to verify pagination and reconcile API
counts; the workflow does not inspect patches or infer change themes. It keeps
live check status in GitHub's Checks tab instead of embedding a stale snapshot
in the body. It does not consume a checked-in narrative body source or execute
pull-request code.
Copy the complete template catalog in the selected language and retain its
scope/verification guidance. Read `pull-request-contract.md` before preparing
structured commit fields or a cumulative `PR-summary-base:` description. The
renderer verifies commit/file inventory counts and fails instead of silently
dropping material topics; review content against the full diff after rendering.

When installing the CodeQL or code-scanning gate asset, also copy
`assets/code-scanning-allowlist.json` to
`.github/code-scanning-allowlist.json`. Keep the scaffold allowlist empty unless
the target repository has independently reviewed a specific alert and recorded
its exact alert number, selector, reason, `reviewed-on`, and
`review-period-days`. Never reuse an exception for a new alert, even when its
tool, rule, and path match a prior alert. The freshness reminder reopens review
when an exception reaches its review date; retain `freshness.yml` whenever this
gate is installed. Its tracker treats the allowlist as optional until the
CodeQL or code-scanning gate asset creates it.
The gate polls the Pull Request API for GitHub's mergeable test commit before
checking CodeQL uploads. Do not substitute the event payload's
`merge_commit_sha`, which can be absent while GitHub is calculating mergeability.
Before generation, ask: "Which project-output language should I use: en, vi, or
both? If both, which should be primary for canonical GitHub files?" Accept only
`en`, `vi`, or `both`; in `both` mode, require the primary language explicitly.
Set `SCAFFOLD_LANGUAGES` to the confirmed set and `SCAFFOLD_LANGUAGE` to its
primary language. For `en`, use canonical assets. For `vi`, render the matching
`.vi` sidecar to its canonical target name. Keep the language-neutral
`CLAUDE.md` adapter unchanged so it imports the canonical `AGENTS.md`.

In `both` mode, do not combine translations inside a single file. Use the primary
language for canonical project-authored paths and single-location configuration,
including `AGENTS.md`, community-health files, `CITATION.cff`, release
configuration, workflow messages, labels, changelog headings, and authorized
commit, pull-request, or release text. Canonical upstream legal text follows its
separate source-language rule below. The primary language also controls
`.github/ISSUE_TEMPLATE/config.yml` and the default
`.github/PULL_REQUEST_TEMPLATE.md`.

Generate a secondary-language README from the same confirmed project facts under
`docs/locales/<secondary-language>/README.md`. Render the other translated
documents from their reviewed locale assets in that directory, using the names
`AGENTS.translation.md`, `CONTRIBUTING.translation.md`,
`SECURITY.translation.md`, `SUPPORT.translation.md`,
`CHANGELOG.translation.md`, and `GOVERNANCE.translation.md` as applicable. Do not
use special active basenames such as `AGENTS.md`, `CLAUDE.md`, `SECURITY.md`, or
`CONTRIBUTING.md` in that directory. Add language links in the root README and in
each generated canonical document that has a translation. Do not create inactive
locale copies of `CITATION.cff`, release configuration, or workflow files. Keep the
`CODE_OF_CONDUCT` and other canonical upstream text in its verified source language
unless a reviewed translation exists; do not create an unofficial legal-policy
translation.

When both locales are selected, provide both translations as distinct GitHub
templates. Put issue forms under `.github/ISSUE_TEMPLATE/` with locale-prefixed
filenames such as `en-bug-report.yml` and `vi-bug-report.yml`, preserving each
asset's localized `name` and `description`. Keep exactly one `config.yml`, rendered
in the primary language; never copy `config.vi.yml` as an extra issue form. Keep
the primary default PR template at `.github/PULL_REQUEST_TEMPLATE.md`. Put both
locales of each focused PR template and the secondary default template in
`.github/PULL_REQUEST_TEMPLATE/` with locale-prefixed names, such as
`en-feature.md`, `vi-feature.md`, and `vi-default.md`. GitHub supports multiple
issue templates in `.github/ISSUE_TEMPLATE/` and multiple PR templates in a
`PULL_REQUEST_TEMPLATE/` subdirectory; PR variants are selected with GitHub's
`template` query parameter. Do not imply that the default PR body automatically
offers a language chooser.

For a single-language selection, preserve the existing behavior: render all
project-authored human-facing content in `SCAFFOLD_LANGUAGE`, and do not create
alternate-language outputs. If another language is requested, explain that the
reviewed assets currently support only `en` and `vi`; do not silently fall back or
generate until the user selects a supported option or stops. Project instructions
and existing documentation may inform the recommendation but may not replace this
confirmation. Do not infer English from identifiers or technical literals. A
multilingual project uses separate linked files; it must not leave a hybrid file.

For the freshness workflow, keep the English asset for `en`. For `vi`, render
the same canonical `.github/workflows/freshness.yml` using YAML data, add exactly
one `--language vi` argument to the audit invocation, and use the reviewed
display strings below in their corresponding literal title, comment, fallback,
and `printf` values. The checker localizes report headings and prose; its JSON
schema, finding/status values, technical diagnostic text, exit codes, marker,
paths, shell syntax, `%s` placeholders, and `\n` escapes remain unchanged.
Do not translate the stable `freshness-audit` job name or any GitHub context.
The optional language argument accepts only `en` or `vi`; omitting it preserves
the English report contract. No language argument may override a path, token,
tracker, or checker-status binding.
The standalone freshness CLI configures its standard text stdout as UTF-8 so
localized output cannot change an indeterminate exit code on Windows pipes.
Library calls preserve caller-supplied text streams.

| English display text | Vietnamese display text |
| --- | --- |
| `Repository freshness` | `Freshness của repository` |
| `Checkout` | `Checkout repository` |
| `Set up Python` | `Thiết lập Python` |
| `Audit versioned maintenance inputs` | `Kiểm tra các đầu vào bảo trì theo phiên bản` |
| `Add report to job summary` | `Thêm báo cáo vào job summary` |
| `Reconcile reminder issue` | `Đồng bộ issue nhắc bảo trì` |
| `# Repository freshness report` | `# Báo cáo freshness của repository` |
| `The checker failed before it could produce a report. Inspect this workflow run.` | `Checker đã lỗi trước khi tạo báo cáo. Hãy kiểm tra workflow run này.` |
| `Repository freshness update required` | `Cần cập nhật các đầu vào bảo trì repository` |
| `Found multiple open freshness reminder issues.` | `Có nhiều issue nhắc bảo trì freshness đang mở.` |
| `Freshness checker returned an unexpected exit status: %s` | `Checker freshness trả về exit status không hợp lệ: %s` |
| `Freshness checker was indeterminate; no reminder issue was changed.` | `Checker freshness chưa xác định được kết quả; không thay đổi issue nhắc bảo trì.` |
| `The scheduled freshness audit is clean, so this reminder is closing automatically.` | `Kiểm tra freshness định kỳ không phát hiện đầu vào lỗi thời, nên tự động đóng issue nhắc bảo trì này.` |

The following Vietnamese mappings are canonical targets for single-language `vi`
or when `vi` is the primary language. In `both` mode, use these mappings for the
primary target and the secondary-language paths described above for translations:

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
- `PULL_REQUEST_TEMPLATE.vi/dependency-update.md` → `.github/PULL_REQUEST_TEMPLATE/dependency-update.md`
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

Validate rendered Issue Forms as typed YAML data, not scalar text that conflates
quoted strings with Boolean or integer values. GitHub permits an omitted input
`id`; when one is supplied, retain its valid unique identifier. Require distinct
dropdown choices, typed `multiple` and a valid integer `default` index; a default
cannot accompany `None` or `n/a` choices. Text `min_length` is a non-negative
integer, checkbox/validation `required` is Boolean, and upload `accept` is a
comma-separated extension string. Missing required top-level fields produce
diagnostics rather than exceptions; unknown per-type attributes/validations,
duplicate keys, malformed values and unsafe paths must reject. This local
schema check does not prove label existence, submission rights, GitHub feature
availability or remote rendering; those retain separate applicability checks.
Form names must have more than three characters to appear in GitHub's chooser
and must be unique among the YAML and Markdown issue templates in the same
directory. An existing template's name is part of that comparison; do not
overwrite it or silently rename project-authored content to obtain a pass.
Optional top-level `title` must remain a string; `labels`, `assignees` and
`projects` accept string arrays or comma-delimited strings without coercion.
Preserve omitted fields and valid empty selections rather than fabricating
defaults. A schema pass does not prove that assigned labels/accounts/projects
exist, that an organization-defined issue type is available, or that the
submitter has project write permission.

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
