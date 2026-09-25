from __future__ import annotations

import importlib.util
import runpy
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ROOT_ENTRYPOINT = PLUGIN_ROOT / "scripts" / "pr_template_preflight.py"
SCRIPT_PATH = (
    PLUGIN_ROOT / "skills" / "repo-scaffold" / "scripts" / "pr_template_preflight.py"
)
sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("pr_template_preflight", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load pr_template_preflight.py")
pr_template_preflight = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pr_template_preflight
SPEC.loader.exec_module(pr_template_preflight)


class PullRequestTemplatePreflightTests(unittest.TestCase):
    def write_templates(self, root: Path) -> None:
        default = root / ".github" / "PULL_REQUEST_TEMPLATE.md"
        default.parent.mkdir(parents=True)
        default.write_text(
            "<!-- repo-scaffold:pr-template=default -->\n", encoding="utf-8"
        )
        for template in (
            "feature",
            "bugfix",
            "documentation",
            "security",
            "deployment",
            "dependency-update",
        ):
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

    def test_template_with_utf8_bom_before_marker_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_templates(root)
            bugfix = root / ".github" / "PULL_REQUEST_TEMPLATE" / "bugfix.md"
            bugfix.write_bytes(
                "\ufeff<!-- repo-scaffold:pr-template=bugfix -->\n".encode("utf-8")
            )
            output = StringIO()

            with redirect_stdout(output):
                result = pr_template_preflight.main(
                    [
                        "--title",
                        "fix: accept a BOM template",
                        "--repository-root",
                        str(root),
                    ]
                )

        self.assertEqual(result, 0)
        self.assertIn(
            "Selected PR template: .github/PULL_REQUEST_TEMPLATE/bugfix.md",
            output.getvalue(),
        )

    def test_selects_an_explicit_focused_template_for_an_unmapped_title(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_templates(root)
            output = StringIO()

            with redirect_stdout(output):
                result = pr_template_preflight.main(
                    [
                        "--title",
                        "chore(deps): update dependency lockfile",
                        "--template",
                        "dependency-update",
                        "--repository-root",
                        str(root),
                    ]
                )

        self.assertEqual(result, 0)
        self.assertIn(
            "Selected PR template: .github/PULL_REQUEST_TEMPLATE/dependency-update.md",
            output.getvalue(),
        )

    def test_selects_focused_templates_with_supported_text_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_templates(root)
            catalog = root / ".github" / "PULL_REQUEST_TEMPLATE"
            canonical = catalog / "security.md"
            template_text = canonical.read_text(encoding="utf-8")
            canonical.unlink()

            for extension in (".TXT", ".markdown"):
                with self.subTest(extension=extension):
                    candidate = catalog / f"security{extension}"
                    candidate.write_text(template_text, encoding="utf-8")
                    output = StringIO()
                    with redirect_stdout(output):
                        result = pr_template_preflight.main(
                            [
                                "--title",
                                "chore: inspect focused template",
                                "--template",
                                "security",
                                "--repository-root",
                                str(root),
                            ]
                        )

                    self.assertEqual(result, 0)
                    self.assertIn(
                        f"Selected PR template: .github/PULL_REQUEST_TEMPLATE/security{extension}",
                        output.getvalue(),
                    )
                    candidate.unlink()

    def test_catalog_discovers_templates_in_all_github_supported_locations(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root_default = root / "PULL_REQUEST_TEMPLATE.TXT"
            root_default.write_text(
                "<!-- repo-scaffold:pr-template=default -->\n", encoding="utf-8"
            )
            docs_template = root / "docs" / "PULL_REQUEST_TEMPLATE" / "feature.markdown"
            docs_template.parent.mkdir(parents=True)
            docs_template.write_text(
                "<!-- repo-scaffold:pr-template=feature -->\n", encoding="utf-8"
            )
            github_template = (
                root / ".github" / "PULL_REQUEST_TEMPLATE" / "security.txt"
            )
            github_template.parent.mkdir(parents=True)
            github_template.write_text(
                "<!-- repo-scaffold:pr-template=security -->\n", encoding="utf-8"
            )

            catalog = pr_template_preflight.template_catalog(root)

            self.assertEqual(catalog["default"], root_default)
            self.assertEqual(catalog["feature"], docs_template)
            self.assertEqual(catalog["security"], github_template)
            self.assertEqual(
                pr_template_preflight.template_path(root, "feature"), docs_template
            )
            self.assertEqual(
                pr_template_preflight.template_path(root, "security"), github_template
            )

    def test_default_template_uses_local_location_search_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root_default = root / "PULL_REQUEST_TEMPLATE.md"
            root_default.write_text(
                "<!-- repo-scaffold:pr-template=default -->\n", encoding="utf-8"
            )
            docs_default = root / "docs" / "PULL_REQUEST_TEMPLATE.txt"
            docs_default.parent.mkdir()
            docs_default.write_text(
                "<!-- repo-scaffold:pr-template=default -->\n", encoding="utf-8"
            )
            github = root / ".github"
            github.mkdir()
            github_default = github / "PULL_REQUEST_TEMPLATE.txt"
            github_default.write_text(
                "<!-- repo-scaffold:pr-template=default -->\n", encoding="utf-8"
            )

            self.assertEqual(
                pr_template_preflight.template_catalog(root)["default"],
                github_default,
            )

            github_default.unlink()
            self.assertEqual(
                pr_template_preflight.template_catalog(root)["default"],
                root_default,
            )

            root_default.unlink()
            self.assertEqual(
                pr_template_preflight.template_catalog(root)["default"],
                docs_default,
            )

    def test_rejects_ambiguous_default_templates_in_precedence_location(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            github = root / ".github"
            github.mkdir()
            for extension in (".md", ".txt"):
                (github / f"PULL_REQUEST_TEMPLATE{extension}").write_text(
                    "<!-- repo-scaffold:pr-template=default -->\n", encoding="utf-8"
                )

            with self.assertRaisesRegex(ValueError, "ambiguous trusted PR default"):
                pr_template_preflight.template_catalog(root)

    def test_body_file_rejects_hard_wrapped_prose_but_not_markdown_structure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_templates(root)
            wrapped = root / "wrapped.md"
            wrapped.write_text(
                "This paragraph is incorrectly\nwrapped onto a second line.\n",
                encoding="utf-8",
            )
            errors = StringIO()
            with redirect_stderr(errors):
                rejected = pr_template_preflight.main(
                    [
                        "--title",
                        "fix: reject wrapped prose",
                        "--body-file",
                        str(wrapped),
                        "--repository-root",
                        str(root),
                    ]
                )

            structured = root / "structured.md"
            structured.write_text(
                "- A list item\n- Another list item.\n\n"
                "Paragraph before the table.\n"
                "| Name | Value |\n| --- | --- |\n| item | value |\n\n"
                "```text\nfirst\nsecond\n```\n",
                encoding="utf-8",
            )
            accepted = pr_template_preflight.main(
                [
                    "--title",
                    "fix: allow markdown structure",
                    "--body-file",
                    str(structured),
                    "--repository-root",
                    str(root),
                ]
            )

        self.assertEqual(rejected, 1)
        self.assertIn("contains hard-wrapped prose at line(s): 2", errors.getvalue())
        self.assertEqual(accepted, 0)

    def test_body_file_rejects_list_continuation_and_inline_comment_wraps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_templates(root)
            for body in (
                "- First list item\n  continuation\n",
                "First part <!-- inline note -->\ncontinuation\n",
                "First <!-- inline note --> visible\ncontinuation\n",
            ):
                body_file = root / "body.md"
                body_file.write_text(body, encoding="utf-8")
                errors = StringIO()
                with redirect_stderr(errors):
                    result = pr_template_preflight.main(
                        [
                            "--title",
                            "fix: reject wrapped structure",
                            "--body-file",
                            str(body_file),
                            "--repository-root",
                            str(root),
                        ]
                    )
                self.assertEqual(result, 1)
                self.assertIn("hard-wrapped prose at line(s): 2", errors.getvalue())

    def test_body_file_parser_ignores_comments_and_rejects_invalid_input(self) -> None:
        self.assertEqual(
            pr_template_preflight.hard_wrapped_prose_lines(
                "<!--\nhidden prose\ncontinues here\n-->\n"
            ),
            (),
        )
        self.assertEqual(
            pr_template_preflight.hard_wrapped_prose_lines("<!-- inline comment -->\n"),
            (),
        )
        with tempfile.TemporaryDirectory() as directory:
            body = Path(directory) / "body.md"
            body.write_text("body\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "regular non-linked"):
                pr_template_preflight.read_body_file(body.parent / "missing.md")
            with mock.patch.object(Path, "open", side_effect=OSError("denied")):
                with self.assertRaisesRegex(ValueError, "could not read body"):
                    pr_template_preflight.read_body_file(body)
            with mock.patch.object(
                Path,
                "open",
                return_value=mock.mock_open(
                    read_data=b"x" * (pr_template_preflight.MAX_BODY_FILE_BYTES + 1)
                ).return_value,
            ):
                with self.assertRaisesRegex(ValueError, "byte limit"):
                    pr_template_preflight.read_body_file(body)
            with mock.patch.object(
                Path,
                "open",
                return_value=mock.mock_open(read_data=b"\xff").return_value,
            ):
                with self.assertRaisesRegex(ValueError, "not valid UTF-8"):
                    pr_template_preflight.read_body_file(body)

    def test_rejects_an_override_of_a_mandatory_template(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_templates(root)
            errors = StringIO()

            with redirect_stderr(errors):
                result = pr_template_preflight.main(
                    [
                        "--title",
                        "fix: correct a security issue",
                        "--template",
                        "security",
                        "--repository-root",
                        str(root),
                    ]
                )

        self.assertEqual(result, 1)
        self.assertIn("requires the 'bugfix' template", errors.getvalue())

    def test_rejects_an_unknown_or_malformed_selected_template(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_templates(root)
            errors = StringIO()
            with redirect_stderr(errors):
                unknown = pr_template_preflight.main(
                    [
                        "--title",
                        "chore: test preflight",
                        "--template",
                        "missing",
                        "--repository-root",
                        str(root),
                    ]
                )
            broken = root / ".github" / "PULL_REQUEST_TEMPLATE" / "security.md"
            broken.write_text(
                "<!-- repo-scaffold:pr-template=default -->\n", encoding="utf-8"
            )
            with redirect_stderr(errors):
                malformed = pr_template_preflight.main(
                    [
                        "--title",
                        "chore: test preflight",
                        "--template",
                        "security",
                        "--repository-root",
                        str(root),
                    ]
                )

        self.assertEqual(unknown, 1)
        self.assertEqual(malformed, 1)
        self.assertIn("unknown pull-request template", errors.getvalue())
        self.assertIn("must contain exactly", errors.getvalue())

    def test_rejects_invalid_or_duplicate_template_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_templates(root)
            directory_path = root / ".github" / "PULL_REQUEST_TEMPLATE"
            invalid = directory_path / "Invalid.md"
            invalid.write_text("template\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unsupported pull-request"):
                pr_template_preflight.template_catalog(root)

            invalid.unlink()
            duplicate = directory_path / "default.md"
            duplicate.write_text("template\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "duplicate pull-request"):
                pr_template_preflight.template_catalog(root)

    def test_rejects_template_catalogs_over_the_entry_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_templates(root)
            template_directory = root / ".github" / "PULL_REQUEST_TEMPLATE"
            for index in range(
                pr_template_preflight.MAX_TEMPLATE_DIRECTORY_ENTRIES + 1
            ):
                template_id = f"extra-{index:03}"
                (template_directory / f"{template_id}.md").write_text(
                    f"<!-- repo-scaffold:pr-template={template_id} -->\n",
                    encoding="utf-8",
                )

            with self.assertRaisesRegex(ValueError, "catalog exceeds"):
                pr_template_preflight.template_catalog(root)

    def test_bounds_raw_template_catalog_directory_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_templates(root)
            template_directory = root / ".github" / "PULL_REQUEST_TEMPLATE"
            original_iterdir = Path.iterdir

            def many_unrelated_entries(path: Path):
                if path == template_directory:
                    return (
                        template_directory / f"asset-{index}.bin"
                        for index in range(
                            pr_template_preflight.MAX_TEMPLATE_DIRECTORY_SCAN_ENTRIES
                            + 1
                        )
                    )
                return original_iterdir(path)

            with mock.patch.object(Path, "iterdir", new=many_unrelated_entries):
                with self.assertRaisesRegex(ValueError, "directory entries"):
                    pr_template_preflight.template_catalog(root)

    def test_reports_unreadable_template_catalog_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_templates(root)
            template_directory = root / ".github" / "PULL_REQUEST_TEMPLATE"
            original_iterdir = Path.iterdir

            def denied_catalog(path: Path):
                if path == template_directory:
                    raise PermissionError("denied")
                return original_iterdir(path)

            with mock.patch.object(Path, "iterdir", new=denied_catalog):
                with self.assertRaisesRegex(PermissionError, "denied"):
                    pr_template_preflight.template_catalog(root)

    def test_rejects_oversized_selected_template(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_templates(root)
            security = root / ".github" / "PULL_REQUEST_TEMPLATE" / "security.md"
            security.write_bytes(b"x" * (pr_template_preflight.MAX_BODY_FILE_BYTES + 1))

            with self.assertRaisesRegex(ValueError, "exceeds"):
                pr_template_preflight.template_path(root, "security")

    def test_selected_template_marker_accepts_crlf_line_endings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_templates(root)
            bugfix = root / ".github" / "PULL_REQUEST_TEMPLATE" / "bugfix.md"
            bugfix.write_bytes(b"<!-- repo-scaffold:pr-template=bugfix -->\r\n")

            self.assertEqual(
                pr_template_preflight.template_path(root, "bugfix"),
                bugfix,
            )

    def test_rejects_linked_or_reparse_repository_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_templates(root)

            with mock.patch.object(
                pr_template_preflight, "path_has_link_or_reparse", return_value=True
            ):
                with self.assertRaisesRegex(ValueError, "linked"):
                    pr_template_preflight.template_catalog(root)

            self.assertTrue(
                pr_template_preflight.path_has_link_or_reparse(root.parent, root)
            )
            self.assertFalse(
                pr_template_preflight.path_has_link_or_reparse(
                    root / ".github" / "missing", root
                )
            )
            with mock.patch.object(
                pr_template_preflight, "is_link_or_reparse", return_value=True
            ):
                self.assertTrue(
                    pr_template_preflight.path_has_link_or_reparse(root, root)
                )
            with mock.patch.object(
                Path,
                "lstat",
                return_value=mock.Mock(st_mode=stat.S_IFLNK, st_file_attributes=0),
            ):
                self.assertTrue(pr_template_preflight.is_link_or_reparse(root))

            with self.assertRaisesRegex(ValueError, "not a directory"):
                pr_template_preflight.safe_repository_root(root / "missing")

            selected = root / ".github" / "PULL_REQUEST_TEMPLATE" / "security.md"

            with mock.patch.object(
                pr_template_preflight,
                "path_has_link_or_reparse",
                side_effect=[False, True],
            ):
                with self.assertRaisesRegex(ValueError, "catalog contains a linked"):
                    pr_template_preflight.template_catalog(root)

            with mock.patch.object(
                pr_template_preflight,
                "path_has_link_or_reparse",
                side_effect=lambda path, _root: path == selected,
            ):
                with self.assertRaisesRegex(ValueError, "template path is linked"):
                    pr_template_preflight.template_catalog(root)

            with (
                mock.patch.object(
                    pr_template_preflight,
                    "safe_repository_root",
                    return_value=root,
                ),
                mock.patch.object(
                    pr_template_preflight,
                    "template_catalog",
                    return_value={"security": selected},
                ),
                mock.patch.object(
                    pr_template_preflight,
                    "path_has_link_or_reparse",
                    return_value=True,
                ),
            ):
                with self.assertRaisesRegex(ValueError, "template path is linked"):
                    pr_template_preflight.template_path(root, "security")
            with mock.patch.object(
                Path,
                "lstat",
                return_value=mock.Mock(st_mode=0, st_file_attributes=0x400),
            ):
                self.assertTrue(pr_template_preflight.is_link_or_reparse(root))

    def test_catalog_fails_closed_on_linked_defaults_and_catalog_directories(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_templates(root)
            default_template = root / ".github" / "PULL_REQUEST_TEMPLATE.md"
            focused_directory = root / ".github" / "PULL_REQUEST_TEMPLATE"

            with mock.patch.object(
                pr_template_preflight,
                "path_has_link_or_reparse",
                side_effect=lambda path, _root: path == default_template,
            ):
                with self.assertRaisesRegex(ValueError, "template path is linked"):
                    pr_template_preflight.template_catalog(root)

            with mock.patch.object(
                pr_template_preflight,
                "path_has_link_or_reparse",
                side_effect=lambda path, _root: path == focused_directory,
            ):
                with self.assertRaisesRegex(ValueError, "catalog contains a linked"):
                    pr_template_preflight.template_catalog(root)

    def test_catalog_ignores_nonfile_and_unsupported_template_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_templates(root)
            (root / "PULL_REQUEST_TEMPLATE").write_text(
                "not a focused-template directory", encoding="utf-8"
            )
            (root / "PULL_REQUEST_TEMPLATE.md").mkdir()
            focused_directory = root / ".github" / "PULL_REQUEST_TEMPLATE"
            (focused_directory / "diagram.png").write_bytes(b"not markdown")
            (focused_directory / "directory.md").mkdir()
            github_directory = root / ".github"
            original_iterdir = Path.iterdir

            def with_missing_template_entries(path: Path):
                if path == github_directory:
                    return iter(
                        [path / "PULL_REQUEST_TEMPLATE.txt", *original_iterdir(path)]
                    )
                if path == focused_directory:
                    return iter([path / "missing.txt", *original_iterdir(path)])
                return original_iterdir(path)

            with mock.patch.object(Path, "iterdir", new=with_missing_template_entries):
                catalog = pr_template_preflight.template_catalog(root)

        self.assertEqual(
            catalog["default"], github_directory / "PULL_REQUEST_TEMPLATE.md"
        )
        self.assertNotIn("missing", catalog)
        self.assertNotIn("directory", catalog)
        self.assertNotIn("diagram", catalog)

    def test_rejects_nonfile_or_unreadable_selected_template(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_templates(root)
            security = root / ".github" / "PULL_REQUEST_TEMPLATE" / "security.md"
            security.unlink()
            security.mkdir()

            with self.assertRaisesRegex(ValueError, "unknown pull-request template"):
                pr_template_preflight.template_path(root, "security")
            with mock.patch.object(
                pr_template_preflight,
                "template_catalog",
                return_value={"security": security},
            ):
                with self.assertRaisesRegex(
                    ValueError, "trusted PR template is missing"
                ):
                    pr_template_preflight.template_path(root, "security")

            security.rmdir()
            security.write_text(
                "<!-- repo-scaffold:pr-template=security -->\n", encoding="utf-8"
            )
            with mock.patch.object(Path, "open", side_effect=OSError("denied")):
                with self.assertRaisesRegex(
                    ValueError, "could not read trusted.*could not read body file"
                ):
                    pr_template_preflight.template_path(root, "security")

    def test_root_entrypoint_targets_the_distributable_preflight_script(self) -> None:
        specification = importlib.util.spec_from_file_location(
            "root_pr_template_preflight", ROOT_ENTRYPOINT
        )
        if specification is None or specification.loader is None:
            self.fail("Could not load the root preflight entrypoint")
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)

        self.assertEqual(module.SKILL_SCRIPT, SCRIPT_PATH)

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
                str(ROOT_ENTRYPOINT),
                run_name="__main__",
                init_globals={"__name__": "__main__"},
            )

        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
