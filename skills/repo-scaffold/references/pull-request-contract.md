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
After preparing the UTF-8 body file, rerun the preflight with
`--body-file <path>` to reject hard-wrapped prose before the GitHub mutation.

Replace guidance with concrete verification evidence. A draft PR may leave
required items unchecked; before ready-for-review, tick an item only after its
work is complete. Write UTF-8 PR text to a file and use `gh pr create --body-file`
or `gh pr edit --body-file`; do not bypass the template with `--fill` or a
free-form body.

In the body file, keep each prose paragraph on one physical line and each list
item on a single line; separate paragraphs with blank lines and let GitHub wrap
text to the viewer's width. GitHub renders line breaks in issue and PR bodies as
line breaks, so do not hard-wrap prose at a fixed column; see [line-break
guidance](https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/basic-writing-and-formatting-syntax#line-breaks).
