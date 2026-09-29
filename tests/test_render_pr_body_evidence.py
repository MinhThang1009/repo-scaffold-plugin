from __future__ import annotations

import importlib.util
import json
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "render_pr_body_evidence.py"
SPEC = importlib.util.spec_from_file_location("render_pr_body_evidence", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load render_pr_body_evidence.py")
renderer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = renderer
SPEC.loader.exec_module(renderer)


HEAD = "a" * 40
BASE = "b" * 40
REPOSITORY = "MinhThang1009/repo-scaffold-plugin"
BODY = (
    "<!-- repo-scaffold:pr-template=bugfix -->\n\n"
    "## Purpose\n\nOld purpose.\n\n"
    "## Root cause\n\nOld cause.\n\n"
    "## Key changes\n\nOld changes.\n\n"
    "## Verification\n\nOld verification.\n\n"
    "## Required checklist\n\n"
    "<!-- repo-scaffold:required-checklist:start -->\n"
    "- [x] Existing evidence\n"
    "<!-- repo-scaffold:required-checklist:end -->\n\n"
    "## If applicable\n\n"
    "<!-- repo-scaffold:optional-checklist:start -->\n"
    "- [ ] Optional evidence\n"
    "<!-- repo-scaffold:optional-checklist:end -->\n\n"
    "## Related issue\n\nOld issue.\n"
)
PR = {
    "title": "fix: generated body",
    "state": "open",
    "head": {"sha": HEAD, "repo": {"full_name": REPOSITORY}},
    "base": {"sha": BASE},
    "body": BODY,
    "commits": 1,
    "changed_files": 1,
}
COMMITS = [
    [
        {
            "sha": HEAD,
            "commit": {"message": "fix: current subject\n\nCause evidence\nCloses #42"},
        }
    ]
]
FILES = [
    [
        {
            "filename": "scripts/example.py",
            "status": "modified",
            "additions": 2,
            "deletions": 1,
            "changes": 3,
        }
    ]
]
CHECKS = [
    [
        {
            "check_runs": [
                {
                    "id": 7,
                    "name": "quality",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        }
    ]
]


class RenderPullRequestBodyTests(unittest.TestCase):
    def test_render_binds_head_and_replaces_every_narrative_section(self) -> None:
        rendered = renderer.render_dynamic_body(BODY, PR, COMMITS, FILES, CHECKS)

        self.assertNotIn(renderer.MANAGED_BODY_MARKER, rendered)
        self.assertIn("Current subject.", rendered)
        self.assertIn("The confirmed cause was not documented", rendered)
        self.assertNotIn("Evidence; and current subject", rendered)
        self.assertIn("## Purpose\n\n", rendered)
        self.assertIn("## Key changes\n\n- Current subject.", rendered)
        self.assertIn(
            "Current CI and mutation results are available in the GitHub Checks tab",
            rendered,
        )
        self.assertIn("#42", rendered)
        self.assertNotIn("Old purpose", rendered)
        self.assertIn("- [x] Existing evidence", rendered)
        self.assertIn("- [ ] Optional evidence", rendered)

    def test_render_uses_structured_commit_summary_fields(self) -> None:
        structured = [
            [
                {
                    "sha": HEAD,
                    "commit": {
                        "message": (
                            "fix: concise summary\n\n"
                            "Why: Protect the mutation boundary.\n"
                            "Root cause: Indeterminate evidence was treated as actionable.\n"
                            "Changes: Add a fail-closed guard.\n"
                            "Verification: Run the focused regression suite."
                        )
                    },
                }
            ]
        ]
        rendered = renderer.render_dynamic_body(BODY, PR, structured, FILES, CHECKS)

        self.assertIn("Protect the mutation boundary", rendered)
        self.assertIn("Indeterminate evidence was treated as actionable", rendered)
        self.assertIn("Add a fail-closed guard", rendered)
        self.assertIn("Run the focused regression suite", rendered)

    def test_cumulative_summary_covers_prior_work_and_retains_later_changes(
        self,
    ) -> None:
        summary_sha = "c" * 40
        summary = (
            "fix: preserve reviewer context\n\n"
            f"PR-summary-base: {BASE}\n"
            "Why: Prevent indeterminate evidence from changing Issues.\n"
            "Root cause: Checker exit 2 reached the write path.\n"
            "Changes:\n"
            "- Guard Issue writes before reconciliation.\n"
            "- Fix mutation worker environment restoration.\n"
            "Verification: Full suite passed on this revision.\n"
        )
        commits = [
            {"sha": "d" * 40, "commit": {"message": "fix: original issue write"}},
            {"sha": summary_sha, "commit": {"message": summary}},
            {"sha": HEAD, "commit": {"message": "fix: retain newly added edge case"}},
        ]
        rendered = renderer.render_dynamic_body(
            BODY, {**PR, "commits": 3}, commits, FILES, None
        )
        key_changes = rendered.split("## Key changes", 1)[1].split(
            "## Verification", 1
        )[0]

        self.assertIn("Guard Issue writes", key_changes)
        self.assertIn("Fix mutation worker environment", key_changes)
        self.assertIn("Retain newly added edge case", key_changes)
        self.assertNotIn("Original issue write", key_changes)
        self.assertIn(f"Author-reported at <code>{summary_sha[:12]}</code>", rendered)

    def test_cumulative_summary_rejects_changed_base_and_missing_sections(self) -> None:
        summary = (
            f"fix: reviewed summary\n\nPR-summary-base: {BASE}\n"
            "Why: Correct the write boundary.\nRoot cause: Evidence was incomplete.\n"
            "Changes: Reject writes.\nVerification: Regression passed.\n"
        )
        with self.assertRaisesRegex(ValueError, "current pull-request base"):
            renderer._summary_commits([{"sha": HEAD, "message": summary}], "e" * 40)
        with self.assertRaisesRegex(ValueError, "must provide"):
            renderer._summary_commits(
                [
                    {
                        "sha": HEAD,
                        "message": summary.replace("Changes: Reject writes.\n", ""),
                    }
                ],
                BASE,
            )

    def test_later_reviewed_summary_can_replace_one_for_an_old_base(self) -> None:
        fields = (
            "Why: Outcome.\nRoot cause: Cause.\nChanges: Fix.\nVerification: Checked.\n"
        )
        old = {
            "sha": "c" * 40,
            "message": "fix: old\nPR-summary-base: " + "e" * 40 + "\n" + fields,
        }
        current = {
            "sha": HEAD,
            "message": f"fix: current\nPR-summary-base: {BASE}\n" + fields,
        }
        self.assertEqual(renderer._summary_commits([old, current], BASE), [current])

    def test_structured_fields_parse_multiline_bullets_without_reclassifying_prose(
        self,
    ) -> None:
        message = (
            "fix: parser\n\nCause evidence is ordinary prose.\n"
            "Why:\nKeep the review outcome\nclear to contributors.\n\n"
            "Root cause:\nThe parser accepted an unlabeled sentence.\n"
            "Changes:\n- Require an explicit field boundary.\n"
            "  Retain wrapped continuation text.\n- Preserve all concerns.\n"
            "## Unrelated notes\nUnstructured text.\n"
        )
        commit = {"sha": HEAD, "message": message}
        self.assertEqual(
            renderer._structured_values([commit], "purpose"),
            ["Keep the review outcome clear to contributors."],
        )
        self.assertEqual(
            renderer._structured_values([commit], "root"),
            ["The parser accepted an unlabeled sentence."],
        )
        self.assertEqual(
            renderer._structured_values([commit], "changes"),
            [
                "Require an explicit field boundary. Retain wrapped continuation text.",
                "Preserve all concerns.",
            ],
        )

    def test_summary_overflow_fails_instead_of_discarding_a_material_change(
        self,
    ) -> None:
        commits = [
            {"sha": HEAD, "message": f"fix: important topic {number}"}
            for number in range(6)
        ]
        with self.assertRaisesRegex(ValueError, "omitting changes"):
            renderer._changes_summary(commits, FILES[0])
        self.assertEqual(len(renderer._changes_summary(commits[:5], FILES[0])), 5)

    def test_merge_filter_keeps_reverts_and_renderer_changes(self) -> None:
        commits = [
            {"sha": HEAD, "message": "Merge branch main"},
            {"sha": HEAD, "message": "Revert regression in body renderer"},
            {"sha": HEAD, "message": "fix(pr): fix structured summary contract"},
        ]
        self.assertEqual(renderer._core_commits(commits), commits[1:])

    def test_inventory_counts_reject_incomplete_api_evidence(self) -> None:
        for field in ("commits", "changed_files"):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "evidence is incomplete"):
                    renderer.render_dynamic_body(
                        BODY, {**PR, field: 2}, COMMITS, FILES, None
                    )
        with self.assertRaisesRegex(ValueError, "supported API limit"):
            renderer.render_dynamic_body(
                BODY, {**PR, "commits": 251}, COMMITS, FILES, None
            )

    def test_manual_acceptance_and_rollout_sections_are_preserved(self) -> None:
        manual = "## Acceptance criteria\n\n- Keyboard access remains available.\n\n## Rollout plan\n\nDeploy to staging first.\n\n"
        body = BODY.replace("## Verification", manual + "## Verification")
        rendered = renderer.render_dynamic_body(
            body, {**PR, "body": body}, COMMITS, FILES, None
        )
        self.assertIn(manual, rendered)

    def test_untrusted_summary_text_is_escaped_once(self) -> None:
        message = "fix: safe summary\n\nWhy: Keep A & B <safe> [link](https://example.com).\nRoot cause: A & B.\nChanges: Guard *writes*.\n"
        commits = [{"sha": HEAD, "commit": {"message": message}}]
        rendered = renderer.render_dynamic_body(BODY, PR, commits, FILES, None)
        self.assertIn("A &amp; B &lt;safe&gt; \\[link\\]", rendered)
        self.assertNotIn("&amp;amp;", rendered)

    def test_template_test_sections_receive_verification_and_vietnamese_messages(
        self,
    ) -> None:
        body = BODY.replace("## Verification", "## How to test")
        rendered = renderer.render_dynamic_body(
            body, {**PR, "body": body}, COMMITS, FILES, None
        )
        self.assertIn("## How to test\n\n- Current CI", rendered)
        body = (
            BODY.replace("## Purpose", "## Mục đích")
            .replace("## Root cause", "## Nguyên nhân gốc")
            .replace("## Verification", "## Cách kiểm thử")
        )
        rendered = renderer.render_dynamic_body(
            body, {**PR, "body": body}, COMMITS, FILES, None
        )
        self.assertIn("## Cách kiểm thử\n\n- Xem kết quả CI", rendered)
        self.assertIn("chưa ghi nguyên nhân đã xác nhận", rendered)

    def test_excess_verification_notes_are_not_silently_discarded(self) -> None:
        commits = [
            {
                "sha": HEAD,
                "message": f"fix: change {number}\nVerification: Check {number} passed.",
            }
            for number in range(5)
        ]
        with self.assertRaisesRegex(ValueError, "verification notes"):
            renderer._verification_summary(commits)

    def test_blank_summary_sentence_is_explicit(self) -> None:
        self.assertEqual(
            renderer._sentence("  \n"),
            "The change is described by the available commit evidence.",
        )

    def test_render_is_deterministic_and_preserves_crlf(self) -> None:
        crlf_body = BODY.replace("\n", "\r\n")
        crlf_pr = {**PR, "body": crlf_body}
        rendered = renderer.render_dynamic_body(
            crlf_body, crlf_pr, COMMITS, FILES, CHECKS
        )

        self.assertEqual(
            renderer.render_dynamic_body(
                rendered, {**crlf_pr, "body": rendered}, COMMITS, FILES, CHECKS
            ),
            rendered,
        )
        self.assertNotIn("\n", rendered.replace("\r\n", ""))
        no_trailing = BODY.rstrip("\n")
        renderer.render_dynamic_body(
            no_trailing, {**PR, "body": no_trailing}, COMMITS, FILES, CHECKS
        )
        legacy_body = BODY.replace(
            "<!-- repo-scaffold:pr-template=bugfix -->\n",
            "<!-- repo-scaffold:pr-template=bugfix -->\n"
            + renderer.MANAGED_BODY_MARKER
            + "\n",
        )
        renderer.render_dynamic_body(
            legacy_body,
            {**PR, "body": legacy_body},
            COMMITS,
            FILES,
            CHECKS,
        )

    def test_render_accepts_direct_check_run_pages_and_empty_files(self) -> None:
        checks = [{"id": 1, "name": "pending", "status": "queued", "conclusion": None}]
        rendered = renderer.render_dynamic_body(
            BODY, {**PR, "changed_files": 0}, COMMITS[0], [], checks
        )

        self.assertNotIn("pending", rendered)
        self.assertIn("Checks tab", rendered)
        self.assertIn("No changed files", rendered)

    def test_render_rejects_stale_or_untrusted_evidence(self) -> None:
        cases = (
            ({**PR, "body": "different"}, COMMITS, FILES, CHECKS, "body changed"),
            ({**PR, "state": "closed"}, COMMITS, FILES, CHECKS, "must remain open"),
            (PR, [[{**COMMITS[0][0], "sha": BASE}]], FILES, CHECKS, "latest commit"),
            (
                PR,
                COMMITS,
                [
                    [
                        {**FILES[0][0], "filename": "same"},
                        {**FILES[0][0], "filename": "same"},
                    ]
                ],
                CHECKS,
                "duplicate",
            ),
        )
        for pr, commits, files, checks, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    renderer.render_dynamic_body(BODY, pr, commits, files, checks)

    def test_render_rejects_malformed_body_and_duplicate_json(self) -> None:
        with self.assertRaisesRegex(ValueError, "trusted template marker"):
            renderer.render_dynamic_body("body\n", PR, COMMITS, FILES, CHECKS)
        with self.assertRaisesRegex(ValueError, "duplicate JSON"):
            renderer._unique_object([("a", 1), ("a", 2)])
        with self.assertRaisesRegex(ValueError, "pagination shape"):
            renderer._flatten_pages([1], "items")

    def test_validation_boundaries_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "could not read"):
            renderer._read_bytes(Path("missing-evidence.json"), "evidence", 10)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            oversized = root / "oversized"
            oversized.write_bytes(b"x" * 11)
            with self.assertRaisesRegex(ValueError, "safety cap"):
                renderer._read_bytes(oversized, "evidence", 10)
            invalid = root / "invalid"
            invalid.write_bytes(b"\xff")
            with self.assertRaisesRegex(ValueError, "valid UTF-8"):
                renderer.read_body(invalid)
        with self.assertRaisesRegex(ValueError, "unsupported carriage"):
            renderer._line_ending("a\rb")
        with self.assertRaisesRegex(ValueError, "mixes CRLF"):
            renderer._line_ending("a\r\nb\n")
        with self.assertRaisesRegex(ValueError, "JSON array"):
            renderer._flatten_pages({}, "items")
        with self.assertRaisesRegex(ValueError, "non-object item"):
            renderer._flatten_pages([[1]], "items")
        with self.assertRaisesRegex(ValueError, "must be text"):
            renderer._bounded_text(None, "value")
        with self.assertRaisesRegex(ValueError, "safety cap"):
            renderer._bounded_text("x" * 3000, "value")
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            renderer._bounded_text("", "value", allow_empty=False)
        with self.assertRaisesRegex(ValueError, "40 hexadecimal"):
            renderer._validate_sha("short", "sha")
        with self.assertRaisesRegex(ValueError, "OWNER/REPOSITORY"):
            renderer._validate_repository("unsafe", "repository")
        with self.assertRaisesRegex(ValueError, "valid head"):
            renderer._head_and_base({})
        with self.assertRaisesRegex(ValueError, "head repository"):
            renderer._head_and_base({"head": {"sha": HEAD}, "base": {"sha": BASE}})
        with self.assertRaisesRegex(ValueError, "missing"):
            renderer._validate_commits([], HEAD)
        with self.assertRaisesRegex(ValueError, "duplicate SHA"):
            renderer._validate_commits([COMMITS[0][0], COMMITS[0][0]], HEAD)
        with self.assertRaisesRegex(ValueError, "metadata"):
            renderer._validate_commits([[{"sha": HEAD}]], HEAD)
        with self.assertRaisesRegex(ValueError, "exceed"):
            renderer._validate_files([{"filename": "x"}] * (renderer.MAX_FILES + 1))
        with self.assertRaisesRegex(ValueError, "non-negative"):
            renderer._validate_files(
                [
                    [
                        {
                            "filename": "x",
                            "status": "modified",
                            "additions": -1,
                            "deletions": 0,
                            "changes": 1,
                        }
                    ]
                ]
            )
        with self.assertRaisesRegex(ValueError, "unknown status"):
            renderer._validate_files(
                [
                    [
                        {
                            "filename": "x",
                            "status": "unknown",
                            "additions": 0,
                            "deletions": 0,
                            "changes": 0,
                        }
                    ]
                ]
            )
        self.assertIn(
            "confirmed cause was not documented",
            renderer._root_cause_lines([{"sha": HEAD, "message": "subject"}])[0],
        )
        self.assertIn(
            "confirmed cause was not documented",
            renderer._root_cause_lines(
                [{"sha": HEAD, "message": "subject\n\nUnstructured detail"}]
            )[0],
        )
        self.assertEqual(
            renderer._structured_values(
                [{"sha": HEAD, "message": "subject\nWhy: same\nWhy: same"}],
                "purpose",
            ),
            ["same"],
        )
        self.assertEqual(
            len(
                renderer._core_commits(
                    [
                        {
                            "sha": HEAD,
                            "message": "docs(pr): clarify structured summary contract",
                        },
                        {"sha": HEAD, "message": "fix: primary change"},
                    ]
                )
            ),
            2,
        )
        self.assertEqual(
            len(
                renderer._changes_summary(
                    [
                        {"sha": HEAD, "message": "fix: current subject"},
                        {"sha": HEAD, "message": "fix: current subject"},
                    ],
                    FILES[0],
                )
            ),
            1,
        )
        self.assertGreaterEqual(
            len(
                renderer._changes_summary(
                    [
                        {"sha": HEAD, "message": "fix: one"},
                        {"sha": HEAD, "message": "fix: two"},
                        {"sha": HEAD, "message": "fix: three"},
                        {"sha": HEAD, "message": "fix: four"},
                    ],
                    FILES[0],
                )
            ),
            4,
        )
        self.assertEqual(
            renderer._verification_summary([]),
            [
                "- Current CI and mutation results are available in the GitHub Checks tab; commit notes do not certify a later head."
            ],
        )
        self.assertIn("available commit and diff evidence", renderer._summary_text([]))
        self.assertEqual(renderer._summary_text(["one", "two"]), "One. Two.")
        with self.assertRaisesRegex(ValueError, "dropping context"):
            renderer._summary_text(["one", "two", "three", "four"])
        duplicate_issue = {"sha": HEAD, "message": "subject\nCloses #1 and #1"}
        self.assertEqual(len(renderer._issue_lines([duplicate_issue])), 1)
        with self.assertRaisesRegex(ValueError, "unterminated"):
            renderer._remove_protocol_lines([renderer.HEAD_START_MARKER])
        self.assertEqual(
            renderer._remove_protocol_lines(
                [renderer.HEAD_START_MARKER, "old metadata", renderer.HEAD_END_MARKER]
            ),
            [],
        )
        with mock.patch.object(renderer, "MAX_BODY_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "generated"):
                renderer.render_dynamic_body(BODY, PR, COMMITS, FILES, CHECKS)
            with self.assertRaisesRegex(ValueError, "generated"):
                renderer.write_body(
                    Path(tempfile.gettempdir()) / "body-output.md", "body"
                )
        with self.assertRaisesRegex(ValueError, "could not write"):
            renderer.write_body(Path(tempfile.gettempdir()), "body")

    def test_cli_renders_and_rejects_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            body = root / "body.md"
            pr = root / "pr.json"
            commits = root / "commits.json"
            files = root / "files.json"
            checks = root / "checks.json"
            output = root / "updated.md"
            body.write_bytes(BODY.encode("utf-8"))
            pr.write_text(json.dumps(PR), encoding="utf-8")
            commits.write_text(json.dumps(COMMITS), encoding="utf-8")
            files.write_text(json.dumps(FILES), encoding="utf-8")
            checks.write_text(json.dumps(CHECKS), encoding="utf-8")
            stdout = StringIO()
            with redirect_stdout(stdout):
                result = renderer.main(
                    [
                        "--body-file",
                        str(body),
                        "--pr-file",
                        str(pr),
                        "--commits-file",
                        str(commits),
                        "--files-file",
                        str(files),
                        "--checks-file",
                        str(checks),
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(result, 0)
            self.assertIn("current PR evidence", stdout.getvalue())
            self.assertIn("Current subject.", output.read_text(encoding="utf-8"))

            pr.write_text("{", encoding="utf-8")
            stderr = StringIO()
            with redirect_stderr(stderr):
                result = renderer.main(
                    [
                        "--body-file",
                        str(body),
                        "--pr-file",
                        str(pr),
                        "--commits-file",
                        str(commits),
                        "--files-file",
                        str(files),
                        "--checks-file",
                        str(checks),
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(result, 1)
            self.assertIn("valid JSON", stderr.getvalue())
            pr.write_text("[]", encoding="utf-8")
            with redirect_stderr(StringIO()):
                result = renderer.main(
                    [
                        "--body-file",
                        str(body),
                        "--pr-file",
                        str(pr),
                        "--commits-file",
                        str(commits),
                        "--files-file",
                        str(files),
                        "--checks-file",
                        str(checks),
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(result, 1)

    def test_script_entrypoint_returns_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {
                name: root / f"{name}.json"
                for name in ("pr", "commits", "files", "checks")
            }
            root.joinpath("body.md").write_bytes(BODY.encode("utf-8"))
            paths["pr"].write_text(json.dumps(PR), encoding="utf-8")
            paths["commits"].write_text(json.dumps(COMMITS), encoding="utf-8")
            paths["files"].write_text(json.dumps(FILES), encoding="utf-8")
            paths["checks"].write_text(json.dumps(CHECKS), encoding="utf-8")
            argv = [
                str(SCRIPT_PATH),
                "--body-file",
                str(root / "body.md"),
                "--pr-file",
                str(paths["pr"]),
                "--commits-file",
                str(paths["commits"]),
                "--files-file",
                str(paths["files"]),
                "--checks-file",
                str(paths["checks"]),
                "--output",
                str(root / "output.md"),
            ]
            old_argv = sys.argv
            sys.argv = argv
            try:
                with self.assertRaises(SystemExit) as raised:
                    runpy.run_path(str(SCRIPT_PATH), run_name="__main__")
            finally:
                sys.argv = old_argv
            self.assertEqual(raised.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
