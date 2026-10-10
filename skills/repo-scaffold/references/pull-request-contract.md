# Pull-request contract

Read this reference only for authorized PR creation or body updates.

Before creating or editing a PR, run
`python scripts/pr_template_preflight.py --title "<title>"` from the target
repository. It validates the checked-in template catalog and identifies the
template required by the title. For a focused security, deployment, or
dependency-update review whose title has no mandatory mapping, pass
`--template security`, `--template deployment`, or
`--template dependency-update`. For a Conventional Commit PR title beginning
with `feat`, `fix`, or `docs`, select `feature`, `bugfix`, or `documentation`,
respectively. The `pr-template` gate rejects a mismatched marker or override.
For other title types, use the default template unless the change genuinely
needs a focused review. Preserve exactly one
`<!-- repo-scaffold:pr-template=<id> -->` marker, every required heading, and
every required-checklist item. The optional checklist is guidance: include only
applicable items.
The gate ignores fenced code, comments, and raw HTML blocks when checking for
required headings and checklist items; hidden content cannot satisfy that
structure contract.
After preparing the UTF-8 body file, rerun the preflight with
`--body-file <path> --require-structure` to reject hard-wrapped prose and missing
template headings or required checklist items before the GitHub mutation.
Structure validation allows unchecked draft items; the remote template gate
separately enforces checked items when the PR is ready for review.
Strict CLI validation binds the body marker to the resolved title/`--template`
selection, so a focused review cannot receive a pass for a default-template body.
Template discovery scans at most 10,000 entries in each candidate directory and
accepts at most 128 focused templates with `.md`, `.markdown`, or `.txt`
extensions (case-insensitive). The selected template and supplied body file are
each limited to 1 MiB of valid UTF-8. Catalog discovery searches `.github/`,
the repository root, and `docs/` in that checker-local order. GitHub documents
these locations but does not specify which default wins if several coexist; the
preflight deterministically selects the first candidate in its local order. If
a location contains multiple default templates, preflight rejects the catalog
as ambiguous.

Replace guidance with concrete verification evidence. A draft PR may leave
required items unchecked; before ready-for-review, tick an item only after its
work is complete. Write UTF-8 PR text to a file and use `gh pr create --body-file`
or `gh pr edit --body-file`; do not bypass the template with `--fill` or a
free-form body.

Review every generated section against the complete PR diff. State the affected
behavior and outcome in Purpose, the confirmed defect in Root cause, and all
material final changes in a few Key changes bullets. Explain tests and known
limits in Verification. Keep acceptance criteria, security/risk notes, and
rollout or rollback plans when the selected template requires them; automation
must not substitute a file-count summary for those decisions.

The optional `pr-body-sync.yml` workflow uses explicit `Why:`, `Root cause:`,
`Changes:`, and `Verification:` commit fields. A field may contain paragraphs
or bullet lists on following lines, but each authored paragraph and bullet item
must occupy one physical line. Do not hard-wrap commit prose at 72 or 80
characters; separate paragraphs and fields with blank lines and preserve
intentional code-block newlines. Wrapped legacy messages remain supported:
the renderer joins their continuations without rewriting published commits.
Use the selected project language for field values. For a
long PR, use a maintainer-reviewed commit containing
`PR-summary-base: <full-current-base-SHA>` and all four fields to record a
cumulative summary through that commit. The workflow validates the base SHA and
required fields, but cannot verify that a person reviewed the summary or that
its claims match the diff. All later changes remain in scope. Reconcile API
commit/file counts, retain reverts, and reject excess topics rather than
silently omitting them. Missing cause evidence must be stated explicitly. The
cumulative summary must cover the PR's primary behavior and safety-boundary
changes, not only PR-body tooling or documentation.
Commit-derived purpose, cause, key changes, and verification notes are labeled
author-reported at the full source SHA; the Checks-tab reminder is generated
separately. The changed-file inventory proves the returned file count, but it
does not verify narrative claims against patch contents. Use GitHub Checks for
the current revision. Review the complete
body against the diff after rendering and after GitHub accepts the update.
The renderer CLI validates the complete rendered structure against the selected
checked-in template before writing output. Its default repository root is the
current working directory; use `--repository-root` when running it elsewhere.
The workflow runs that CLI from the trusted base checkout, so incomplete bodies
fail before the body file or GitHub description is replaced.
The renderer recognizes generated sections by exact heading aliases outside
code, comments, and raw HTML blocks. Custom risk, acceptance, rollback, and
combined verification/monitoring sections remain author-owned. Legacy head
metadata is removed only from the body preamble; protocol examples elsewhere
are preserved.
Native Dependabot bodies are preserved only for the authenticated
`dependabot[bot]` account with API user type `Bot`. Native Release Please bodies
are preserved only on a `release-please--branches--` branch in the base
repository, authored by a bot or the repository owner. These ownership rules
match the template gate; a branch name alone is insufficient. The renderer
still validates the open PR, current body, exact head/base, complete commit and
file inventories, and resource bounds before preserving an automation body.
GitHub's pull-request update endpoint has no conditional body-write parameter.
A concurrent human body edit after the workflow's final read but before its
update can be overwritten; the workflow minimizes that window and verifies the
result afterward.
Bind every initial, pre-update and post-update receipt to the requested PR
number and original numeric base/head repository IDs, not only matching names
and SHAs. Require a currently active base repository and typed integer
commit/file counts before comparing snapshots; Boolean or float equality is
not valid count evidence. Reject duplicate JSON members rather than allowing
an ambiguous receipt to choose its last value. These rechecks do not provide
a conditional body-write or eliminate the documented concurrent-edit window.
Legacy body output allocates its temporary file before accepting a cleanup
obligation. Publication and temporary-cleanup failures must report a controlled
error without a success message; retain the primary failure when cleanup also
fails. A failed cleanup may leave an owned temp file requiring review. Never
claim the destination was unchanged if publication may already have succeeded.
The renderer adds the current Checks-tab status line automatically; do not
repeat that live-status statement in a structured `Verification:` value.
Keep a cumulative summary to at most five key-change bullets and four
verification notes. Combine related evidence without omitting material scope;
the renderer adds its Checks-tab reminder separately from those four notes.

In the body file, keep each prose paragraph on one physical line and each list
item on a single line; separate paragraphs with blank lines and let GitHub wrap
text to the viewer's width. GitHub renders line breaks in issue and PR bodies as
line breaks, so do not hard-wrap prose at a fixed column; see [line-break
guidance](https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/basic-writing-and-formatting-syntax#line-breaks).
The checker follows GFM block parsing and preserves fenced and indented code,
code spans, autolinks, inline and block HTML, comments, headings, tables, block
quotes, and nested lists while rejecting hard-wrapped prose. A table is
recognized when a matching delimiter row confirms its header, including when
the table starts inside an open paragraph without a separating blank line.
Valid link reference definitions are non-rendering metadata, not prose. They
may be adjacent or contain multiline labels/titles, but cannot interrupt an
existing paragraph. Malformed definitions remain subject to ordinary prose
checks. Opaque definition lines cannot supply required headings/checklists,
and destinations are parsed as data without network requests. Reference scans
share a 4,194,304-character work budget per body; exhaustion fails closed.
Inline-code/comment disambiguation is bounded to 4,194,304 scanned characters
per body or template; exceeding the budget fails validation closed.
