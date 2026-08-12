# Changelog

All notable changes to `repo-scaffold` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.2.2](https://github.com/MinhThang1009/repo-scaffold-plugin/compare/v1.2.1...v1.2.2) (2026-08-12)


### Bug Fixes

* **mutation:** isolate the selector from subprocesses ([#15](https://github.com/MinhThang1009/repo-scaffold-plugin/issues/15)) ([0b97318](https://github.com/MinhThang1009/repo-scaffold-plugin/commit/0b973188159538f74234903fd153a753a143d5d8))
* **mutation:** calibrate the quality gate ([#17](https://github.com/MinhThang1009/repo-scaffold-plugin/issues/17)) ([fd1aa0e](https://github.com/MinhThang1009/repo-scaffold-plugin/commit/fd1aa0e307a9b25e36ecc76e22acd993c77089c2))

## [1.2.1](https://github.com/MinhThang1009/repo-scaffold-plugin/compare/v1.2.0...v1.2.1) (2026-08-11)


### Bug Fixes

* **release:** localize Release Please into Vietnamese ([#14](https://github.com/MinhThang1009/repo-scaffold-plugin/issues/14)) ([29abc4c](https://github.com/MinhThang1009/repo-scaffold-plugin/commit/29abc4c6673c6f5cb7beb7e338da80ccd9f39559))
* **scorecard:** scan all source workflows ([#11](https://github.com/MinhThang1009/repo-scaffold-plugin/issues/11)) ([dc70515](https://github.com/MinhThang1009/repo-scaffold-plugin/commit/dc70515263a3e7c0151370787a878ba79252e196))

## [1.2.0](https://github.com/MinhThang1009/repo-scaffold-plugin/compare/v1.1.1...v1.2.0) (2026-08-11)


### Features

* **scaffold:** enforce documentation and release contracts ([#9](https://github.com/MinhThang1009/repo-scaffold-plugin/issues/9)) ([43a8127](https://github.com/MinhThang1009/repo-scaffold-plugin/commit/43a8127d8c33d9381a632bccf281cda152ef3cfc))


### Bug Fixes

* **links:** handle a release tag that has not been created ([#12](https://github.com/MinhThang1009/repo-scaffold-plugin/issues/12)) ([e05cac8](https://github.com/MinhThang1009/repo-scaffold-plugin/commit/e05cac8c746175a7870a2637681fff0fdd54eafe))

## [1.1.1](https://github.com/MinhThang1009/repo-scaffold-plugin/compare/v1.1.0...v1.1.1) (2026-07-31)


### Bug Fixes

* **security:** prevent ReDoS in preflight ([#7](https://github.com/MinhThang1009/repo-scaffold-plugin/issues/7)) ([3823caa](https://github.com/MinhThang1009/repo-scaffold-plugin/commit/3823caaa1dd8d9a952e4c758228631a38c40d0f5))

## [1.1.0](https://github.com/MinhThang1009/repo-scaffold-plugin/releases/tag/v1.1.0) (2026-07-31)


### Features

* **scaffold:** complete the production repository scaffold ([#1](https://github.com/MinhThang1009/repo-scaffold-plugin/issues/1)) ([8006353](https://github.com/MinhThang1009/repo-scaffold-plugin/commit/800635356fa2c2589a6a220d898df6bbb7395b9b))
* add the repo-scaffold plugin ([c6ed7a7](https://github.com/MinhThang1009/repo-scaffold-plugin/commit/c6ed7a7683f7474c94d5b14f9428f50913616325))

### Changed

* Normalized the public version to clean SemVer and added an explicit,
  tag-and-commit-verified manual release path.

## [Unreleased]

### Added

* Added pinned markdownlint and scheduled link-health Actions, plus a
  deterministic rendered-document validator for current and future scaffolds.
* Added signed SLSA build-provenance attestations for release assets, isolated
  from project builds with least-privilege reusable-workflow permissions.
* Cross-platform CI, dependency review, and Dependabot configuration.
* Commitlint, pull-request labeling, and stale issue/PR automation.
* Repository-managed CodeQL advanced setup for Python, with a reusable
  stack-rendered workflow asset.
* Release Please configuration for automated versioning and releases.
* ShellCheck-enforced workflow linting and deterministic repository contract checks.
* Tag-driven GitHub Release automation for versioned plugin archives.
* Repository ownership, editor, ignore, and community-documentation hardening.

### Changed

* Centralized rolling CI bootstrap settings and reviewed standalone-tool release
  pins in a machine-readable policy, with scheduled upstream drift detection.
* Centered the README header and badges, numbered its outline, and added a
  complete manual table of contents.
* Made documentation language detection and Markdown/template conventions
  explicit in the scaffold workflow.
* Added deterministic per-project English/Vietnamese language selection and
  applied the selected language consistently to community and release content.
* Updated issue-form validation for GitHub's current schema, including upload
  inputs and documented `.yml` recognition requirements.
* Refreshed bundled GitHub Actions to verified current releases with immutable
  commit pins.
* Extended repository validation to enforce the release attestation boundary,
  caller permissions, artifact scope, and publish gate.
* Replaced the repository's manual tag dispatcher with Release Please while
  preserving the isolated reusable release engine.
