from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = (
    ".github/workflows/ci.yml",
    ".github/workflows/community-health.yml",
    ".github/workflows/freshness.yml",
    ".github/workflows/official-docs.yml",
    "skills/repo-scaffold/assets/workflows/community-health.yml",
    "skills/repo-scaffold/assets/workflows/freshness.yml",
)
BASH = shutil.which("bash")
NATIVE_GH = shutil.which("gh")
if BASH is None and os.name == "nt":
    git_bash = (
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
    )
    if git_bash.is_file():
        BASH = str(git_bash)


def child_cli_environment(overrides: dict[str, str]) -> dict[str, str]:
    """Keep mutmut's parent-test trampoline state out of copied CLI children."""
    environment = os.environ.copy()
    environment["MUTANT_UNDER_TEST"] = ""
    environment["MUTMUT_DEPENDENCY_DEPTH"] = "-1"
    environment.update(overrides)
    return environment


def search_reconciliation_scripts() -> list[tuple[str, str]]:
    """Discover every shipped Issue Search consumer rather than one test case."""
    scripts = []
    for directory in (
        ROOT / ".github/workflows",
        ROOT / "skills/repo-scaffold/assets/workflows",
    ):
        for path in sorted(directory.iterdir()):
            if path.suffix not in {".yml", ".yaml"}:
                continue
            document = yaml.load(
                path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader
            )
            for job in document["jobs"].values():
                for step in job.get("steps", []):
                    command = step.get("run", "")
                    if "search/issues?" in command:
                        scripts.append((path.relative_to(ROOT).as_posix(), command))
    return scripts


