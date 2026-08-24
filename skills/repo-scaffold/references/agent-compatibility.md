# Agent Compatibility

Repo Scaffold has one [Agent Skills](https://agentskills.io) core at
`skills/repo-scaffold/SKILL.md`. The core contains the scaffold workflow,
English/Vietnamese project-output policy, assets, and validation scripts. Do
not copy it into agent-specific adapters.

## Codex

The Codex adapter is `.codex-plugin/plugin.json`. Install it through a Codex
marketplace as described in the [official Codex plugin documentation](https://developers.openai.com/plugins/build/plugins), then ask for a repository scaffold normally.

Codex reads project instructions from `AGENTS.md`; it layers the applicable
files from the repository root to the working directory. The generated
`AGENTS.md` is the language-selected root instruction entry point created by
the scaffold; Codex can layer applicable global or nested instruction files
around it. See the [official AGENTS.md documentation](https://learn.chatgpt.com/docs/agent-configuration/agents-md).

## Claude Code

The Claude Code adapter is `.claude-plugin/plugin.json`. Claude Code discovers
the standard `skills/` directory in a plugin, so it loads the same core skill
without a copied wrapper. For local verification from this repository root:

```bash
claude plugin validate --strict .
claude --plugin-dir .
```

In the resulting session, invoke `/repo-scaffold:repo-scaffold` or ask Claude
to scaffold the repository. Claude Code documents both the plugin layout and
the shared `SKILL.md` format in its [plugin guide](https://code.claude.com/docs/en/plugins)
and [skills guide](https://code.claude.com/docs/en/skills).

Release assets contain both manifests under `repo-scaffold/`. Pass the ZIP
directly to `claude --plugin-dir`, or extract it and pass the extracted
directory.

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
