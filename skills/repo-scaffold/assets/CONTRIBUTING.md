# Contributing to {{REPO_SCAFFOLD_PROJECT_NAME}}

Thanks for your interest in contributing! This document describes the workflow.

## Workflow (GitHub Flow)

1. Create a branch off **{{REPO_SCAFFOLD_DEFAULT_BRANCH}}**: `git checkout -b feat/<short-description>`.
2. Commit using [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/): `feat(scope): description`.
3. Push the branch and open a Pull Request against **{{REPO_SCAFFOLD_DEFAULT_BRANCH}}**.
{{REPO_SCAFFOLD_CONTRIBUTION_REVIEW_STEP}}

Before creating or editing a pull request, run
`python scripts/pr_template_preflight.py --title "<title>"`. For a focused
security, deployment, or dependency-update review whose title has no mandatory
mapping, add `--template security`, `--template deployment`, or
`--template dependency-update`.

## Code expectations

- Follow the existing conventions of the codebase.
- Run the linter and tests before opening a PR.
- Keep each PR focused on one purpose; smaller PRs are easier to review.

## Reporting issues

{{REPO_SCAFFOLD_ISSUE_REPORTING_GUIDANCE}}

{{REPO_SCAFFOLD_CODE_OF_CONDUCT_SECTION}}
