# Discovery and path safety

Read this reference during the survey phase.

Resolve an absolute repository root with `git rev-parse --show-toplevel` inside
a work tree, or use the user-confirmed absolute target directory outside one.
Anchor every path to that root. Before reading, traversing, creating, or writing
a path, normalize it, require it to remain a boundary-safe descendant, and
reject every symbolic link, junction, mount point, or reparse-point component.
Repeat the component check immediately before opening a newly created path.
Never follow a repository-controlled link unless the user explicitly confirms
the resolved external target and expands the filesystem scope.

Build the overwrite inventory from tracked, untracked, and ignored files, then
perform a literal existence check immediately before each write. Build manifest
discovery separately and exclude dependency/build trees such as `node_modules`,
`.venv`, `vendor`, `dist`, `build`, and `target` unless the user confirms a
first-party workspace.

For GitHub.com, parse every fetch remote as data and collect distinct
`OWNER/REPO` candidates. When there is more than one, show the mapping and ask;
never prefer `origin`, `upstream`, `GH_REPO`, or GitHub CLI's default. Pass the
selected `github.com/OWNER/REPO` and `--hostname github.com` explicitly to
every GitHub CLI call, and require returned `nameWithOwner` to match it. A fork
requires this confirmation because GitHub CLI can default to its parent.

Inspect supported community-health locations in GitHub precedence order:
`.github/`, root, then `docs/`. Preserve the selected active path and do not
create a higher-precedence duplicate. Inspect effective inherited policy for a
non-fork repository before proposing a local override. A public owner `.github`
repository can provide defaults; inaccessible lookup is indeterminate, not proof
that no policy exists. Adding a local issue-template file disables an inherited
issue-template directory as a set.
