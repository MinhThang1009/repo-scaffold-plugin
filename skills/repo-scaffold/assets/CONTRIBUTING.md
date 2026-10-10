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
After preparing the UTF-8 body file, rerun the preflight with `--body-file <path>`
to reject hard-wrapped prose before the GitHub mutation.

When PR-body sync is installed, include explicit `Why:`, `Root cause:`,
`Changes:`, and `Verification:` fields in commit bodies. Values may be
paragraphs or bullet lists. Describe all material changes, including later
fixes and reverts. For a long PR, a reviewed `PR-summary-base:` commit can
consolidate the complete diff at the current base SHA; later commits stay in
scope. Keep validation notes tied to their source revision, and review the
generated prose against the full diff before requesting review. The cumulative
summary must cover primary behavior and safety-boundary changes, not only
PR-body tooling or documentation. The renderer fails instead of silently
dropping topics to shorten the body.

Keep each commit-body prose paragraph and bullet item on one physical line;
do not hard-wrap them at 72 or 80 characters. Separate paragraphs and fields
with blank lines and preserve intentional code-block newlines. This convention
overrides generic commit-body wrapping advice. The renderer still accepts
wrapped legacy messages without rewriting their commits.

## Code expectations

- Follow the existing conventions of the codebase.
- Run the linter and tests before opening a PR.
- Keep each PR focused on one purpose; smaller PRs are easier to review.

## Reporting issues

{{REPO_SCAFFOLD_ISSUE_REPORTING_GUIDANCE}}

{{REPO_SCAFFOLD_CODE_OF_CONDUCT_SECTION}}
