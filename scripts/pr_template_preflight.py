#!/usr/bin/env python3
"""Run the distributable pull-request-template preflight from this repository."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


SKILL_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "repo-scaffold"
    / "scripts"
    / "pr_template_preflight.py"
)


def validate_body_structure(
    repository_root: Path,
    title: str,
    body: str,
    *,
    requested_template: str | None = None,
) -> None:
    """Delegate structure validation to the same bundled template contract."""
    module = runpy.run_path(
        str(SKILL_SCRIPT), run_name="skills.repo-scaffold.scripts.pr_template_preflight"
    )
    module["validate_body_structure"](
        repository_root, title, body, requested_template=requested_template
    )


if __name__ == "__main__":
    sys.path.insert(0, str(SKILL_SCRIPT.parent))
    runpy.run_path(str(SKILL_SCRIPT), run_name="__main__")
else:
    _shared = runpy.run_path(
        str(SKILL_SCRIPT), run_name="skills.repo-scaffold.scripts.pr_template_preflight"
    )
    globals().update(
        {
            name: value
            for name, value in _shared.items()
            if not name.startswith("_") and name != "validate_body_structure"
        }
    )
