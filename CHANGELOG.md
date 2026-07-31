# Changelog

All notable changes to `repo-scaffold` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.1.1](https://github.com/MinhThang1009/repo-scaffold-plugin/compare/v1.1.0...v1.1.1) (2026-07-31)


### Bug Fixes

* **security:** ngăn ReDoS trong preflight ([#7](https://github.com/MinhThang1009/repo-scaffold-plugin/issues/7)) ([bebaa0c](https://github.com/MinhThang1009/repo-scaffold-plugin/commit/bebaa0c10166023487d7c7da5ab88b7f7c57e91d))

## [1.1.0](https://github.com/MinhThang1009/repo-scaffold-plugin/releases/tag/v1.1.0) (2026-07-31)


### Features

* **scaffold:** hoàn thiện bộ khung repository ([#1](https://github.com/MinhThang1009/repo-scaffold-plugin/issues/1)) ([60e6e7f](https://github.com/MinhThang1009/repo-scaffold-plugin/commit/60e6e7f83a5cf8a4117af1fb07fc364df9881f56))
* thêm plugin repo-scaffold ([a533b69](https://github.com/MinhThang1009/repo-scaffold-plugin/commit/a533b6968e89621bf1e6f5e0b11164bb13cf2e65))

### Changed

- Normalized the public version to clean SemVer and added an explicit,
  tag-and-commit-verified manual release path.

## [Unreleased]

### Added

- Cross-platform CI, dependency review, and Dependabot configuration.
- Commitlint, pull-request labeling, and stale issue/PR automation.
- Repository-managed CodeQL advanced setup for Python, with a reusable
  stack-rendered workflow asset.
- Release Please configuration for automated versioning and releases.
- ShellCheck-enforced workflow linting and deterministic repository contract checks.
- Tag-driven GitHub Release automation for versioned plugin archives.
- Repository ownership, editor, ignore, and community-documentation hardening.

### Changed

- Updated issue-form validation for GitHub's current schema, including upload
  inputs and documented `.yml` recognition requirements.
- Refreshed bundled GitHub Actions to verified current releases with immutable
  commit pins.
- Replaced the repository's manual tag dispatcher with Release Please while
  preserving the isolated reusable release engine.
