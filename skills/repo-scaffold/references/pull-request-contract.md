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
`--body-file <path>` to reject hard-wrapped prose before the GitHub mutation.
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
`Changes:`, and `Verification:` commit fields, with multiline paragraphs and
bullets supported. Use the selected project language for their values. For a
long PR, a reviewed commit containing `PR-summary-base: <full-current-base-SHA>`
and all four fields records a cumulative summary through that commit. All later
changes remain in scope. Reconcile API commit/file counts, retain reverts, and
reject excess topics rather than silently omitting them. Missing cause evidence
must be stated explicitly. Commit verification notes are author-reported at the
source SHA; use GitHub Checks for the current revision. Review the complete
body again after rendering and after GitHub accepts the update.

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
Inline-code/comment disambiguation is bounded to 4,194,304 scanned characters
per body or template; exceeding the budget fails validation closed.
