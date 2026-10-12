from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
RUN_INTEGRATION = os.environ.get("REPO_SCAFFOLD_MUTMUT_INTEGRATION") == "1"


@unittest.skipUnless(
    sys.platform.startswith("linux") and RUN_INTEGRATION,
    "requires the Linux mutation integration job",
)
class LinuxMutationRunnerIntegrationTests(unittest.TestCase):
    def source_definitions(self, path: Path, names: set[str]) -> str:
        """Copy bounded checked-in definitions, never downloaded generated code."""
        self.assertLess(path.stat().st_size, 2 * 1024 * 1024)
        source = path.read_text(encoding="utf-8")
        selected = {}
        for node in ast.parse(source).body:
            if isinstance(node, ast.FunctionDef) and node.name in names:
                selected[node.name] = ast.get_source_segment(source, node)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in names:
                        selected[target.id] = ast.get_source_segment(source, node)
        self.assertEqual(set(selected), names)
        self.assertTrue(all(value is not None for value in selected.values()))
        return "\n\n".join(str(selected[name]) for name in sorted(names)) + "\n"

    def run_command(
        self, root: Path, *arguments: str
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, *arguments],
            cwd=root,
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"command failed: {arguments!r}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        return result

    def test_real_mutmut_pool_reuses_prepared_source_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                "pyproject.toml": (
                    "[tool.mutmut]\n"
                    'source_paths = ["scripts"]\n'
                    'pytest_add_cli_args_test_selection = ["tests"]\n'
                    'pytest_add_cli_args = ["-p", "no:cacheprovider"]\n'
                    "mutate_only_covered_lines = false\n"
                ),
                "scripts/decision.py": (
                    "def is_positive(value: int) -> bool:\n    return value > 0\n"
                ),
                "tests/test_decision.py": (
                    "from scripts.decision import is_positive\n\n"
                    "def test_positive_boundary():\n"
                    "    assert is_positive(1)\n"
                    "    assert not is_positive(0)\n"
                ),
            }
            for relative, content in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            runner = str(PLUGIN_ROOT / "scripts" / "run_mutation_testing.py")
            preparer = str(PLUGIN_ROOT / "scripts" / "prepare_mutation_cache.py")
            runner_arguments = (
                runner,
                "--repository-root",
                str(root),
                "--max-children",
                "1",
            )

            self.run_command(root, *runner_arguments)
            self.run_command(root, preparer, "record", "--repository-root", str(root))
            self.run_command(root, preparer, "prepare", "--repository-root", str(root))

            marker = root / "mutants" / ".incremental-sources.json"
            self.assertEqual(
                json.loads(marker.read_text(encoding="utf-8"))["sources"],
                ["scripts/decision.py"],
            )
            generated = root / "mutants" / "scripts" / "decision.py"
            generated_before = generated.read_bytes()
            metadata = root / "mutants" / "scripts" / "decision.py.meta"
            metadata_before = metadata.read_bytes()
            prepared_exit_codes = json.loads(metadata_before)["exit_code_by_key"]
            self.assertTrue(prepared_exit_codes)
            self.assertTrue(
                set(prepared_exit_codes.values()).issubset({1, 3}),
                prepared_exit_codes,
            )

            second = self.run_command(root, *runner_arguments)

            self.assertIn("1 unmodified", second.stdout)
            self.assertEqual(generated.read_bytes(), generated_before)
            self.assertEqual(metadata.read_bytes(), metadata_before)
            self.assertFalse(marker.exists())

    def test_process_cached_bundled_code_does_not_mask_real_mutants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                "pyproject.toml": (
                    "[tool.mutmut]\n"
                    'source_paths = ["scripts", "skills/repo-scaffold/scripts"]\n'
                    'pytest_add_cli_args_test_selection = ["tests"]\n'
                    'pytest_add_cli_args = ["-p", "no:cacheprovider"]\n'
                ),
                "skills/repo-scaffold/scripts/decision.py": (
                    "def is_positive(value):\n    return value > 0\n"
                ),
                "scripts/decision.py": (
                    "import runpy\nfrom pathlib import Path\n"
                    "BUNDLED = runpy.run_path(\n"
                    "    str(Path(__file__).resolve().parents[1] / "
                    "'skills/repo-scaffold/scripts/decision.py'),\n"
                    "    run_name='skills.repo-scaffold.scripts.decision',\n)\n"
                    "def is_positive(value):\n"
                    "    return BUNDLED['is_positive'](value)\n"
                ),
                "tests/test_decision.py": (
                    "from scripts.decision import is_positive\n"
                    "def test_boundary():\n"
                    "    assert is_positive(1)\n"
                    "    assert not is_positive(0)\n"
                    "    assert not is_positive(-1)\n"
                ),
            }
            for relative, content in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            self.run_command(
                root,
                str(PLUGIN_ROOT / "scripts/run_mutation_testing.py"),
                "--max-children",
                "1",
            )
            metadata = list((root / "mutants").rglob("*.meta"))
            self.assertEqual(len(metadata), 2)
            for path in metadata:
                codes = json.loads(path.read_bytes())["exit_code_by_key"]
                self.assertTrue(codes, path)
                self.assertTrue(set(codes.values()).issubset({1, 3}), codes)

    def test_http_source_cache_does_not_mask_real_mutants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                "pyproject.toml": (
                    "[tool.mutmut]\n"
                    'source_paths = ["scripts"]\n'
                    'pytest_add_cli_args_test_selection = ["tests"]\n'
                    'pytest_add_cli_args = ["-p", "no:cacheprovider"]\n'
                ),
                "scripts/decision.py": (
                    "def is_positive(value):\n    return value > 0\n"
                ),
                "tests/http_loader.py": (
                    PLUGIN_ROOT / "tests/test_http_error_resource_ownership.py"
                ).read_text(encoding="utf-8"),
                "tests/test_decision.py": (
                    "from http_loader import load_source\n"
                    "decision = load_source('scripts/decision.py')\n"
                    "def test_boundary():\n"
                    "    assert load_source('scripts/decision.py') is decision\n"
                    "    assert decision.is_positive(1)\n"
                    "    assert not decision.is_positive(0)\n"
                    "    assert not decision.is_positive(-1)\n"
                ),
            }
            for relative, content in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            self.run_command(
                root,
                str(PLUGIN_ROOT / "scripts/run_mutation_testing.py"),
                "--max-children",
                "1",
            )
            metadata = json.loads(
                (root / "mutants/scripts/decision.py.meta").read_bytes()
            )
            codes = metadata["exit_code_by_key"]
            self.assertTrue(codes)
            self.assertTrue(set(codes.values()).issubset({1, 3}), codes)

    def test_token_read_budget_kills_real_index_reset_mutants(self) -> None:
        classifiers = self.source_definitions(
            PLUGIN_ROOT / "scripts/validate_repository.py",
            {
                "github_api_is_mutation",
                "GITHUB_API_METHOD_OPTIONS",
                "GITHUB_API_BODY_OPTIONS",
                "GITHUB_API_READ_ONLY_METHODS",
            },
        ) + self.source_definitions(
            PLUGIN_ROOT
            / "skills/repo-scaffold/scripts/workflow_installation_preflight.py",
            {
                "network_client_is_mutation",
                "executable_basename",
                "UNSAFE_NETWORK_EXECUTORS",
                "NETWORK_METHOD_OPTIONS",
                "NETWORK_BODY_OPTIONS",
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                "pyproject.toml": (
                    "[tool.mutmut]\n"
                    'source_paths = ["scripts"]\n'
                    'pytest_add_cli_args_test_selection = ["tests"]\n'
                    'pytest_add_cli_args = ["-p", "no:cacheprovider"]\n'
                ),
                "scripts/decision.py": ("import re\n\n" + classifiers),
                "tests/budget_helper.py": (
                    PLUGIN_ROOT / "tests/token_walk_budget.py"
                ).read_text(encoding="utf-8"),
                "tests/test_decision.py": (
                    "from scripts.decision import github_api_is_mutation, network_client_is_mutation\n"
                    "from budget_helper import guard_token_reads\n"
                    "classify = guard_token_reads(github_api_is_mutation)\n"
                    "def test_http_methods():\n"
                    "    assert not classify(['gh', 'api', 'endpoint', '-X', 'HEAD'], 0)\n"
                    "    assert classify(['gh', 'api', 'endpoint', '-X', 'POST'], 0)\n"
                    "    assert classify(['gh', 'api', 'endpoint', '--method'], 0)\n"
                    "    assert classify(['gh', 'api', 'endpoint', '-f', 'x=1'], 0)\n"
                    "    assert not classify(['gh', 'api', 'endpoint', '-f', 'x=1', '-X', 'GET'], 0)\n"
                    "def test_network_methods():\n"
                    "    network = guard_token_reads(network_client_is_mutation)\n"
                    "    assert not network(['curl', '--head', 'https://example.test'])\n"
                    "    assert network(['curl', '-X', 'POST', 'https://example.test'])\n"
                    "    assert not network(['curl', '-X', 'GET', 'https://example.test'])\n"
                ),
            }
            for relative, content in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            self.run_command(
                root,
                str(PLUGIN_ROOT / "scripts/run_mutation_testing.py"),
                "--max-children",
                "1",
            )
            generated = root / "mutants/scripts/decision.py"
            self.assertLess(generated.stat().st_size, 4 * 1024 * 1024)
            module = ast.parse(generated.read_text(encoding="utf-8"))
            resets = set()
            for function in module.body:
                if (
                    not isinstance(function, ast.FunctionDef)
                    or "__mutmut_" not in function.name
                ):
                    continue
                for loop in ast.walk(function):
                    if not isinstance(loop, ast.While):
                        continue
                    if any(
                        (
                            isinstance(statement, ast.Assign)
                            and any(
                                isinstance(target, ast.Name) and target.id == "index"
                                for target in statement.targets
                            )
                            and isinstance(statement.value, ast.Constant)
                            and statement.value.value == 1
                        )
                        or (
                            isinstance(statement, ast.AugAssign)
                            and isinstance(statement.target, ast.Name)
                            and statement.target.id == "index"
                            and isinstance(statement.op, ast.Sub)
                        )
                        for statement in ast.walk(loop)
                    ):
                        resets.add(function.name)
            self.assertTrue(resets)
            metadata = json.loads(
                (root / "mutants/scripts/decision.py.meta").read_bytes()
            )
            codes = metadata["exit_code_by_key"]
            selected = {
                key: code
                for key, code in codes.items()
                if key.rsplit(".", 1)[-1] in resets
            }
            self.assertEqual(len(selected), len(resets))
            self.assertTrue(all(code in {1, 3} for code in selected.values()), selected)

    def test_text_work_budget_kills_real_nonadvancing_comment_and_line_mutants(
        self,
    ) -> None:
        definitions = self.source_definitions(
            PLUGIN_ROOT / "scripts/validate_repository.py",
            {
                "freshness_issue_lookup_substitution_is_safe",
                "shell_command_segments",
                "SHELL_CONTROL_CHARACTERS",
            },
        ) + self.source_definitions(
            PLUGIN_ROOT / "skills/repo-scaffold/scripts/markdown_body_preflight.py",
            {
                "html_comment_start",
                "preceded_by_odd_backslashes",
                "strip_html_comments",
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                "pyproject.toml": (
                    "[tool.mutmut]\n"
                    'source_paths = ["scripts"]\n'
                    'pytest_add_cli_args_test_selection = ["tests"]\n'
                    'pytest_add_cli_args = ["-p", "no:cacheprovider"]\n'
                ),
                "scripts/decision.py": "import re\nimport shlex\n\n" + definitions,
                "tests/budget_helper.py": (
                    PLUGIN_ROOT / "tests/parser_work_budget.py"
                ).read_text(encoding="utf-8"),
                "tests/test_decision.py": (
                    "from scripts import decision\n"
                    "from budget_helper import guard_text_work\n"
                    "decision.html_comment_start = guard_text_work(decision.html_comment_start, input_name='line')\n"
                    "decision.strip_html_comments = guard_text_work(decision.strip_html_comments, input_name='line')\n"
                    "lookup = guard_text_work(decision.freshness_issue_lookup_substitution_is_safe, input_name='text')\n"
                    "def test_lookup_continuation():\n"
                    "    text = 'issue_numbers_output=$(\\ngh api endpoint ' + chr(92) + '\\n --method HEAD\\n)'\n"
                    "    assert lookup(text)\n"
                    "    assert not lookup('issue_numbers_output=$(\\nprintf extra\\ngh api endpoint\\n)')\n"
                    "def test_comment_offsets_and_removal():\n"
                    "    text = '<!--first--><!--second-->'\n"
                    "    assert decision.html_comment_start(text, 12, []) == 12\n"
                    "    assert decision.html_comment_start(text, 24, []) is None\n"
                    "    assert decision.html_comment_start(chr(92) + '<!--escaped-->', 0, []) is None\n"
                    "    assert decision.strip_html_comments(text, False, []) == ('', False, [])\n"
                    "    visible = '<!--first-->visible<!--second-->tail'\n"
                    "    assert decision.strip_html_comments(visible, False, []) == ('visibletail', False, [])\n"
                ),
            }
            for relative, content in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            self.run_command(
                root,
                str(PLUGIN_ROOT / "scripts/run_mutation_testing.py"),
                "--max-children",
                "1",
            )
            generated = root / "mutants/scripts/decision.py"
            self.assertLess(generated.stat().st_size, 8 * 1024 * 1024)
            functions = ast.parse(generated.read_text(encoding="utf-8")).body
            selected_names = set()
            for function in functions:
                if (
                    not isinstance(function, ast.FunctionDef)
                    or "__mutmut_" not in function.name
                    or function.name.endswith("__mutmut_orig")
                ):
                    continue
                statements = list(ast.walk(function))
                missing_find_offset = any(
                    name in function.name
                    for name in ("html_comment_start", "strip_html_comments")
                ) and any(
                    isinstance(statement, ast.Call)
                    and isinstance(statement.func, ast.Attribute)
                    and statement.func.attr == "find"
                    and (
                        len(statement.args) < 2
                        or (
                            isinstance(statement.args[1], ast.Constant)
                            and statement.args[1].value is None
                        )
                    )
                    for statement in statements
                )
                backward_line_cursor = (
                    "freshness_issue_lookup_substitution_is_safe" in function.name
                    and any(
                        (
                            isinstance(statement, ast.AugAssign)
                            and isinstance(statement.target, ast.Name)
                            and statement.target.id == "cursor"
                            and isinstance(statement.op, ast.Sub)
                        )
                        or (
                            isinstance(statement, ast.Assign)
                            and any(
                                isinstance(target, ast.Name) and target.id == "cursor"
                                for target in statement.targets
                            )
                            and isinstance(statement.value, ast.Constant)
                            and statement.value.value == 1
                        )
                        or (
                            isinstance(statement, ast.Assign)
                            and any(
                                isinstance(target, ast.Name) and target.id == "index"
                                for target in statement.targets
                            )
                            and isinstance(statement.value, ast.BinOp)
                            and isinstance(statement.value.op, ast.Sub)
                        )
                        for statement in statements
                    )
                )
                backward_comment_cursor = (
                    "strip_html_comments" in function.name
                    and any(
                        isinstance(statement, ast.Assign)
                        and any(
                            isinstance(target, ast.Name) and target.id == "cursor"
                            for target in statement.targets
                        )
                        and isinstance(statement.value, ast.BinOp)
                        and isinstance(statement.value.left, ast.Name)
                        and statement.value.left.id == "next_comment_start"
                        and isinstance(statement.value.op, ast.Sub)
                        for statement in statements
                    )
                )
                if (
                    missing_find_offset
                    or backward_line_cursor
                    or backward_comment_cursor
                ):
                    selected_names.add(function.name)
            self.assertGreaterEqual(len(selected_names), 8)
            metadata = json.loads(
                (root / "mutants/scripts/decision.py.meta").read_bytes()
            )
            selected = {
                key: code
                for key, code in metadata["exit_code_by_key"].items()
                if key.rsplit(".", 1)[-1] in selected_names
            }
            self.assertEqual(len(selected), len(selected_names))
            self.assertTrue(all(code in {1, 3} for code in selected.values()), selected)

    def test_real_shards_merge_with_only_metadata_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repository"
            files = {
                "pyproject.toml": (
                    "[tool.mutmut]\n"
                    'source_paths = ["scripts"]\n'
                    'pytest_add_cli_args_test_selection = ["tests"]\n'
                    'pytest_add_cli_args = ["-p", "no:cacheprovider"]\n'
                ),
                "scripts/decision.py": "def is_positive(value):\n    return value > 0\n",
                "tests/test_decision.py": (
                    "from scripts.decision import is_positive\n"
                    "def test_boundary():\n"
                    "    assert is_positive(1)\n"
                    "    assert not is_positive(0)\n"
                ),
            }
            for relative, content in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            runner = str(PLUGIN_ROOT / "scripts/run_mutation_testing.py")
            preparer = str(PLUGIN_ROOT / "scripts/prepare_mutation_cache.py")
            merger = str(PLUGIN_ROOT / "scripts/merge_mutation_shards.py")
            self.run_command(root, runner, "--max-children", "1", "--plan-shards", "2")
            self.run_command(root, preparer, "record")
            self.run_command(root, preparer, "prepare")
            for index in range(2):
                worker = Path(directory) / f"worker-{index}"
                shutil.copytree(
                    root, worker, ignore=shutil.ignore_patterns("mutation-shards")
                )
                with mock.patch.dict(
                    os.environ, {"REPO_SCAFFOLD_MUTATION_DIAGNOSTICS": "1"}
                ):
                    result = self.run_command(
                        worker,
                        runner,
                        "--max-children",
                        "1",
                        "--shard-index",
                        str(index),
                    )
                events = [
                    json.loads(line.split("mutation-worker-diagnostic ", 1)[1])
                    for line in result.stdout.splitlines()
                    if "mutation-worker-diagnostic " in line
                ]
                planned = json.loads(
                    (worker / "mutants/mutation-shards.json").read_bytes()
                )["shards"][index]
                for phase in ("submitted", "completed"):
                    self.assertEqual(
                        {
                            event["mutant"]
                            for event in events
                            if event["phase"] == phase
                        },
                        set(planned),
                    )
                self.assertTrue(
                    all(
                        type(event["pid"]) is int and event["pid"] > 0
                        for event in events
                        if event["phase"] == "submitted"
                    )
                )
                output = root / "mutation-shards" / f"mutation-shard-{index}"
                for metadata in (worker / "mutants").rglob("*.meta"):
                    destination = output / metadata.relative_to(worker / "mutants")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(metadata, destination)
            self.run_command(root, merger)
            metadata = json.loads(
                (root / "mutants/scripts/decision.py.meta").read_text(encoding="utf-8")
            )
            self.assertTrue(metadata["exit_code_by_key"])
            self.assertTrue(set(metadata["exit_code_by_key"].values()).issubset({1, 3}))
            self.assertEqual(
                set(metadata["durations_by_key"]), set(metadata["exit_code_by_key"])
            )
            self.run_command(root, preparer, "record")
            self.run_command(root, preparer, "prepare")
            metadata_path = root / "mutants/scripts/decision.py.meta"
            before_reuse = metadata_path.read_bytes()
            self.run_command(root, runner, "--max-children", "1", "--shard-index", "0")
            self.assertEqual(metadata_path.read_bytes(), before_reuse)
            self.assertFalse((root / "mutants/.incremental-sources.json").exists())


if __name__ == "__main__":
    unittest.main()
