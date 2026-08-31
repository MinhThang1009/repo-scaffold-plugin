# Pull-request contract

Read this reference only for authorized PR creation or body updates.

Read the trusted template set from the target base branch. For a Conventional
Commit PR title beginning with `feat`, `fix`, or `docs`, select `feature`,
`bugfix`, or `documentation`, respectively. The `pr-template` gate rejects a
mismatched marker. For other title types, use the default template unless the
change genuinely needs a focused security, deployment, or dependency-update
review. Preserve exactly one `<!-- repo-scaffold:pr-template=<id> -->` marker,
every required heading, and every required-checklist item. The optional
checklist is guidance: include only applicable items.

Replace guidance with concrete verification evidence. A draft PR may leave
required items unchecked; before ready-for-review, tick an item only after its
work is complete. Write UTF-8 PR text to a file and use `gh pr create --body-file`
or `gh pr edit --body-file`; do not bypass the template with `--fill` or a
free-form body.
