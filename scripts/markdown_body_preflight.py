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


if __name__ == "__main__":
    runpy.run_path(str(SKILL_SCRIPT), run_name="__main__")
