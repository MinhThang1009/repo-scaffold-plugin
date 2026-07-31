# Changelog

All notable changes to `repo-scaffold` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
