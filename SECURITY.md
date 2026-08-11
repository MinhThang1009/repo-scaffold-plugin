# Security Policy

## Supported versions

Security fixes are provided for the latest released plugin version and the
current default-branch version. Older versions are not supported; reproduce the
issue against the latest version before reporting it when practical.

## Report a vulnerability

Do not open a public issue or discussion for a suspected vulnerability. Do not
include exploit details, tokens, credentials, private repository content, or
other sensitive information in public logs.

Use the first available private channel:

1. Use [GitHub Private Vulnerability Reporting][private-report] for this
   repository.
2. For a locally shared copy, contact the person or workspace that supplied the
   plugin through the same private channel used to share it.
3. If neither channel is available, request a private contact method without
   disclosing vulnerability details publicly.

Include the affected plugin version, entry point, prerequisites, reproduction
steps, impact, and any proposed mitigation. The maintainer will acknowledge the
report as soon as practical and provide updates when material progress is made.

## Scope

Security reports may cover the plugin manifest, skill instructions, templates,
GitHub Actions workflows, repository-configuration commands, the CodeQL
preflight helper, installation lifecycle, path handling, permissions, secret
handling, and external integrations.

Reports about GitHub, Codex, GitHub CLI, Python, PyYAML, or another dependency
that do not arise from this plugin should be reported to the relevant upstream
project.

[private-report]: https://github.com/MinhThang1009/repo-scaffold-plugin/security/advisories/new
