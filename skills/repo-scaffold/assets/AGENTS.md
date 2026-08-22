# Agent Instructions

This file is the shared instruction entry point for coding agents that support
`AGENTS.md`.

## Working agreement

- Follow the user's request and the repository's applicable instructions.
- Read the relevant files before making a change and preserve project-specific
  conventions.
- Keep changes focused, validate them with the project's relevant checks, and
  report any verification that could not run.
- Do not commit, push, create pull requests, or change remote settings unless
  the user explicitly requests that action.

## Pull requests

Before creating or updating a pull request, read the trusted template set from
the target base branch. Use the default template unless the change genuinely
needs feature, bugfix, documentation, or security review. Preserve exactly one
matching `<!-- repo-scaffold:pr-template=<id> -->` marker, every required
heading, and every required-checklist item from the selected template. Include
an `If applicable` item only when it applies, and omit that section when none
apply. A draft PR may leave required items unchecked; before marking it ready
for review, tick a required item only after it is complete. Use a UTF-8 body
file with `gh pr create --body-file` or `gh pr edit --body-file`. Do not use `--fill`
or a free-form `--body` value that bypasses the template.

## Language

Use the dominant language of existing project-facing documentation. When it is
unclear, ask before creating substantial user-facing prose.
