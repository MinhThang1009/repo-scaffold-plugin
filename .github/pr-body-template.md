<!-- repo-scaffold:pr-template=bugfix -->
<!-- repo-scaffold:pr-body-managed -->
<!-- This body is read by legacy template-based body-sync; the API-evidence body-sync does not read it. -->

## Purpose

Close verified gaps across repository guardrails, mutation caches, development tooling, and end-to-end pull-request evidence while preserving supported host and distribution contracts.

## Root cause

Reminder checks could treat indeterminate results as actionable. Body synchronization rejected the valid 3,000-file limit, omitted full source revision attribution, and read remote evidence before enforcing byte caps. A direct Coverage.py development pin also lagged the current upstream release.

## Key changes

- Fail closed before reminder Issue mutations and preserve the mutation worker's environment and exact shard assignments.
- Bind reusable mutation state and verdicts to source, tests, workflow, dependencies, runtime, platform, and complete non-overlapping shard evidence; write shard plans atomically.
- Bound API pages, combined evidence, cache metadata, and workflow inputs before decoding; accept the 3,000-file ceiling, attribute PR claims to full source SHAs, preserve human sections, verify remote body state, and fail closed on incomplete evidence or untranslated Vietnamese text.
- Synchronize Codex and Claude package assets, English/Vietnamese templates, documentation, validators, and the legacy manual body source.
- Update Coverage.py to 7.16.2 and regenerate both hashed lockfiles without changing other resolved package versions.

## Verification

- Full test suite: run `python -m pytest -q` and record the terminal result for the exact source revision.
- Coverage report: 100.00% statement and branch coverage.
- Repository, scaffold, workflow, Ruff, mypy, compileall, Markdownlint, pip check, and strict Claude plugin validation passed. `pip-compile 7.6.0` regenerated both lockfiles, and hash-locked development dependencies installed successfully.
- Freshness and official-documentation audits returned current; versioned maintenance inputs had no drift.
- English and Vietnamese isolated targets passed scaffold validation, body rendering, and both body preflights. Incomplete inventory failed before output, and existing README content remained unchanged.
- Codex and Claude installed the isolated package successfully. The published v1.10.14 archive digest and contents matched GitHub's release asset; full mutation-quality remains a separate scheduled/manual result and is not claimed.
- Manual rendering with `scripts/update_pr_body.py --template-file` binds `{{HEAD_SHA}}` and `{{HEAD_REPOSITORY}}`. The legacy template-based workflow may consume this file; the API-evidence body-sync added by this change will not.

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
