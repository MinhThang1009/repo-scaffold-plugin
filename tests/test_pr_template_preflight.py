from __future__ import annotations

import importlib.util
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "pr_template_preflight.py"
SPEC = importlib.util.spec_from_file_location("scripts.pr_template_preflight", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load pr_template_preflight.py")
pr_template_preflight = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pr_template_preflight
SPEC.loader.exec_module(pr_template_preflight)


class PullRequestTemplatePreflightTests(unittest.TestCase):
    def write_templates(self, root: Path) -> None:
        default = root / ".github" / "PULL_REQUEST_TEMPLATE.md"
        default.parent.mkdir(parents=True)
        default.write_text("<!-- repo-scaffold:pr-template=default -->\n", encoding="utf-8")
        for template in ("feature", "bugfix", "documentation"):
            path = root / ".github" / "PULL_REQUEST_TEMPLATE" / f"{template}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"<!-- repo-scaffold:pr-template={template} -->\n", encoding="utf-8"
            )

    def test_selects_the_template_required_by_the_title_type(self) -> None:
        cases = {
            "feat: add preflight": "feature",
            "fix(mutation)!: preserve state": "bugfix",
            "docs(readme): clarify setup": "documentation",
            "chore: update metadata": "default",
        }

        for title, expected in cases.items():
            with self.subTest(title=title):
                self.assertEqual(pr_template_preflight.select_template(title), expected)

    def test_main_reports_the_template_and_safe_gh_body_file_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_templates(root)
            output = StringIO()

            with redirect_stdout(output):
                result = pr_template_preflight.main(
                    ["--title", "fix: copy metadata", "--repository-root", str(root)]
                )

        self.assertEqual(result, 0)
        self.assertIn(
            "Selected PR template: .github/PULL_REQUEST_TEMPLATE/bugfix.md",
            output.getvalue(),
        )
        self.assertIn("gh pr create --body-file", output.getvalue())

    def test_main_fails_when_the_required_template_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            errors = StringIO()
            with redirect_stderr(errors):
                result = pr_template_preflight.main(
                    ["--title", "fix: copy metadata", "--repository-root", directory]
                )

        self.assertEqual(result, 1)
        self.assertIn("trusted PR template is missing", errors.getvalue())

    def test_script_entrypoint_returns_main_status(self) -> None:
        output = StringIO()
        with (
            redirect_stdout(output),
            self.assertRaises(SystemExit) as raised,
        ):
            runpy.run_path(
                str(SCRIPT_PATH),
                run_name="__main__",
                init_globals={"__name__": "__main__"},
            )

        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
