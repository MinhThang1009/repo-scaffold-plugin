# Changelog

All notable changes to `repo-scaffold` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.2.2](https://github.com/MinhThang1009/repo-scaffold-plugin/compare/v1.2.1...v1.2.2) (2026-08-12)


### Sửa lỗi

* **mutation:** cô lập selector subprocess ([#15](https://github.com/MinhThang1009/repo-scaffold-plugin/issues/15)) ([79a869c](https://github.com/MinhThang1009/repo-scaffold-plugin/commit/79a869c57f7482996f114a0c5fdd6a5ef20f95ef))
* **mutation:** hiệu chỉnh quality gate ([#17](https://github.com/MinhThang1009/repo-scaffold-plugin/issues/17)) ([cc2ad49](https://github.com/MinhThang1009/repo-scaffold-plugin/commit/cc2ad497b7cd660b7d8dcd255acc7cadbc7bc46d))

## [1.2.1](https://github.com/MinhThang1009/repo-scaffold-plugin/compare/v1.2.0...v1.2.1) (2026-08-11)


### Sửa lỗi

* **release:** việt hóa Release Please ([#14](https://github.com/MinhThang1009/repo-scaffold-plugin/issues/14)) ([50a0003](https://github.com/MinhThang1009/repo-scaffold-plugin/commit/50a00031fb8bee6ad051d1a1826ac075b071454a))
* **scorecard:** quét đủ workflow nguồn ([#11](https://github.com/MinhThang1009/repo-scaffold-plugin/issues/11)) ([dcd66a2](https://github.com/MinhThang1009/repo-scaffold-plugin/commit/dcd66a2c74919ff1fd3019a6b383be442e3a70f3))

## [1.2.0](https://github.com/MinhThang1009/repo-scaffold-plugin/compare/v1.1.1...v1.2.0) (2026-08-11)


### Features

* **scaffold:** thực thi chuẩn tài liệu và release ([#9](https://github.com/MinhThang1009/repo-scaffold-plugin/issues/9)) ([17f9db1](https://github.com/MinhThang1009/repo-scaffold-plugin/commit/17f9db178ee879cc050365f094d36b6282105afa))


### Bug Fixes

* **links:** xử lý tag release chưa tạo ([#12](https://github.com/MinhThang1009/repo-scaffold-plugin/issues/12)) ([cb5f29a](https://github.com/MinhThang1009/repo-scaffold-plugin/commit/cb5f29a8c53325acdd2f80630f552f46c5a40ec4))

## [1.1.1](https://github.com/MinhThang1009/repo-scaffold-plugin/compare/v1.1.0...v1.1.1) (2026-07-31)


### Bug Fixes

* **security:** ngăn ReDoS trong preflight ([#7](https://github.com/MinhThang1009/repo-scaffold-plugin/issues/7)) ([bebaa0c](https://github.com/MinhThang1009/repo-scaffold-plugin/commit/bebaa0c10166023487d7c7da5ab88b7f7c57e91d))

## [1.1.0](https://github.com/MinhThang1009/repo-scaffold-plugin/releases/tag/v1.1.0) (2026-07-31)


### Features

* **scaffold:** hoàn thiện bộ khung repository ([#1](https://github.com/MinhThang1009/repo-scaffold-plugin/issues/1)) ([60e6e7f](https://github.com/MinhThang1009/repo-scaffold-plugin/commit/60e6e7f83a5cf8a4117af1fb07fc364df9881f56))
* thêm plugin repo-scaffold ([a533b69](https://github.com/MinhThang1009/repo-scaffold-plugin/commit/a533b6968e89621bf1e6f5e0b11164bb13cf2e65))

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
* Updated issue-form validation for GitHub's current schema, including upload
  inputs and documented `.yml` recognition requirements.
* Refreshed bundled GitHub Actions to verified current releases with immutable
  commit pins.
* Extended repository validation to enforce the release attestation boundary,
  caller permissions, artifact scope, and publish gate.
* Replaced the repository's manual tag dispatcher with Release Please while
  preserving the isolated reusable release engine.
