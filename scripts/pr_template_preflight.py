#!/usr/bin/env python3
"""Run the distributable pull-request-template preflight from this repository."""

from __future__ import annotations

import runpy
from pathlib import Path


SKILL_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "repo-scaffold"
    / "scripts"
    / "pr_template_preflight.py"
)


if __name__ == "__main__":
    runpy.run_path(str(SKILL_SCRIPT), run_name="__main__")
