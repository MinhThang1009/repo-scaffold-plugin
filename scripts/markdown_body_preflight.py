#!/usr/bin/env python3
"""Run the bundled Markdown body preflight from the repository root."""

from __future__ import annotations

import runpy
from pathlib import Path


SKILL_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "repo-scaffold"
    / "scripts"
    / "markdown_body_preflight.py"
)


def visible_body_heading_lines(markdown: str) -> set[int]:
    """Use the bundled Markdown parser when imported by repository tooling."""
    module = runpy.run_path(
        str(SKILL_SCRIPT),
        run_name="skills.repo-scaffold.scripts.markdown_body_preflight",
    )
    headings: set[int] = module["visible_body_heading_lines"](markdown)
    return headings


if __name__ == "__main__":
    runpy.run_path(str(SKILL_SCRIPT), run_name="__main__")
else:
    # Source tooling may import this wrapper before a bundled preflight imports
    # the same module name. Expose the complete public parser API in either order.
    _shared = runpy.run_path(
        str(SKILL_SCRIPT),
        run_name="skills.repo-scaffold.scripts.markdown_body_preflight",
    )
    globals().update(
        {
            name: value
            for name, value in _shared.items()
            if not name.startswith("_") and name != "visible_body_heading_lines"
        }
    )
