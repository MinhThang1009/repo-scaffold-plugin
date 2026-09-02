# Maintainer instructions

## Repository purpose

This repository distributes the `repo-scaffold` skill for Codex and Claude
Code. Keep the shared skill, both plugin manifests, and generated scaffold
assets compatible with their documented host contracts.

## Changes and validation

- Read the relevant source and its matching test before editing. Keep source
  scripts, generated assets, and their documented contracts synchronized.
- Run `python -m pytest -q` for behavior changes.
- Run `python scripts/validate_repository.py` and
  `python skills/repo-scaffold/scripts/validate_scaffold.py --repository-root . --template-root skills/repo-scaffold/assets`.
- Run `python scripts/validate_workflows.py` after workflow changes, and
  `claude plugin validate --strict .` after plugin-manifest or skill changes.
- Do not alter release configuration, remote settings, tags, or releases unless
  the maintainer explicitly requests that action.

## Pull requests

Before `gh pr create` or `gh pr edit`, run
`python scripts/pr_template_preflight.py --title "<title>"`, then use the
selected checked-in template with `--body-file`. For a focused template without
a mandatory title mapping, pass `--template <id>`. Record validation evidence
and update a focused regression test when behavior changes.
