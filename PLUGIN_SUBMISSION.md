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
- Support: <https://github.com/MinhThang1009/repo-scaffold-plugin/blob/main/SUPPORT.md>
- Privacy policy: <https://github.com/MinhThang1009/repo-scaffold-plugin/blob/main/PRIVACY.md>
- Terms of use: <https://github.com/MinhThang1009/repo-scaffold-plugin/blob/main/TERMS.md>

Long description:

> Repo Scaffold creates project-specific GitHub community files, maintenance
> configuration, and validated workflows. It supports English or Vietnamese
> project-facing content, preserves existing work, pins third-party Actions to
> reviewed commit SHAs, and confirms remote repository changes before applying
> them.

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

## Portal-only prerequisites

Before submission, the publisher must:

- verify the publisher identity and account required by the portal;
- upload a production logo that meets the current portal requirements;
- select the intended supported countries or regions;
- confirm that every listing URL is reachable from the default branch;
- run and record the positive and negative tests against the submitted build;
  and
- review the final listing, permissions, privacy disclosures, and release notes
  in the portal before submission.