@contextmanager
def native_search_fixture(
    status: int = 200,
) -> Iterator[tuple[str, dict[str, str], list[object]]]:
    """Serve bounded synthetic JSON locally, with no inherited authentication."""
    response: list[object] = [{}]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            payload = b"" if status == 204 else json.dumps(response[0]).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    with tempfile.TemporaryDirectory() as configuration:
        environment = child_cli_environment(
            {
                "GH_CONFIG_DIR": configuration,
                "GH_TOKEN": "synthetic-fixture-token-not-a-credential",
                "GH_HOST": "github.com",
                "NO_PROXY": "127.0.0.1,localhost",
            }
        )
        for name in (
            "GITHUB_TOKEN",
            "GH_ENTERPRISE_TOKEN",
            "GITHUB_ENTERPRISE_TOKEN",
            "GH_DEBUG",
            "GH_REPO",
        ):
            environment.pop(name, None)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield (
                f"http://127.0.0.1:{server.server_port}/fixture",
                environment,
                response,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class ReminderWorkflowTests(unittest.TestCase):
    @unittest.skipUnless(NATIVE_GH and BASH, "requires GitHub CLI and Bash")
    def test_missing_search_response_cannot_masquerade_as_checked_empty_lookup(
        self,
    ) -> None:
        stub = r"""gh() {
  if [[ "$1" == api ]]; then
    local query=''
    while (( $# > 0 )); do
      if [[ "$1" == --jq ]]; then shift; query="$1"; break; fi
      shift
    done
    "$TEST_NATIVE_GH" api "$TEST_FIXTURE_URL" --method GET --jq "$query"
    return $?
  fi
  printf 'MUTATION:%s\n' "$2"
}
python() { "$TEST_PYTHON" "$@"; }
"""
        with native_search_fixture(204) as (url, fixture_environment, _response):
            for relative, script in search_reconciliation_scripts():
                with (
                    self.subTest(workflow=relative),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    self.install_body_preflight(
                        root, root_entrypoint=relative == ".github/workflows/ci.yml"
                    )
                    marker = re.search(r"marker='([^']+)'", script)
                    assert marker is not None
                    for report in (
                        "community-health.md",
                        "freshness.md",
                        "official-docs.md",
                    ):
                        (root / report).write_text(
                            marker.group(1) + "\n\nSynthetic complete report.\n",
                            encoding="utf-8",
                        )
                    environment = {
                        **fixture_environment,
                        "TEST_NATIVE_GH": str(NATIVE_GH).replace("\\", "/"),
                        "TEST_PYTHON": sys.executable.replace("\\", "/"),
                        "TEST_FIXTURE_URL": url,
                        "REPOSITORY": "synthetic/example",
                        "GITHUB_REPOSITORY": "synthetic/example",
                        "RUNNER_TEMP": ".",
                        "CHECKER_EXIT": "1",
                        "PYTHON_CANARY_RESULT": "failure",
                        "TOOLCHAIN_CANARY_RESULT": "success",
                        "RUN_URL": "https://github.com/synthetic/example/actions/runs/1",
                        "GITHUB_STEP_SUMMARY": str(root / "summary.md"),
                    }
                    process = subprocess.run(
                        [str(BASH), "--noprofile", "--norc", "-s"],
                        input=stub + script,
                        cwd=root,
                        env=environment,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=20,
                        check=False,
                    )
                    self.assertNotIn("MUTATION:", process.stdout)
                    self.assertNotEqual(process.returncode, 0)

    @unittest.skipUnless(NATIVE_GH, "requires the GitHub CLI query engine")
    def test_native_search_filters_reject_partial_and_malformed_evidence(self) -> None:
        filters = set()
        for relative, command in search_reconciliation_scripts():
            expressions = re.findall(r"--jq\s+'([^']+)'", command)
            self.assertEqual(len(expressions), 1, relative)
            filters.add(expressions[0])
        self.assertTrue(filters)
        line_projection = "else .items[].number end"
        for expression in tuple(filters):
            filters.add(
                expression.replace(
                    'else [.items[].number] | join(" ") end', line_projection, 1
                )
            )
        complete = {
            "incomplete_results": False,
            "total_count": 1,
            "items": [{"number": 41}],
        }
        cases: tuple[tuple[str, object, str | None], ...] = (
            ("empty", {**complete, "total_count": 0, "items": []}, "none"),
            ("one", complete, "41"),
            (
                "two",
                {
                    **complete,
                    "total_count": 2,
                    "items": [{"number": 41}, {"number": 42}],
                },
                "41 42",
            ),
            (
                "more-than-page",
                {
                    **complete,
                    "total_count": 99,
                    "items": [{"number": 41}, {"number": 42}],
                },
                "41 42",
            ),
            (
                "partial-empty",
                {**complete, "incomplete_results": True, "items": []},
                None,
            ),
            ("partial-one", {**complete, "incomplete_results": True}, None),
            (
                "missing-completeness",
                {"total_count": 1, "items": [{"number": 41}]},
                None,
            ),
            (
                "ambiguous-completeness",
                {**complete, "incomplete_results": "false"},
                None,
            ),
            ("negative-count", {**complete, "total_count": -1}, None),
            ("fractional-count", {**complete, "total_count": 1.5}, None),
            ("string-count", {**complete, "total_count": "1"}, None),
            (
                "missing-count",
                {"incomplete_results": False, "items": [{"number": 41}]},
                None,
            ),
            ("inconsistent-count", {**complete, "items": []}, None),
            ("non-array-items", {**complete, "items": {"number": 41}}, None),
            (
                "excess-items",
                {
                    **complete,
                    "total_count": 3,
                    "items": [{"number": 41}, {"number": 42}, {"number": 43}],
                },
                None,
            ),
            ("missing-number", {**complete, "items": [{}]}, None),
            ("zero-number", {**complete, "items": [{"number": 0}]}, None),
            ("fractional-number", {**complete, "items": [{"number": 41.5}]}, None),
            ("string-number", {**complete, "items": [{"number": "41"}]}, None),
            (
                "option-number",
                {**complete, "items": [{"number": "--repo other/target"}]},
                None,
            ),
            (
                "duplicate-number",
                {
                    **complete,
                    "total_count": 2,
                    "items": [{"number": 41}, {"number": 41}],
                },
                None,
            ),
            ("non-object-response", [], None),
        )
        with native_search_fixture() as (url, environment, response):
            for expression in filters:
                for label, payload, expected in cases:
                    with self.subTest(case=label, query=expression):
                        response[0] = payload
                        result = subprocess.run(
                            [
                                str(NATIVE_GH),
                                "api",
                                url,
                                "--method",
                                "GET",
                                "--jq",
                                expression,
                            ],
                            env=environment,
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            timeout=15,
                            check=False,
                        )
                        if expected is None:
                            self.assertNotEqual(result.returncode, 0, result.stdout)
                            self.assertEqual(result.stdout, "")
                        else:
                            self.assertEqual(result.returncode, 0, result.stderr)
                            selected = (
                                expected.replace(" ", "\n")
                                if line_projection in expression
                                else expected
                            )
                            self.assertEqual(result.stdout.strip(), selected)

    @unittest.skipUnless(NATIVE_GH and BASH, "requires GitHub CLI and Bash")
    def test_search_evidence_controls_every_real_reminder_mutation(self) -> None:
        stub = r"""gh() {
  if [[ "$1" == api ]]; then
    local query=''
    while (( $# > 0 )); do
      if [[ "$1" == --jq ]]; then shift; query="$1"; break; fi
      shift
    done
    "$TEST_NATIVE_GH" api "$TEST_FIXTURE_URL" --method GET --jq "$query"
    return $?
  fi
  printf 'MUTATION:%s\n' "$2"
}
python() { "$TEST_PYTHON" "$@"; }
"""
        complete = {
            "incomplete_results": False,
            "total_count": 1,
            "items": [{"number": 41}],
        }
        cases: tuple[tuple[str, object, str, str | None], ...] = (
            (
                "complete-create",
                {**complete, "total_count": 0, "items": []},
                "1",
                "create",
            ),
            ("complete-edit", complete, "1", "edit"),
            ("complete-close", complete, "0", "close"),
            (
                "complete-clean-no-op",
                {**complete, "total_count": 0, "items": []},
                "0",
                None,
            ),
            (
                "incomplete-create",
                {**complete, "incomplete_results": True, "items": []},
                "1",
                None,
            ),
            ("incomplete-edit", {**complete, "incomplete_results": True}, "1", None),
            ("incomplete-close", {**complete, "incomplete_results": True}, "0", None),
            ("count-mismatch", {**complete, "items": []}, "1", None),
            (
                "invalid-number",
                {**complete, "items": [{"number": "--help"}]},
                "1",
                None,
            ),
        )
        with native_search_fixture() as (url, fixture_environment, response):
            for relative, script in search_reconciliation_scripts():
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    self.install_body_preflight(
                        root, root_entrypoint=relative == ".github/workflows/ci.yml"
                    )
                    marker = re.search(r"marker='([^']+)'", script)
                    assert marker is not None
                    for report in (
                        "community-health.md",
                        "freshness.md",
                        "official-docs.md",
                    ):
                        (root / report).write_text(
                            marker.group(1) + "\n\nSynthetic complete report.\n",
                            encoding="utf-8",
                        )
                    for label, payload, checker_exit, expected in cases:
                        with self.subTest(workflow=relative, case=label):
                            response[0] = payload
                            environment = {
                                **fixture_environment,
                                "TEST_NATIVE_GH": str(NATIVE_GH).replace("\\", "/"),
                                "TEST_PYTHON": sys.executable.replace("\\", "/"),
                                "TEST_FIXTURE_URL": url,
                                "REPOSITORY": "synthetic/example",
                                "GITHUB_REPOSITORY": "synthetic/example",
                                "RUNNER_TEMP": ".",
                                "CHECKER_EXIT": checker_exit,
                                "PYTHON_CANARY_RESULT": "failure"
                                if checker_exit == "1"
                                else "success",
                                "TOOLCHAIN_CANARY_RESULT": "success",
                                "RUN_URL": "https://github.com/synthetic/example/actions/runs/1",
                                "GITHUB_STEP_SUMMARY": str(root / "summary.md"),
                            }
                            result = subprocess.run(
                                [str(BASH), "--noprofile", "--norc", "-s"],
                                input=stub + script,
                                cwd=root,
                                env=environment,
                                capture_output=True,
                                text=True,
                                encoding="utf-8",
                                timeout=20,
                                check=False,
                            )
                            mutations = re.findall(
                                r"^MUTATION:(\w+)$", result.stdout, re.MULTILINE
                            )
                            self.assertEqual(
                                mutations, [expected] if expected else [], result.stderr
                            )
                            if label.startswith(("incomplete", "count-", "invalid-")):
                                self.assertNotEqual(result.returncode, 0)

    @staticmethod
    def install_body_preflight(root: Path, *, root_entrypoint: bool = False) -> None:
        script = root / "scripts" / "markdown_body_preflight.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        bundled_script = (
            ROOT / "skills" / "repo-scaffold" / "scripts" / "markdown_body_preflight.py"
        )
        if root_entrypoint:
            shutil.copyfile(ROOT / "scripts" / "markdown_body_preflight.py", script)
            bundled_destination = (
                root / "skills" / "repo-scaffold" / "scripts" / script.name
            )
            bundled_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(bundled_script, bundled_destination)
        else:
            shutil.copyfile(bundled_script, script)

    def test_child_cli_environment_does_not_inherit_mutmut_runtime_flags(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"MUTANT_UNDER_TEST": "stats", "MUTMUT_DEPENDENCY_DEPTH": "1"},
        ):
            environment = child_cli_environment({"CHILD_TEST_VALUE": "preserved"})

        self.assertEqual(environment["MUTANT_UNDER_TEST"], "")
        self.assertEqual(environment["MUTMUT_DEPENDENCY_DEPTH"], "-1")
        self.assertEqual(environment["CHILD_TEST_VALUE"], "preserved")

    def test_every_issue_body_writer_preflights_before_edit_or_create(self) -> None:
        report_paths = {
            ".github/workflows/ci.yml": "$report",
            ".github/workflows/community-health.yml": "$RUNNER_TEMP/community-health.md",
            ".github/workflows/freshness.yml": "$RUNNER_TEMP/freshness.md",
            ".github/workflows/official-docs.yml": "$RUNNER_TEMP/official-docs.md",
            "skills/repo-scaffold/assets/workflows/community-health.yml": "$RUNNER_TEMP/community-health.md",
            "skills/repo-scaffold/assets/workflows/freshness.yml": "$RUNNER_TEMP/freshness.md",
        }
        for relative, report_path in report_paths.items():
            document = yaml.load(
                (ROOT / relative).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
            )
            scripts = [
                step["run"]
                for job in document["jobs"].values()
                for step in job["steps"]
                if "Reconcile" in step.get("name", "") and "issue" in step["name"]
            ]
            self.assertEqual(len(scripts), 1, relative)
            script = scripts[0]
            preflight = (
                f'python scripts/markdown_body_preflight.py --body-file "{report_path}"'
            )
            with self.subTest(workflow=relative):
                self.assertIn(preflight, script)
                self.assertLess(script.index(preflight), script.index("gh issue edit"))
                self.assertLess(
                    script.index(preflight), script.index("gh issue create")
                )

    @unittest.skipUnless(BASH, "requires Bash (Git Bash on Windows)")
    def test_hard_wrapped_issue_reports_fail_before_mutation(self) -> None:
        cases = (
            (
                ".github/workflows/ci.yml",
                "ci-policy-drift.md",
                "repo-scaffold-ci-policy-drift",
                True,
            ),
            (
                ".github/workflows/community-health.yml",
                "community-health.md",
                "repo-scaffold-community-health-drift",
                False,
            ),
            (
                ".github/workflows/freshness.yml",
                "freshness.md",
                "repo-scaffold-freshness-audit",
                False,
            ),
            (
                ".github/workflows/official-docs.yml",
                "official-docs.md",
                "repo-scaffold-official-docs-audit",
                False,
            ),
            (
                "skills/repo-scaffold/assets/workflows/community-health.yml",
                "community-health.md",
                "repo-scaffold-community-health-drift",
                False,
            ),
            (
                "skills/repo-scaffold/assets/workflows/freshness.yml",
                "freshness.md",
                "repo-scaffold-freshness-audit",
                False,
            ),
        )
        stub = """gh() {
  if [[ "$1" == api ]]; then printf '41\\n'; return 0; fi
  printf 'MUTATION:%s\\n' "$2"
}
"""
        for relative, report_name, marker, root_entrypoint in cases:
            document = yaml.load(
                (ROOT / relative).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
            )
            script = next(
                step["run"]
                for job in document["jobs"].values()
                for step in job["steps"]
                if "Reconcile" in step.get("name", "") and "issue" in step["name"]
            )
            if root_entrypoint:
                preflight_line = (
                    'python scripts/markdown_body_preflight.py --body-file "$report"\n'
                )
                self.assertIn(preflight_line, script)
                script = script.replace(
                    preflight_line,
                    'printf "%s\\n" "" "First line" "continued line" >> "$report"\n'
                    + preflight_line,
                    1,
                )
            with self.subTest(workflow=relative):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    self.install_body_preflight(root, root_entrypoint=root_entrypoint)
                    (root / report_name).write_text(
                        f"<!-- {marker} -->\nFirst line\ncontinued line\n",
                        encoding="utf-8",
                    )
                    environment = child_cli_environment(
                        {
                            "REPOSITORY": "synthetic/example",
                            "GITHUB_REPOSITORY": "synthetic/example",
                            "RUNNER_TEMP": ".",
                            "CHECKER_EXIT": "1",
                            "PYTHON_CANARY_RESULT": "failure",
                            "TOOLCHAIN_CANARY_RESULT": "success",
                            "RUN_URL": "https://github.com/synthetic/example/actions/runs/1",
                            "GITHUB_STEP_SUMMARY": str(root / "summary.md"),
                        }
                    )
                    result = subprocess.run(
                        [str(BASH), "--noprofile", "--norc", "-s"],
                        input=stub + script,
                        cwd=root,
                        env=environment,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=15,
                        check=False,
                    )

                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertIn("hard-wrapped prose", result.stderr)
                self.assertNotIn("MUTATION:", result.stdout)

    def test_reminder_workflows_serialize_repository_issue_state(self) -> None:
        for relative in WORKFLOWS:
            document = yaml.load(
                (ROOT / relative).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
            )
            if relative == ".github/workflows/ci.yml":
                concurrency = document["jobs"]["policy-drift-reminder"]["concurrency"]
                expected_group = (
                    "repo-scaffold-ci-policy-drift-${{ github.repository }}"
                )
            else:
                concurrency = document["concurrency"]
                expected_group = (
                    "repo-scaffold-community-health-${{ github.repository }}"
                    if relative.endswith("/community-health.yml")
                    else (
                        "repo-scaffold-official-docs-${{ github.repository }}"
                        if relative.endswith("/official-docs.yml")
                        else "repo-scaffold-freshness-${{ github.repository }}"
                    )
                )
            self.assertEqual(
                concurrency,
                {"group": expected_group, "cancel-in-progress": "false"},
                relative,
            )
            self.assertNotIn("github.workflow", concurrency["group"], relative)

    def test_issue_lookup_is_bounded_search(self) -> None:
        for relative in WORKFLOWS:
            document = yaml.load(
                (ROOT / relative).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
            )
            scripts = [
                step["run"]
                for job in document["jobs"].values()
                for step in job["steps"]
                if "Reconcile" in step.get("name", "") and "issue" in step["name"]
            ]
            self.assertEqual(len(scripts), 1, relative)
            script = scripts[0]
            self.assertIn("search/issues?q=repo:", script, relative)
            self.assertIn("is:issue+is:open+in:body+", script, relative)
            self.assertIn("%22%3C%21--+", script, relative)
            self.assertIn("+--%3E%22&per_page=2", script, relative)
            self.assertIn("per_page=2", script, relative)
            self.assertIn(".incomplete_results == false", script, relative)
            self.assertIn('(.total_count | type) == "number"', script, relative)
            self.assertIn(
                "(.items | length) == ([.total_count, 2] | min)", script, relative
            )
            self.assertIn('[.items[].number] | join(" ")', script, relative)
            self.assertNotIn("--paginate", script, relative)

    def test_issue_writing_reminders_checkout_the_default_branch(self) -> None:
        for relative in (
            ".github/workflows/community-health.yml",
            ".github/workflows/freshness.yml",
            ".github/workflows/official-docs.yml",
            "skills/repo-scaffold/assets/workflows/community-health.yml",
            "skills/repo-scaffold/assets/workflows/freshness.yml",
        ):
            document = yaml.load(
                (ROOT / relative).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
            )
            checkout_steps = [
                step
                for job in document["jobs"].values()
                for step in job["steps"]
                if step.get("uses", "").startswith("actions/checkout@")
            ]
            self.assertEqual(len(checkout_steps), 1, relative)
            self.assertEqual(
                checkout_steps[0]["with"],
                {
                    "ref": "${{ github.event.repository.default_branch }}",
                    "persist-credentials": "false",
                },
                relative,
            )

    @unittest.skipUnless(BASH, "requires Bash (Git Bash on Windows)")
    def test_clean_status_requires_report_marker_before_close(self) -> None:
        for relative, report in (
            (".github/workflows/freshness.yml", "freshness.md"),
            (".github/workflows/community-health.yml", "community-health.md"),
            (".github/workflows/official-docs.yml", "official-docs.md"),
            ("skills/repo-scaffold/assets/workflows/freshness.yml", "freshness.md"),
        ):
            document = yaml.load(
                (ROOT / relative).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
            )
            script = next(
                step["run"]
                for job in document["jobs"].values()
                for step in job["steps"]
                if "Reconcile" in step.get("name", "") and "issue" in step["name"]
            )
            with self.subTest(workflow=relative):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    self.install_body_preflight(root)
                    (root / report).write_text("wrong-marker\n", encoding="utf-8")
                    environment = child_cli_environment(
                        {
                            "REPOSITORY": "synthetic/example",
                            "GITHUB_REPOSITORY": "synthetic/example",
                            "RUNNER_TEMP": ".",
                            "CHECKER_EXIT": "0",
                        }
                    )
                    stub = """gh() {
  if [[ "$1" == api ]]; then printf '41\\n'; return 0; fi
  printf 'MUTATION:%s\\n' "$2"
}
"""
                    result = subprocess.run(
                        [str(BASH), "--noprofile", "--norc", "-s"],
                        input=stub + script,
                        cwd=root,
                        env=environment,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=15,
                        check=False,
                    )
                    self.assertNotIn("MUTATION:close", result.stdout)
                    self.assertNotEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(BASH, "requires Bash (Git Bash on Windows)")
    def test_issue_lookup_must_succeed_before_any_reminder_mutation(self) -> None:
        for relative in WORKFLOWS:
            document = yaml.load(
                (ROOT / relative).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
            )
            scripts = [
                step["run"]
                for job in document["jobs"].values()
                for step in job["steps"]
                if "Reconcile" in step.get("name", "") and "issue" in step["name"]
            ]
            self.assertEqual(len(scripts), 1, relative)
            for api_exit, numbers, clean, mutation in (
                (23, "", False, ""),
                (23, "41", False, ""),
                (23, "", True, ""),
                (0, "", False, "create"),
                (0, "41", False, "edit"),
                (0, "", True, ""),
                (0, "41", True, "close"),
                (0, "41\n42", False, ""),
            ):
                with self.subTest(
                    workflow=relative, api_exit=api_exit, numbers=numbers, clean=clean
                ):
                    with tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        self.install_body_preflight(root)
                        for name, marker in (
                            ("freshness", "repo-scaffold-freshness-audit"),
                            (
                                "community-health",
                                "repo-scaffold-community-health-drift",
                            ),
                            ("official-docs", "repo-scaffold-official-docs-audit"),
                        ):
                            (root / f"{name}.md").write_text(
                                f"<!-- {marker} -->\n", encoding="utf-8"
                            )
                        environment = child_cli_environment(
                            {
                                "REPOSITORY": "synthetic/example",
                                "GITHUB_REPOSITORY": "synthetic/example",
                                "RUNNER_TEMP": ".",
                                "GITHUB_STEP_SUMMARY": "summary.md",
                                "RUN_URL": "https://example.test/run",
                                "CHECKER_EXIT": "0" if clean else "1",
                                "PYTHON_CANARY_RESULT": "success"
                                if clean
                                else "failure",
                                "TOOLCHAIN_CANARY_RESULT": "success",
                                "TEST_API_EXIT": str(api_exit),
                                "TEST_NUMBERS": numbers,
                            }
                        )
                        stub = """gh() {
  if [[ "$1" == api ]]; then
    if [[ -n "$TEST_NUMBERS" ]]; then printf '%s\\n' "${TEST_NUMBERS//$'\\n'/ }"; else printf 'none\\n'; fi
    return "$TEST_API_EXIT"
  fi
  printf 'MUTATION:%s\\n' "$2"
}
"""
                        result = subprocess.run(
                            [str(BASH), "--noprofile", "--norc", "-s"],
                            input=stub + scripts[0],
                            cwd=root,
                            env=environment,
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            timeout=15,
                            check=False,
                        )
                        mutations = [
                            line
                            for line in result.stdout.splitlines()
                            if line.startswith("MUTATION:")
                        ]
                        self.assertEqual(
                            mutations,
                            [f"MUTATION:{mutation}"] if mutation else [],
                            result.stderr,
                        )
                        if api_exit:
                            self.assertEqual(result.returncode, api_exit, result.stderr)
                        elif numbers == "41\n42":
                            self.assertNotEqual(result.returncode, 0)
                        elif clean:
                            self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(BASH, "requires Bash (Git Bash on Windows)")
    def test_community_health_rejects_unexpected_checker_status(self) -> None:
        relative = ".github/workflows/community-health.yml"
        document = yaml.load(
            (ROOT / relative).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
        )
        script = next(
            step["run"]
            for job in document["jobs"].values()
            for step in job["steps"]
            if "Reconcile" in step.get("name", "") and "issue" in step["name"]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.install_body_preflight(root)
            (root / "community-health.md").write_text(
                "<!-- repo-scaffold-community-health-drift -->\n", encoding="utf-8"
            )
            environment = child_cli_environment(
                {
                    "REPOSITORY": "synthetic/example",
                    "GITHUB_REPOSITORY": "synthetic/example",
                    "RUNNER_TEMP": ".",
                    "CHECKER_EXIT": "127",
                }
            )
            stub = """gh() {
  if [[ "$1" == api ]]; then printf 'none\\n'; return 0; fi
  printf 'MUTATION:%s\\n' "$2"
}
"""
            result = subprocess.run(
                [str(BASH), "--noprofile", "--norc", "-s"],
                input=stub + script,
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
                check=False,
            )

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("unexpected exit status", result.stderr)
        self.assertNotIn("MUTATION:", result.stdout)

    @unittest.skipUnless(BASH, "requires Bash (Git Bash on Windows)")
    def test_indeterminate_checker_status_fails_before_issue_mutation(self) -> None:
        cases = (
            (
                ".github/workflows/community-health.yml",
                "community-health.md",
                "repo-scaffold-community-health-drift",
            ),
            (
                ".github/workflows/freshness.yml",
                "freshness.md",
                "repo-scaffold-freshness-audit",
            ),
            (
                ".github/workflows/official-docs.yml",
                "official-docs.md",
                "repo-scaffold-official-docs-audit",
            ),
            (
                "skills/repo-scaffold/assets/workflows/community-health.yml",
                "community-health.md",
                "repo-scaffold-community-health-drift",
            ),
            (
                "skills/repo-scaffold/assets/workflows/freshness.yml",
                "freshness.md",
                "repo-scaffold-freshness-audit",
            ),
        )
        stub = """gh() {
  if [[ "$1" == api ]]; then printf 'none\\n'; return 0; fi
  printf 'MUTATION:%s\\n' "$2"
}
"""
        for relative, report_name, marker in cases:
            document = yaml.load(
                (ROOT / relative).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
            )
            script = next(
                step["run"]
                for job in document["jobs"].values()
                for step in job["steps"]
                if "Reconcile" in step.get("name", "") and "issue" in step["name"]
            )
            with self.subTest(workflow=relative):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    self.install_body_preflight(root)
                    (root / report_name).write_text(
                        f"<!-- {marker} -->\n", encoding="utf-8"
                    )
                    environment = child_cli_environment(
                        {
                            "REPOSITORY": "synthetic/example",
                            "GITHUB_REPOSITORY": "synthetic/example",
                            "RUNNER_TEMP": ".",
                            "CHECKER_EXIT": "2",
                        }
                    )
                    result = subprocess.run(
                        [str(BASH), "--noprofile", "--norc", "-s"],
                        input=stub + script,
                        cwd=root,
                        env=environment,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=15,
                        check=False,
                    )
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertIn("indeterminate", result.stderr)
                self.assertNotIn("MUTATION:", result.stdout)

    @unittest.skipUnless(BASH, "requires Bash (Git Bash on Windows)")
    def test_freshness_rejects_unexpected_checker_status(self) -> None:
        for relative in (
            ".github/workflows/freshness.yml",
            "skills/repo-scaffold/assets/workflows/freshness.yml",
        ):
            document = yaml.load(
                (ROOT / relative).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
            )
            script = next(
                step["run"]
                for job in document["jobs"].values()
                for step in job["steps"]
                if "Reconcile" in step.get("name", "") and "issue" in step["name"]
            )
            with self.subTest(workflow=relative):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    self.install_body_preflight(root)
                    (root / "freshness.md").write_text(
                        "<!-- repo-scaffold-freshness-audit -->\n",
                        encoding="utf-8",
                    )
                    environment = child_cli_environment(
                        {
                            "GITHUB_REPOSITORY": "synthetic/example",
                            "RUNNER_TEMP": ".",
                            "CHECKER_EXIT": "127",
                        }
                    )
                    stub = """gh() {
  if [[ "$1" == api ]]; then printf 'none\\n'; return 0; fi
  printf 'MUTATION:%s\\n' "$2"
}
"""
                    result = subprocess.run(
                        [str(BASH), "--noprofile", "--norc", "-s"],
                        input=stub + script,
                        cwd=root,
                        env=environment,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=15,
                        check=False,
                    )

                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    self.assertIn("unexpected exit status", result.stderr)
                    self.assertNotIn("MUTATION:", result.stdout)


class PullRequestBodyPaginationTests(unittest.TestCase):
    def _workflow_functions(self) -> tuple[str, str]:
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/pr-body-sync.yml").read_text(encoding="utf-8")
        )
        run = workflow["jobs"]["update"]["steps"][1]["run"]
        api_start = run.index("gh_api_to_file() {")
        api_end = run.index("\n}\n\n", api_start) + 2
        pages_start = run.index("collect_pages() {")
        pages_end = run.index("\n}\n\ncollect_pages ", pages_start) + 2
        return run[api_start:api_end], run[pages_start:pages_end]

    @unittest.skipUnless(BASH, "requires Bash (Git Bash on Windows)")
    def test_api_file_reader_rejects_oversize_before_writing(self) -> None:
        api_function, _ = self._workflow_functions()
        stub = "gh() { printf '12345'; }\n"
        script = (
            "set -euo pipefail\n"
            + stub
            + api_function
            + '\ngh_api_to_file "oversized.json" 4 "endpoint"\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = subprocess.run(
                [str(BASH), "--noprofile", "--norc", "-s"],
                input=script,
                cwd=root,
                env=child_cli_environment({"RUNNER_TEMP": "."}),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertIn("byte safety cap", result.stderr)
            self.assertFalse((root / "oversized.json").exists())

    @unittest.skipUnless(BASH, "requires Bash (Git Bash on Windows)")
    def test_body_sync_collects_exactly_three_thousand_files(self) -> None:
        api_function, pages_function = self._workflow_functions()
        stub = """gh() {
  local endpoint=""
  for argument in "$@"; do endpoint="$argument"; done
  local page="${endpoint##*page=}"
  cat "$FAKE_PAGES/$page.json"
}
"""
        script = (
            "set -euo pipefail\n"
            + stub
            + api_function
            + "\n"
            + pages_function
            + '\ncollect_pages "repos/synthetic/example/pulls/1/files" files files 30\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page_root = root / "pages"
            page_root.mkdir()
            for page in range(1, 31):
                rows = [
                    {"filename": f"src/file-{page}-{item}.py"} for item in range(100)
                ]
                (page_root / f"{page}.json").write_text(
                    json.dumps(rows), encoding="utf-8"
                )
            result = subprocess.run(
                [str(BASH), "--noprofile", "--norc", "-s"],
                input=script,
                cwd=root,
                env=child_cli_environment({"RUNNER_TEMP": ".", "FAKE_PAGES": "pages"}),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            pages = json.loads(
                (root / "pr-body-files.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(pages), 30)
            self.assertEqual(sum(len(page) for page in pages), 3000)

    @unittest.skipUnless(BASH, "requires Bash (Git Bash on Windows)")
    def test_body_sync_rejects_combined_pagination_evidence_over_the_byte_cap(
        self,
    ) -> None:
        api_function, pages_function = self._workflow_functions()
        stub = """gh() {
  local endpoint=""
  for argument in "$@"; do endpoint="$argument"; done
  local page="${endpoint##*page=}"
  cat "$FAKE_PAGES/$page.json"
}
"""
        script = (
            "set -euo pipefail\n"
            + stub
            + api_function
            + "\n"
            + pages_function
            + '\ncollect_pages "repos/synthetic/example/pulls/1/files" files files 30\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page_root = root / "pages"
            page_root.mkdir()
            for page in range(1, 31):
                rows = [{"filename": "\u5b57" * 2048} for _ in range(100)]
                (page_root / f"{page}.json").write_text(
                    json.dumps(rows, ensure_ascii=False), encoding="utf-8"
                )
            result = subprocess.run(
                [str(BASH), "--noprofile", "--norc", "-s"],
                input=script,
                cwd=root,
                env=child_cli_environment({"RUNNER_TEMP": ".", "FAKE_PAGES": "pages"}),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertIn("Combined paginated API evidence exceeds", result.stderr)
            self.assertFalse((root / "pr-body-files.json").exists())
