# Changelog

All notable changes to `repo-scaffold` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.1.0+codex.20260731120453](https://github.com/MinhThang1009/repo-scaffold-plugin/compare/v1.0.0+codex.20260731120453...v1.1.0+codex.20260731120453) (2026-07-31)


### Features

* **scaffold:** hoàn thiện bộ khung repository ([#1](https://github.com/MinhThang1009/repo-scaffold-plugin/issues/1)) ([60e6e7f](https://github.com/MinhThang1009/repo-scaffold-plugin/commit/60e6e7f83a5cf8a4117af1fb07fc364df9881f56))
* thêm plugin repo-scaffold ([a533b69](https://github.com/MinhThang1009/repo-scaffold-plugin/commit/a533b6968e89621bf1e6f5e0b11164bb13cf2e65))

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
