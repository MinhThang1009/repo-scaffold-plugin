"""Regression tests for focused pull-request template documentation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts import validate_repository


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


class PullRequestTemplateCatalogDocumentationTests(unittest.TestCase):
    def test_current_readme_documents_every_focused_template(self) -> None:
        self.assertEqual(
            validate_repository.validate_pr_template_catalog_documentation(PLUGIN_ROOT),
            [],
        )

    def test_missing_focused_template_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template_directory = root / ".github" / "PULL_REQUEST_TEMPLATE"
            template_directory.mkdir(parents=True)
            (root / "README.md").write_text("`feature.md`\n", encoding="utf-8")
            (template_directory / "feature.md").write_text(
                "# Feature\n", encoding="utf-8"
            )
            (template_directory / "deployment.md").write_text(
                "# Deployment\n", encoding="utf-8"
            )

            self.assertEqual(
                validate_repository.validate_pr_template_catalog_documentation(root),
                ["README.md: must document focused PR template `deployment.md`"],
            )

    def test_unreadable_readme_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").mkdir()

            problems = validate_repository.validate_pr_template_catalog_documentation(
                root
            )

            self.assertEqual(len(problems), 1)
            self.assertIn("README PR-template catalog is unreadable", problems[0])

    def test_empty_focused_template_directory_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("# Project\n", encoding="utf-8")
            (root / ".github" / "PULL_REQUEST_TEMPLATE").mkdir(parents=True)

            self.assertEqual(
                validate_repository.validate_pr_template_catalog_documentation(root),
                ["README PR-template catalog has no focused templates to document"],
            )
