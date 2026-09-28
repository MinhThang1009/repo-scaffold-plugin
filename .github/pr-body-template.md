<!-- repo-scaffold:pr-template=bugfix -->

## Purpose

Keep every mutation-capable preflight bound to current, complete evidence and make the review body reproducible from a checked-in source template.

## Root cause

Mutation planning and cache reuse could spend time on unnecessary execution and could reuse state without binding every material source, test, workflow, dependency, runtime, and platform input. The pull-request body also had no authoritative full-body source, so verification prose became stale after later commits.

## Key changes

- Generate the mutation plan without running the clean suite or executing mutations, while supporting the reviewed mutmut planning API.
- Bind reusable mutation state to source, test, workflow, dependency, branch, runtime, and platform inputs, and save the exact plan before shard execution.
- Reuse pending mutants by exact shard assignment and retain sparse shard metadata while the merger enforces complete, valid, deterministic verdicts.
- Keep source, generated workflow assets, validators, tests, and documentation synchronized for the mutation and pull-request contracts.
- Render the entire pull-request body from this reviewed source template, with the current head SHA and head repository bound to the exact pull request revision.

## Verification

- `python -m pytest -q`: 1272 passed, 4 skipped, 2837 subtests passed.
- Coverage-instrumented full suite: 1272 passed, 4 skipped; `coverage report --fail-under=100` reports 100.00% statement and branch coverage.
- `python scripts/validate_repository.py`, `python skills/repo-scaffold/scripts/validate_scaffold.py --repository-root . --template-root skills/repo-scaffold/assets`, and `python scripts/validate_workflows.py` passed.
- `python -m ruff format --check skills scripts tests`, `python -m ruff check skills scripts tests`, the reviewed mypy command, compileall, markdownlint, and `claude plugin validate --strict .` passed.
- The current pull-request body is rendered from this file at head `{{HEAD_SHA}}` in `{{HEAD_REPOSITORY}}`; mutation-quality remains a separate dispatched evidence path and is not claimed by this body.

## Required checklist

<!-- repo-scaffold:required-checklist:start -->
- [x] The failure mode or user impact is described above
- [x] The change addresses the confirmed cause or documented mitigation
- [x] Verification evidence or an automation rationale is recorded above
- [x] No secret, credential, private data, or unresolved scaffold marker is included
<!-- repo-scaffold:required-checklist:end -->

## If applicable

<!-- repo-scaffold:optional-checklist:start -->
- [x] A focused regression test covers the failure mode
- [x] Documentation, migration, rollback, release, or security effects were assessed
<!-- repo-scaffold:optional-checklist:end -->

## Related issue

Not applicable.
