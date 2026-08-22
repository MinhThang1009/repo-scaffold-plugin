# Agent Compatibility

Repo Scaffold has one [Agent Skills](https://agentskills.io) core at
`skills/repo-scaffold/SKILL.md`. The core contains the scaffold workflow,
English/Vietnamese project-output policy, assets, and validation scripts. Do
not copy it into agent-specific adapters.

## Codex

The Codex adapter is `.codex-plugin/plugin.json`. Install it through a Codex
marketplace as described in the [official Codex plugin documentation](https://developers.openai.com/plugins/build/plugins), then ask for a repository scaffold normally.

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

Release assets contain both manifests under `repo-scaffold/`. Extract the
archive, then pass that extracted directory to `claude --plugin-dir`.

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

## Language policy

Agent-facing compatibility documentation is available in English and Vietnamese.
For every target repository, resolve exactly one project-output language,
`en` or `vi`; this affects generated project-facing files, not the selected
agent adapter.
