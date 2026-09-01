# Agent Compatibility

Repo Scaffold has one [Agent Skills](https://agentskills.io) core at
`skills/repo-scaffold/SKILL.md`. The core contains the scaffold workflow,
English/Vietnamese project-output policy, assets, and validation scripts. Do
not copy it into agent-specific adapters.

## Codex

The Codex adapter is `.codex-plugin/plugin.json`. The repository ships the
Codex marketplace catalog at `.agents/plugins/marketplace.json`, so install it
through the documented marketplace flow:

```bash
codex plugin marketplace add MinhThang1009/repo-scaffold-plugin
codex plugin add repo-scaffold@repo-scaffold-plugins
```

Restart Codex, then ask for a repository scaffold normally. The catalog's
`source.path` resolves from the marketplace root and targets this plugin root.
For a checkout private to one user, use a personal marketplace at
`~/.agents/plugins/marketplace.json`. See the [official Codex plugin documentation](https://developers.openai.com/plugins/build/plugins).

For an OpenAI public listing, submit the release ZIP through the [Skills-only
plugin flow](https://developers.openai.com/plugins/guides/submit-claude-plugin).
Its single `repo-scaffold/` directory includes a nonempty Claude manifest and
the shared skill with all referenced files. The OpenAI portal normalizes the
Codex manifest during review; a Claude Code marketplace listing remains a
separate approval.

Codex reads project instructions from `AGENTS.md`; it layers the applicable
files from the repository root to the working directory. The generated
`AGENTS.md` is the language-selected root instruction entry point created by
the scaffold; Codex can layer applicable global or nested instruction files
around it. See the [official AGENTS.md documentation](https://learn.chatgpt.com/docs/agent-configuration/agents-md).

## Claude Code

The Claude Code adapter is `.claude-plugin/plugin.json`. Claude Code discovers
the standard `skills/` directory in a plugin, so it loads the same core skill
without a copied wrapper. The repository also ships a Claude Code marketplace
catalog at `.claude-plugin/marketplace.json`, so users can install the release
through the documented marketplace flow:

```bash
claude plugin validate --strict .
claude plugin marketplace add MinhThang1009/repo-scaffold-plugin
claude plugin install repo-scaffold@repo-scaffold-plugins
```

The public third-party Anthropic marketplace is `claude-community`; use its
in-app submission form for a public listing. `claude-plugins-official` is a
separately curated marketplace. This repository's `repo-scaffold-plugins`
marketplace remains a separate local or private distribution source.

Restart Claude Code, then invoke `/repo-scaffold:repo-scaffold` or ask Claude
to scaffold the repository. For local verification without installation, run
`claude --plugin-dir .`. Claude Code documents the marketplace contract, plugin
layout, and shared `SKILL.md` format in its [marketplace guide](https://code.claude.com/docs/en/plugin-marketplaces), [plugin guide](https://code.claude.com/docs/en/plugins), and [skills guide](https://code.claude.com/docs/en/skills).

Release assets contain both manifests under `repo-scaffold/`. Claude Code
v2.1.128 or later can pass the ZIP directly to `claude --plugin-dir` for an
ephemeral local check. On an older Claude Code release, extract the archive and
add the extracted directory as a local marketplace.

## Other agents

Use the core directly only in an agent that supports the Agent Skills standard.
Point that agent's skill discovery at `skills/`, or import
`skills/repo-scaffold/SKILL.md` through the agent's documented mechanism. This
repository deliberately does not claim native installation support for an
unverified agent or invent its configuration format.

Host instructions remain authoritative. Codex can use `AGENTS.md`; Claude Code
reads `CLAUDE.md`, not `AGENTS.md` directly. For a target repository that needs
shared instructions, use a `CLAUDE.md` containing `@AGENTS.md`. The scaffold
ships that exact adapter as `assets/CLAUDE.md`. See the [Claude Code memory documentation](https://code.claude.com/docs/en/memory).

## Multi-agent behavior

Repo Scaffold does not ship custom subagent definitions. Its one shared skill
is safe to invoke from a host's primary agent or from a host-managed subagent;
the host owns delegation, concurrency, model choice, and permissions.

- In Codex, a target repository may opt into project-scoped custom agents in
  `.codex/agents/<name>.toml`. Each definition needs `name`, `description`, and
  `developer_instructions`; add one only for a real, narrow role. See the
  [official Codex subagents documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents).
- In Claude Code, a target repository may opt into project subagents in
  `.claude/agents/<name>.md`. A distributable Claude Code plugin uses its
  top-level `agents/` directory only when it actually ships a specialized
  agent. See the [official Claude Code subagents documentation](https://code.claude.com/docs/en/sub-agents).

When a target repository adds custom agents, those agents must use the same
generated `AGENTS.md` and `CLAUDE.md` instruction contract. Do not duplicate
the scaffold workflow or create agent-specific language variants.

## Language policy

Agent-facing compatibility documentation is available in English and Vietnamese.
For every target repository, resolve exactly one project-output language,
`en` or `vi`; this affects generated project-facing files, not the selected
agent adapter.
