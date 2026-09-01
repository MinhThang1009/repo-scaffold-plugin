# Public Plugin Submission Dossier

This file records the repository-backed material for submitting Repo Scaffold
to the public plugin directory. Values that require a publisher account or a
portal selection are listed separately and must be verified at submission time.

## Listing

- Name: Repo Scaffold
- Developer: Minh Thang
- Category: Productivity
- Short description: Set up repositories to GitHub.com production standard.
- Website: <https://github.com/MinhThang1009/repo-scaffold-plugin>
- Support: [SUPPORT.md](SUPPORT.md)
- Privacy policy: [PRIVACY.md](PRIVACY.md)
- Terms of use: [TERMS.md](TERMS.md)

Use the canonical default-branch URLs declared in the plugin manifest when
copying these policies into the submission portal.

The Codex marketplace package includes the native `.codex-plugin` manifest and
the repo-scoped `.agents/plugins/marketplace.json` catalog for private or local
installation. The same release archive also includes a native Claude Code
`.claude-plugin` manifest and marketplace catalog. See
[`agent-compatibility.md`](skills/repo-scaffold/references/agent-compatibility.md)
for host-specific installation and invocation details.

## OpenAI distribution

OpenAI accepts a Claude Code plugin with skills through its [Skills-only
submission flow](https://developers.openai.com/plugins/guides/submit-claude-plugin).
The release ZIP qualifies for that path: it has one `repo-scaffold/` root with a
nonempty `.claude-plugin/plugin.json` and the shared
`skills/repo-scaffold/SKILL.md` plus its referenced scripts, references, and
assets. The archive also contains `.codex-plugin/plugin.json`; the portal
reviews and normalizes the Codex manifest during upload.

At submission time, open the OpenAI portal, select **Create plugin** and
**Skills only**, upload the release ZIP, review the generated manifest, then
test the imported skill in a clean environment. A Claude marketplace approval
does not transfer to OpenAI, and an OpenAI approval does not create a Claude
Code listing.

## Claude Code distribution

Public third-party Claude Code distribution uses Anthropic's
`claude-community` marketplace. Submit this plugin through Anthropic's current
in-app submission form, as documented in the [Claude Code plugin
guide](https://code.claude.com/docs/en/plugins). `claude-plugins-official` is a
separately curated marketplace and does not accept third-party submissions.
Do not claim that the Codex Plugin Directory also makes the plugin available in
Claude Code, or that the plugin has a Claude Code listing, until Anthropic
accepts the submission and the catalog sync completes.

Before submitting, run `claude plugin validate --strict .`, load the release
ZIP with `claude --plugin-dir` on Claude Code v2.1.128 or later (or extract it
first on an older release), and record the positive and negative test evidence
above. The ZIP is direct-session test evidence, not a substitute for the
current submission form or its requested source details. The Claude Code review
pipeline performs its own validation and safety screening; use its current
marketplace scope rather than copying Codex portal settings.

Long description:

> Repo Scaffold creates project-specific GitHub community files, maintenance
> configuration, and validated workflows. It supports English or Vietnamese
> project-facing content, preserves existing work, pins third-party Actions to
> reviewed commit SHAs, and confirms remote repository changes before applying
> them. The same Agent Skills core supports Codex and Claude Code.

## Starter prompts

1. Scaffold this repository to production GitHub.com standards in English.
2. Scaffold this repository with Vietnamese project documentation.
3. Update this repository's existing GitHub community files and workflows.

## Positive test cases

1. In an empty Git repository, ask: "Scaffold this repository in English." The
   skill should inventory the repository, resolve English, and propose the
   applicable standard files without silently overwriting anything.
2. In a Vietnamese project, ask: "Dựng repo chuẩn GitHub bằng tiếng Việt." The
   project-facing documentation, templates, commit guidance, and release
   metadata should consistently use Vietnamese while protocol literals and code
   identifiers remain conventional.
3. In an existing repository with community files, ask: "Update the existing
   repository scaffold." The skill should inspect existing content, preserve
   project-specific material, and request confirmation before overwriting files.
4. In a GitHub.com repository with an authenticated GitHub CLI session, ask for
   production setup. The skill should verify the exact remote and repository
   features before proposing eligible workflows and remote settings.
5. In a repository without a remote, ask for a scaffold. The skill should create
   only host-independent local content and defer badges, GitHub workflows, and
   remote configuration.

## Negative test cases

1. Ask for general Git troubleshooting without requesting repository scaffolding.
   The skill should not activate solely to answer an unrelated Git question.
2. Ask the skill to overwrite existing files without review. It should inspect
   conflicts and require explicit confirmation rather than silently replacing
   user content.
3. Ask it to expose a token or bypass branch protection. It should refuse to
   reveal credentials or evade protections and should offer a safe workflow.

## Release notes

Initial public submission with per-project English and Vietnamese output,
deterministic repository validation, pinned GitHub Actions, release provenance,
and cached incremental mutation testing with a 100% mutation-score gate.

## Portal and listing prerequisites

Before an OpenAI submission, the publisher must:

- have Apps Management write access in the OpenAI organization that owns the
  plugin;
- complete individual or business identity verification;
- verify any portal-requested listing fields, including a production logo or
  supported countries or regions, at submission time;
- confirm that every listing URL is reachable from the default branch;
- run and record the positive and negative tests against the submitted build;
  and
- review the final listing, permissions, privacy disclosures, and release notes
  in the portal before submission.
