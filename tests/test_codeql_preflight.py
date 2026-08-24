from __future__ import annotations

import importlib.util
import argparse
import json
import os
import re
import runpy
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import BinaryIO, Protocol, cast
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT / "skills" / "repo-scaffold" / "scripts" / "codeql_preflight.py"
)
SPEC = importlib.util.spec_from_file_location(
    "skills.repo-scaffold.scripts.codeql_preflight", SCRIPT_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load codeql_preflight.py")
codeql_preflight = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = codeql_preflight
SPEC.loader.exec_module(codeql_preflight)

VALIDATOR_PATH = PLUGIN_ROOT / "scripts" / "validate_workflows.py"
VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "scripts.validate_workflows", VALIDATOR_PATH
)
if VALIDATOR_SPEC is None or VALIDATOR_SPEC.loader is None:
    raise RuntimeError("Could not load validate_workflows.py")
validate_workflows = importlib.util.module_from_spec(VALIDATOR_SPEC)
sys.modules[VALIDATOR_SPEC.name] = validate_workflows
VALIDATOR_SPEC.loader.exec_module(validate_workflows)


class GitHubClientProtocol(Protocol):
    request_count: int
    response_bytes: int
    deadline: float

    def _run(self, endpoint: str, *, raw: bool = False) -> str: ...

    def json(self, endpoint: str) -> object: ...

    def raw(self, endpoint: str) -> str: ...


class ExecutableResolutionTests(unittest.TestCase):
    def test_module_reports_missing_pyyaml_without_a_coverage_exclusion(self) -> None:
        output = StringIO()
        with (
            mock.patch.dict(sys.modules, {"yaml": None}),
            redirect_stdout(output),
            self.assertRaises(SystemExit),
        ):
            runpy.run_path(str(SCRIPT_PATH), run_name="codeql_without_yaml")

        self.assertEqual(
            json.loads(output.getvalue())["error"],
            "PyYAML is required for structural workflow inspection.",
        )

    def test_resolver_ignores_repository_controlled_path_entry(self) -> None:
        executable_name = "probe"
        executable_filename = (
            f"{executable_name}.exe" if os.name == "nt" else executable_name
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            trusted_tools = root / "trusted-tools"
            repository.mkdir()
            trusted_tools.mkdir()
            repository_executable = repository / executable_filename
            trusted_executable = trusted_tools / executable_filename
            for executable in (repository_executable, trusted_executable):
                executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                executable.chmod(0o755)

            path_value = os.pathsep.join((str(repository), str(trusted_tools)))
            with mock.patch.dict(os.environ, {"PATH": path_value}):
                for resolver in (
                    codeql_preflight.resolve_path_executable,
                    validate_workflows.resolve_path_executable,
                ):
                    with self.subTest(module=resolver.__module__):
                        resolved = resolver(executable_name, forbidden_root=repository)
                        self.assertIsNotNone(resolved)
                        self.assertEqual(
                            Path(cast(str, resolved)).resolve(),
                            trusted_executable.resolve(),
                        )

    def test_resolver_skips_empty_relative_missing_and_unresolvable_entries(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            broken_directory = root / "broken"
            trusted = root / "trusted"
            repository.mkdir()
            broken_directory.mkdir()
            trusted.mkdir()
            broken = broken_directory / "gh"
            executable = trusted / "gh"
            executable.touch()
            original_resolve = Path.resolve

            def resolve_path(path: Path, strict: bool = False) -> Path:
                if path == broken:
                    raise RuntimeError("unresolvable")
                return original_resolve(path, strict=strict)

            def find_tool(_name: str, *, path: str) -> str | None:
                if path == str(broken_directory):
                    return str(broken)
                if path == str(trusted):
                    return str(executable)
                return None

            path_value = os.pathsep.join(
                [
                    "",
                    "relative",
                    str(root / "missing"),
                    str(broken_directory),
                    str(trusted),
                ]
            )
            with (
                mock.patch.dict(os.environ, {"PATH": path_value}),
                mock.patch.object(
                    codeql_preflight.shutil, "which", side_effect=find_tool
                ),
                mock.patch.object(codeql_preflight.Path, "resolve", resolve_path),
            ):
                self.assertEqual(
                    codeql_preflight.resolve_path_executable(
                        "gh", forbidden_root=repository
                    ),
                    str(executable.resolve()),
                )

            with mock.patch.dict(os.environ, {"PATH": "relative"}):
                self.assertIsNone(
                    codeql_preflight.resolve_path_executable(
                        "gh", forbidden_root=repository
                    )
                )


class FlatResolver:
    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, call: str, context: object) -> object:
        self.calls += 1
        return codeql_preflight.WorkflowNode(
            ("flat", call),
            call,
            context,
            codeql_preflight.WorkflowSignals(False, ()),
        )


class FailingResolver:
    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, call: str, context: object) -> object:
        del call, context
        self.calls += 1
        raise codeql_preflight.InspectionError("unresolvable")


class ChainResolver:
    def __init__(self, last: int) -> None:
        self.last = last

    def resolve(self, call: str, context: object) -> object:
        index = int(call)
        nested = () if index == self.last else (str(index + 1),)
        return codeql_preflight.WorkflowNode(
            ("chain", call),
            call,
            context,
            codeql_preflight.WorkflowSignals(False, nested),
        )


class GraphResolver:
    def __init__(self, graph: dict[str, tuple[str, tuple[str, ...]]]) -> None:
        self.graph = graph

    def resolve(self, call: str, context: object) -> object:
        identity, nested = self.graph[call]
        return codeql_preflight.WorkflowNode(
            ("graph", identity),
            identity,
            context,
            codeql_preflight.WorkflowSignals(False, nested),
        )


class FanInResolver:
    def resolve(self, call: str, context: object) -> object:
        if call.startswith("parent-"):
            parent_context = codeql_preflight.WorkflowContext(
                "remote", "octo", call, "a" * 40
            )
            return codeql_preflight.WorkflowNode(
                ("parent", call),
                call,
                parent_context,
                codeql_preflight.WorkflowSignals(False, ("shared",)),
            )
        if call == "shared":
            return codeql_preflight.WorkflowNode(
                ("shared",),
                "shared",
                context,
                codeql_preflight.WorkflowSignals(False, ()),
            )
        raise AssertionError(f"Unexpected call: {call}")


class WorkflowParserTests(unittest.TestCase):
    def assert_adversarial_regex_probe_completes(self, case: str) -> None:
        probe = r"""
import runpy
import sys
import time

module = runpy.run_path(sys.argv[1])
started = time.perf_counter()
if sys.argv[2] == "env":
    payload = "\n_=" + ('"" _=' * 128) + "!"
    module["CODEQL_CLI"].search(payload)
elif sys.argv[2] == "alias":
    payload = 'Set-Alias -Name "' + ('`!' * 128)
    module["_powershell_alias_definition"](payload)
else:
    raise AssertionError(f"Unknown probe: {sys.argv[2]}")
elapsed = time.perf_counter() - started
if elapsed >= 1.0:
    raise AssertionError(f"Regex probe took {elapsed:.3f} seconds")
"""
        environment = os.environ.copy()
        environment.pop("MUTANT_UNDER_TEST", None)
        subprocess.run(
            [sys.executable, "-c", probe, str(SCRIPT_PATH), case],
            check=True,
            capture_output=True,
            env=environment,
            # Mutmut instruments the whole module, so its import may be slow even
            # though the regex operation itself is bounded inside the child.
            timeout=30,
        )

    def test_direct_codeql_regex_handles_adversarial_assignments(self) -> None:
        self.assert_adversarial_regex_probe_completes("env")

    def test_powershell_alias_regex_handles_adversarial_quotes(self) -> None:
        self.assert_adversarial_regex_probe_completes("alias")

    def test_deeply_nested_yaml_fails_closed_without_recursion_traceback(self) -> None:
        nesting = 500
        text = (
            "jobs: {test: {runs-on: ubuntu-latest, steps: [{run: echo ok}], "
            "extra: " + "[" * nesting + "]" * nesting + "}}"
        )
        with self.assertRaisesRegex(
            codeql_preflight.InspectionError, "Could not parse workflow"
        ):
            codeql_preflight.parse_workflow(text, "deeply-nested")

    def test_detects_structural_action_and_cli_forms(self) -> None:
        fixtures = {
            "block": """
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: github/codeql-action/init@v4
""",
            "flow": (
                "jobs: {scan: {runs-on: ubuntu-latest, "
                "steps: [{uses: github/codeql-action/init@v4}]}}"
            ),
            "quoted": """
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - 'uses': github/codeql-action/init@v4
""",
            "inline_cli": (
                "jobs: {scan: {runs-on: ubuntu-latest, "
                "steps: [{run: codeql database create db}]}}"
            ),
            "conditional_cli": (
                "jobs: {scan: {runs-on: ubuntu-latest, steps: "
                "[{run: 'if test -n \"$X\"; then codeql database create db; fi'}]}}"
            ),
            "environment_cli": (
                "jobs: {scan: {runs-on: ubuntu-latest, steps: "
                "[{run: 'env CODEQL_RAM=4096 codeql database create db'}]}}"
            ),
            "variable_cli": (
                "jobs: {scan: {runs-on: ubuntu-latest, steps: "
                "[{run: '\"$CODEQL\" database create db'}]}}"
            ),
            "quoted_path_cli": (
                "jobs: {scan: {runs-on: ubuntu-latest, steps: "
                "[{run: '\"/opt/CodeQL Bundle/codeql\" database create db'}]}}"
            ),
        }
        for name, text in fixtures.items():
            with self.subTest(name=name):
                self.assertTrue(
                    codeql_preflight.parse_workflow(text, name).has_advanced_setup
                )

    def test_detects_codeql_after_concatenated_assignment_value(self) -> None:
        command = 'SCAN=prefix"quoted suffix" codeql database create db'
        self.assertTrue(codeql_preflight.contains_codeql_cli(command, "bash"))

    def test_ignores_codeql_text_outside_executable_steps(self) -> None:
        for name, prefix in {
            "env": "env:\n  EXAMPLE: |\n    codeql database create db\n",
            "documentation": "description: |\n  codeql database create db\n",
        }.items():
            text = prefix + (
                "jobs: {test: {runs-on: ubuntu-latest, steps: [{run: echo ok}]}}"
            )
            with self.subTest(name=name):
                self.assertFalse(
                    codeql_preflight.parse_workflow(text, name).has_advanced_setup
                )

    def test_ignores_codeql_cli_inside_shell_heredoc(self) -> None:
        text = """
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: |
          cat >example.sh <<'EOF'
          codeql database create db
          EOF
          echo ok
"""
        self.assertFalse(
            codeql_preflight.parse_workflow(text, "heredoc").has_advanced_setup
        )

    def test_quoted_or_commented_heredoc_marker_does_not_hide_codeql(self) -> None:
        fixtures = {
            "quoted": 'echo "example <<EOF"\ncodeql database create db\n',
            "commented": "# docs: <<EOF\ncodeql database create db\n",
        }
        for name, run in fixtures.items():
            workflow = (
                "jobs:\n  scan:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - run: |\n"
                + "".join(f"          {line}\n" for line in run.splitlines())
            )
            with self.subTest(name=name):
                self.assertTrue(
                    codeql_preflight.parse_workflow(workflow, name).has_advanced_setup
                )

    def test_escaped_quote_does_not_hide_codeql(self) -> None:
        text = """
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - run: |
          echo \\'
          codeql database create db
          echo \\"
"""
        self.assertTrue(
            codeql_preflight.parse_workflow(text, "escaped-quotes").has_advanced_setup
        )

    def test_detects_codeql_after_real_shell_heredoc(self) -> None:
        text = """
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - run: |
          cat >example.sh <<EOF
          codeql database create inert-db
          EOF
          codeql database create real-db
"""
        self.assertTrue(
            codeql_preflight.parse_workflow(text, "after-heredoc").has_advanced_setup
        )

    def test_rejects_unterminated_shell_heredoc(self) -> None:
        text = """
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - run: |
          cat >example.sh <<EOF
          echo incomplete
"""
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "not terminated"):
            codeql_preflight.parse_workflow(text, "unterminated-heredoc")

    def test_ignores_codeql_inside_bash_multiline_string(self) -> None:
        text = """
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - run: |
          printf '%s' '
          codeql database create db
          '
"""
        self.assertFalse(
            codeql_preflight.parse_workflow(text, "bash-string").has_advanced_setup
        )

    def test_detects_codeql_command_substitution_inside_bash_double_quote(self) -> None:
        text = """
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - run: |
          value="prefix
          $(codeql database create db)
          suffix"
"""
        self.assertTrue(
            codeql_preflight.parse_workflow(
                text, "bash-substitution"
            ).has_advanced_setup
        )

    def test_detects_codeql_in_dynamic_bash_execution(self) -> None:
        fixtures = {
            "shell-heredoc": "bash <<'EOF'\ncodeql database create db\nEOF",
            "exec-shell-heredoc": "exec bash <<'EOF'\ncodeql database create db\nEOF",
            "command-option-shell-heredoc": (
                "command -- bash <<'EOF'\ncodeql database create db\nEOF"
            ),
            "exec-option-shell-heredoc": (
                "exec -a scan bash <<'EOF'\ncodeql database create db\nEOF"
            ),
            "nohup-shell-heredoc": (
                "nohup bash <<'EOF'\ncodeql database create db\nEOF"
            ),
            "sudo-shell-heredoc": "sudo bash <<'EOF'\ncodeql database create db\nEOF",
            "time-shell-heredoc": "time -p bash <<'EOF'\ncodeql database create db\nEOF",
            "env-option-shell-heredoc": (
                "env -u UNUSED bash <<'EOF'\ncodeql database create db\nEOF"
            ),
            "sudo-option-shell-heredoc": (
                "sudo -u root bash <<'EOF'\ncodeql database create db\nEOF"
            ),
            "time-option-shell-heredoc": (
                "time -f '%e' bash <<'EOF'\ncodeql database create db\nEOF"
            ),
            "brace-group": "{ codeql database create db; }",
            "exec-cli": "exec codeql database create db",
            "env-option-cli": "env -u UNUSED codeql database create db",
            "sudo-option-cli": "sudo -u root codeql database create db",
            "time-cli": "/usr/bin/time codeql database create db",
            "timeout-cli": "timeout 30 codeql database create db",
            "bash-c-single": "bash -c 'codeql database create db'",
            "bash-c-double": 'bash -c "codeql database create db"',
            "bash-c-end-options": "bash -c -- 'codeql database create db'",
            "wrapped-bash-c": "env -u UNUSED sudo -u root bash -lc 'codeql database create db'",
            "eval-multiline": "eval '\ncodeql database create db\n'",
            "eval-first-line": "eval 'codeql database create db\necho done\n'",
            "eval-inline": "eval 'codeql database create db'",
            "eval-unquoted": "eval codeql database create db",
            "source-here-string": ("source /dev/stdin <<< 'codeql database create db'"),
            "dot-source-here-string": (". /dev/stdin <<< 'codeql database create db'"),
            "env-split-string": "env -S 'codeql database create db'",
            "xargs-cli": "printf '%s\\n' db | xargs codeql database analyze",
            "xargs-separated-option": (
                "printf '%s\\n' db | xargs -n 1 codeql database analyze"
            ),
            "xargs-attached-option": (
                "printf '%s\\n' db | xargs -n1 codeql database analyze"
            ),
            "xargs-end-options": (
                "printf '%s\\n' db | xargs -- codeql database analyze"
            ),
            "find-exec-cli": "find . -exec codeql database analyze db {} \\;",
            "backtick-substitution": "result=`codeql database create db`",
            "array-command-substitution": (
                'values=($("/opt/CodeQL Bundle/codeql" database create db))'
            ),
            "array-process-substitution": (
                'values=(<("/opt/CodeQL Bundle/codeql" database create db))'
            ),
            "array-backtick-substitution": (
                'values=(`"/opt/CodeQL Bundle/codeql" database create db`)'
            ),
        }
        for name, run in fixtures.items():
            workflow = (
                "jobs:\n  scan:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - shell: bash\n"
                "        run: |\n"
                + "".join(f"          {line}\n" for line in run.splitlines())
            )
            with self.subTest(name=name):
                self.assertTrue(
                    codeql_preflight.parse_workflow(workflow, name).has_advanced_setup
                )

    def test_detects_codeql_through_bash_wrappers_and_nested_launchers(self) -> None:
        fixtures = {
            "coprocess": "coproc codeql database create db",
            "timed-function": "scan() { codeql database create db; }\ntime scan",
            "coprocess-function": (
                "scan() { codeql database create db; }\ncoproc scan"
            ),
            "xargs-shell": ("printf x | xargs sh -c 'codeql database create db'"),
            "find-shell": ("find . -exec sh -c 'codeql database create db' \\;"),
        }
        for name, command in fixtures.items():
            with self.subTest(name=name):
                self.assertTrue(codeql_preflight.contains_codeql_cli(command, "bash"))

    def test_external_bash_wrappers_cannot_invoke_unexported_functions(self) -> None:
        for name, invocation in {
            "command": "command scan",
            "environment": "env MODE=test scan",
            "exec": "exec scan",
            "nice": "nice -n 5 scan",
            "nohup": "nohup scan",
            "standard-buffer": "stdbuf -oL scan",
            "sudo": "sudo scan",
            "timeout": "timeout 30 scan",
        }.items():
            command = "scan() { codeql database create db; }\n" + invocation
            with self.subTest(name=name):
                self.assertFalse(codeql_preflight.contains_codeql_cli(command, "bash"))

    def test_nonliteral_dynamic_bash_execution_fails_closed(self) -> None:
        fixtures = {
            "shell-variable": "cmd='codeql database create db'\nbash -c \"$cmd\"",
            "eval-variable": "cmd='codeql database create db'\neval \"$cmd\"",
            "shell-missing-payload": "bash -c",
            "pipeline-to-shell": ("printf '%s\\n' 'codeql database create db' | bash"),
            "pipeline-to-source": (
                "printf '%s\\n' 'codeql database create db' | source /dev/stdin"
            ),
            "process-substitution-to-shell": (
                "bash < <(printf '%s\\n' 'codeql database create db')"
            ),
            "process-substitution-to-source": (
                "source <(printf '%s\\n' 'codeql database create db')"
            ),
            "variable-executable": ('tool=codeql\n"$tool" database create db'),
            "variable-function": (
                'scan() { codeql database create db; }\nfn=scan\n"$fn"'
            ),
            "trap-variable": (
                'scan() { codeql database create db; }\nfn=scan\ntrap "$fn" EXIT'
            ),
        }
        for name, run in fixtures.items():
            workflow = (
                "jobs:\n  scan:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - shell: bash\n"
                "        run: |\n"
                + "".join(f"          {line}\n" for line in run.splitlines())
            )
            with self.subTest(name=name):
                with self.assertRaisesRegex(
                    codeql_preflight.InspectionError, "dynamic command"
                ):
                    codeql_preflight.parse_workflow(workflow, name)

    def test_literal_dynamic_bash_execution_without_codeql_is_inspected(self) -> None:
        text = """
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - shell: bash
        run: |
          bash -c 'printf ok'
          eval 'printf done'
"""
        self.assertFalse(
            codeql_preflight.parse_workflow(
                text, "literal-dynamic-bash"
            ).has_advanced_setup
        )

    def test_bash_here_string_is_not_misparsed_as_a_heredoc(self) -> None:
        harmless = """
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - shell: bash
        run: cat <<< 'harmless literal'
"""
        codeql = """
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - shell: bash
        run: bash <<< 'codeql database create db'
"""
        self.assertFalse(
            codeql_preflight.parse_workflow(
                harmless, "harmless-bash-here-string"
            ).has_advanced_setup
        )
        self.assertTrue(
            codeql_preflight.parse_workflow(
                codeql, "executed-bash-here-string"
            ).has_advanced_setup
        )
        self.assertTrue(
            codeql_preflight.contains_codeql_cli(
                "bash <<<'codeql database create db'", "bash"
            )
        )

    def test_bash_arithmetic_shift_is_not_misparsed_as_a_heredoc(self) -> None:
        for name, run in {
            "expansion": "echo $((1 << 2))",
            "command": '(( value = 1 << 2 ))\necho "$value"',
            "multiline": '(( value =\n  1 << 2\n))\necho "$value"',
        }.items():
            workflow = (
                "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - shell: bash\n"
                "        run: |\n"
                + "".join(f"          {line}\n" for line in run.splitlines())
            )
            with self.subTest(name=name):
                self.assertFalse(
                    codeql_preflight.parse_workflow(
                        workflow, f"bash-arithmetic-{name}"
                    ).has_advanced_setup
                )

    def test_dynamic_command_nesting_is_bounded(self) -> None:
        with self.assertRaisesRegex(
            codeql_preflight.InspectionError, "nesting exceeds"
        ):
            codeql_preflight.contains_codeql_cli(
                "eval " * (codeql_preflight.MAX_DYNAMIC_EXECUTION_DEPTH + 2)
                + "printf ok",
                "bash",
            )
        with self.assertRaisesRegex(
            codeql_preflight.InspectionError, "nesting exceeds"
        ):
            codeql_preflight.contains_codeql_cli(
                "xargs " * (codeql_preflight.MAX_DYNAMIC_EXECUTION_DEPTH + 2)
                + "printf ok",
                "bash",
            )

    def test_ignores_uninvoked_bash_function_but_detects_invocation(self) -> None:
        definition = "scan()\n{\n  codeql database create db\n}"
        for name, (suffix, expected) in {
            "uninvoked": ("", False),
            "invoked": ("\nscan", True),
        }.items():
            run = definition + suffix
            workflow = (
                "jobs:\n  scan:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - shell: bash\n"
                "        run: |\n"
                + "".join(f"          {line}\n" for line in run.splitlines())
            )
            with self.subTest(name=name):
                self.assertEqual(
                    codeql_preflight.parse_workflow(
                        workflow, f"bash-function-{name}"
                    ).has_advanced_setup,
                    expected,
                )

    def test_detects_bash_function_invoked_by_literal_eval(self) -> None:
        text = """
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - shell: bash
        run: |
          scan()
          {
            codeql database create db
          }
          eval scan
"""
        self.assertTrue(
            codeql_preflight.parse_workflow(
                text, "bash-function-literal-eval"
            ).has_advanced_setup
        )

    def test_detects_exported_bash_function_invoked_by_nested_shell(self) -> None:
        command = """
scan() {
  codeql database create db
}
export -f scan
bash -c scan
"""
        self.assertTrue(codeql_preflight.contains_codeql_cli(command, "bash"))

    def test_detects_bash_function_invoked_by_exit_trap(self) -> None:
        command = """
scan() {
  codeql database create db
}
trap scan EXIT
"""
        self.assertTrue(codeql_preflight.contains_codeql_cli(command, "bash"))

    def test_detects_codeql_bash_alias_invoked_by_literal_eval(self) -> None:
        command = """
shopt -s expand_aliases
alias scan='codeql database create db'
eval scan
"""
        self.assertTrue(codeql_preflight.contains_codeql_cli(command, "bash"))

    def test_bash_alias_detection_avoids_inert_aliases(self) -> None:
        fixtures = {
            "uninvoked": (
                "shopt -s expand_aliases\nalias scan='codeql database create db'"
            ),
            "expansion-disabled": ("alias scan='codeql database create db'\neval scan"),
            "harmless": ("shopt -s expand_aliases\nalias scan='printf ok'\neval scan"),
            "same-parse-unit": (
                "shopt -s expand_aliases; alias scan='codeql database create db'; scan"
            ),
        }
        for name, command in fixtures.items():
            with self.subTest(name=name):
                self.assertFalse(codeql_preflight.contains_codeql_cli(command, "bash"))

    def test_detects_transitively_invoked_bash_function(self) -> None:
        text = """
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - shell: bash
        run: |
          scan() {
            codeql database create db
          }
          wrapper() {
            scan
          }
          wrapper
"""
        self.assertTrue(
            codeql_preflight.parse_workflow(
                text, "transitive-bash-function"
            ).has_advanced_setup
        )

    def test_detects_bash_function_invoked_through_alias(self) -> None:
        command = """
InvokeScan() {
  codeql database create db
}
shopt -s expand_aliases
alias scan=InvokeScan
eval scan
"""
        self.assertTrue(codeql_preflight.contains_codeql_cli(command, "bash"))

    def test_detects_bash_alias_invoked_inside_reachable_function(self) -> None:
        command = """
InvokeScan() {
  codeql database create db
}
shopt -s expand_aliases
alias scan=InvokeScan
wrapper() {
  scan
}
wrapper
"""
        self.assertTrue(codeql_preflight.contains_codeql_cli(command, "bash"))

    def test_ignores_nested_uninvoked_bash_function(self) -> None:
        text = """
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - shell: bash
        run: |
          outer() {
            inner() {
              codeql database create db
            }
          }
          outer
"""
        self.assertFalse(
            codeql_preflight.parse_workflow(
                text, "nested-uninvoked-bash-function"
            ).has_advanced_setup
        )

    def test_detects_nested_invoked_bash_function(self) -> None:
        text = """
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - shell: bash
        run: |
          outer() {
            inner() {
              codeql database create db
            }
            inner
          }
          outer
"""
        self.assertTrue(
            codeql_preflight.parse_workflow(
                text, "nested-invoked-bash-function"
            ).has_advanced_setup
        )

    def test_shell_c_after_script_name_is_not_executed_as_a_command_string(
        self,
    ) -> None:
        for name, run in {
            "script-positional": "bash noop.sh -c 'codeql database create db'",
            "command-argument": "bash -c 'printf ok' 'codeql database create db'",
        }.items():
            text = f"""
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - shell: bash
        run: {run}
"""
            with self.subTest(name=name):
                self.assertFalse(
                    codeql_preflight.parse_workflow(text, name).has_advanced_setup
                )

    def test_command_substitution_ignores_parentheses_inside_quotes(self) -> None:
        text = """
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - shell: bash
        run: |
          value="$(
            printf '%s' ')'
            codeql database create db
          )"
"""
        self.assertTrue(
            codeql_preflight.parse_workflow(
                text, "bash-substitution-quoted-parenthesis"
            ).has_advanced_setup
        )

    def test_ignores_codeql_in_inert_bash_strings_and_comments(self) -> None:
        fixtures = {
            "comment": "# codeql database create db",
            "trailing-comment": "printf ok # codeql database create db",
            "single-string": "printf '%s' 'codeql database create db'",
            "awk-program": "awk '{ codeql database create db }' input.txt",
            "double-string": "printf '%s' \"\ncodeql database create db\n\"",
            "non-command-eval-word": "echo eval '\ncodeql database create db\n'",
            "command-inspection": "command -v codeql database create db",
            "echo-wrapper-words": "echo exec codeql database create db",
            "shell-c-as-data": "printf '%s' \"bash -c 'codeql database create db'\"",
            "array-literal": 'values=("\ncodeql database create db\n")',
            "append-array-literal": 'values+=("\ncodeql database create db\n")',
            "multiline-array-literal": 'values=(\n  "\ncodeql database create db\n"\n)',
            "unquoted-array-literal": "values=(codeql database create db)",
            "multiline-unquoted-array-literal": (
                "values=(\n  codeql database create db\n)"
            ),
        }
        for name, run in fixtures.items():
            text = (
                "jobs:\n  scan:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - shell: bash\n"
                "        run: |\n"
                + "".join(f"          {line}\n" for line in run.splitlines())
            )
            with self.subTest(name=name):
                self.assertFalse(
                    codeql_preflight.parse_workflow(text, name).has_advanced_setup
                )

    def test_ignores_codeql_inside_powershell_here_strings(self) -> None:
        for quote in ("'", '"'):
            text = f"""
jobs:
  scan:
    runs-on: windows-latest
    steps:
      - shell: pwsh
        run: |
          @{quote}
          codeql database create db
          {quote}@ | Set-Content example.ps1
"""
            with self.subTest(quote=quote):
                self.assertFalse(
                    codeql_preflight.parse_workflow(
                        text, "pwsh-here-string"
                    ).has_advanced_setup
                )

    def test_powershell_backslash_does_not_escape_a_string_delimiter(self) -> None:
        text = r"""
jobs:
  scan:
    runs-on: windows-latest
    steps:
      - shell: pwsh
        run: |
          $path="C:\"; $value=@'
          codeql database create db
          '@
          Write-Output $path $value
"""
        self.assertFalse(
            codeql_preflight.parse_workflow(
                text, "pwsh-backslash-before-here-string"
            ).has_advanced_setup
        )

    def test_detects_codeql_subexpression_inside_powershell_here_string(self) -> None:
        text = """
jobs:
  scan:
    runs-on: windows-latest
    steps:
      - shell: pwsh
        run: |
          @"
          $(
            codeql database create db
          )
          "@
"""
        self.assertTrue(
            codeql_preflight.parse_workflow(
                text, "pwsh-subexpression"
            ).has_advanced_setup
        )

    def test_detects_codeql_here_string_piped_to_invoke_expression(self) -> None:
        text = """
jobs:
  scan:
    runs-on: windows-latest
    steps:
      - shell: pwsh
        run: |
          @'
          codeql database create db
          '@ | Invoke-Expression
"""
        self.assertTrue(
            codeql_preflight.parse_workflow(
                text, "pwsh-invoke-expression"
            ).has_advanced_setup
        )

    def test_detects_codeql_here_string_passed_to_invoke_expression_command(
        self,
    ) -> None:
        text = """
jobs:
  scan:
    runs-on: windows-latest
    steps:
      - shell: pwsh
        run: |
          Invoke-Expression -Command @'
          codeql database create db
          '@
"""
        self.assertTrue(
            codeql_preflight.parse_workflow(
                text, "pwsh-invoke-expression-command"
            ).has_advanced_setup
        )

    def test_detects_codeql_started_as_a_powershell_process(self) -> None:
        fixtures = {
            "command-name": "Start-Process codeql -ArgumentList 'database create db' -Wait",
            "quoted-command-name": (
                "Start-Process 'codeql' -ArgumentList 'database', 'create', 'db'"
            ),
            "quoted-file-path": (
                "Start-Process -FilePath 'C:\\CodeQL Bundle\\codeql.exe' "
                "-ArgumentList 'database', 'create', 'db' -Wait"
            ),
            "alias-with-continuation": (
                "saps `\n  codeql `\n  -ArgumentList 'database create db' -Wait"
            ),
            "after-separator": (
                "Write-Output ready; Start-Process codeql "
                "-ArgumentList 'database create db' -Wait"
            ),
            "call-operator": (
                "& Start-Process codeql -ArgumentList 'database create db' -Wait"
            ),
        }
        for name, run in fixtures.items():
            text = (
                "jobs:\n  scan:\n    runs-on: windows-latest\n    steps:\n"
                "      - shell: pwsh\n"
                "        run: |\n"
                + "".join(f"          {line}\n" for line in run.splitlines())
            )
            with self.subTest(name=name):
                self.assertTrue(
                    codeql_preflight.parse_workflow(text, name).has_advanced_setup
                )

    def test_detects_codeql_in_dynamic_powershell_execution(self) -> None:
        fixtures = {
            "invoke-expression-single": (
                "Invoke-Expression 'codeql database create db'"
            ),
            "invoke-expression-double": 'iex "codeql database create db"',
            "scriptblock": "& { codeql database create db }",
            "quoted-command-path": (
                "& 'C:\\CodeQL Bundle\\codeql.exe' database create db"
            ),
            "nested-pwsh": "pwsh -Command 'codeql database create db'",
            "nested-windows-powershell": (
                "powershell.exe -NoProfile -Command 'codeql database create db'"
            ),
            "scriptblock-create": (
                "[scriptblock]::Create('codeql database create db').Invoke()"
            ),
            "dot-source-scriptblock-create": (
                ". ([scriptblock]::Create('codeql database create db'))"
            ),
            "call-scriptblock-create": (
                "& ([scriptblock]::Create('codeql database create db'))"
            ),
        }
        for name, run in fixtures.items():
            text = (
                "jobs:\n  scan:\n    runs-on: windows-latest\n    steps:\n"
                "      - shell: pwsh\n"
                "        run: |\n"
                + "".join(f"          {line}\n" for line in run.splitlines())
            )
            with self.subTest(name=name):
                self.assertTrue(
                    codeql_preflight.parse_workflow(text, name).has_advanced_setup
                )

    def test_nonliteral_dynamic_powershell_execution_fails_closed(self) -> None:
        fixtures = {
            "invoke-expression-variable": (
                "$cmd='codeql database create db'\nInvoke-Expression $cmd"
            ),
            "nested-shell-variable": (
                "$cmd='codeql database create db'\npwsh -Command $cmd"
            ),
            "scriptblock-variable": (
                "$cmd='codeql database create db'\n[scriptblock]::Create($cmd).Invoke()"
            ),
            "pipeline-variable": (
                "$cmd='codeql database create db'\n$cmd | Invoke-Expression"
            ),
            "invoke-expression-concatenation": (
                "$cmd='codeql database create db'\n"
                "Invoke-Expression ('Write-Output ready; ' + $cmd)"
            ),
            "scriptblock-concatenation": (
                "$cmd='codeql database create db'\n"
                "[scriptblock]::Create('Write-Output ready; ' + $cmd).Invoke()"
            ),
            "variable-executable": ("$tool='codeql'\n& $tool database create db"),
            "start-process-variable": (
                "$tool='codeql'\nStart-Process $tool -ArgumentList 'database create db'"
            ),
            "encoded-command": "pwsh -EncodedCommand YwBvAGQAZQBxAGwA",
            "call-expression": "& (Get-Command codeql) database create db",
            "call-scriptblock-expression": "& ([scriptblock]::Create($cmd))",
            "dot-source-expression": ". ([scriptblock]::Create($cmd))",
        }
        for name, run in fixtures.items():
            workflow = (
                "jobs:\n  scan:\n    runs-on: windows-latest\n    steps:\n"
                "      - shell: pwsh\n"
                "        run: |\n"
                + "".join(f"          {line}\n" for line in run.splitlines())
            )
            with self.subTest(name=name):
                with self.assertRaisesRegex(
                    codeql_preflight.InspectionError, "dynamic command"
                ):
                    codeql_preflight.parse_workflow(workflow, name)

    def test_literal_dynamic_powershell_execution_without_codeql_is_inspected(
        self,
    ) -> None:
        text = """
jobs:
  scan:
    runs-on: windows-latest
    steps:
      - shell: pwsh
        run: |
          Invoke-Expression 'Write-Output ok'
          pwsh -Command 'Write-Output done'
          [scriptblock]::Create('Write-Output complete').Invoke()
"""
        self.assertFalse(
            codeql_preflight.parse_workflow(
                text, "literal-dynamic-powershell"
            ).has_advanced_setup
        )

    def test_ignores_uninvoked_powershell_function_but_detects_invocation(
        self,
    ) -> None:
        definition = "function Invoke-Scan\n{\n  codeql database create db\n}"
        for name, (suffix, expected) in {
            "uninvoked": ("", False),
            "invoked": ("\nInvoke-Scan", True),
        }.items():
            run = definition + suffix
            workflow = (
                "jobs:\n  scan:\n    runs-on: windows-latest\n    steps:\n"
                "      - shell: pwsh\n"
                "        run: |\n"
                + "".join(f"          {line}\n" for line in run.splitlines())
            )
            with self.subTest(name=name):
                self.assertEqual(
                    codeql_preflight.parse_workflow(
                        workflow, f"powershell-function-{name}"
                    ).has_advanced_setup,
                    expected,
                )

    def test_detects_codeql_through_cmd_shell(self) -> None:
        text = """
jobs:
  scan:
    runs-on: windows-latest
    steps:
      - shell: pwsh
        run: cmd /c codeql database create db
"""
        self.assertTrue(
            codeql_preflight.parse_workflow(
                text, "powershell-cmd-codeql"
            ).has_advanced_setup
        )

    def test_detects_transitively_invoked_powershell_function(self) -> None:
        text = """
jobs:
  scan:
    runs-on: windows-latest
    steps:
      - shell: pwsh
        run: |
          function Invoke-Scan {
            codeql database create db
          }
          function Invoke-Wrapper {
            Invoke-Scan
          }
          Invoke-Wrapper
"""
        self.assertTrue(
            codeql_preflight.parse_workflow(
                text, "transitive-powershell-function"
            ).has_advanced_setup
        )

    def test_detects_codeql_invoked_through_powershell_alias(self) -> None:
        command = """
Set-Alias scan codeql
scan database create db
"""
        self.assertTrue(codeql_preflight.contains_codeql_cli(command, "powershell"))

    def test_powershell_alias_detection_avoids_inert_aliases(self) -> None:
        fixtures = {
            "uninvoked": "Set-Alias scan codeql",
            "harmless": "Set-Alias scan Write-Output\nscan database create db",
            "overwritten": (
                "Set-Alias scan codeql\n"
                "Set-Alias scan Write-Output\n"
                "scan database create db"
            ),
            "inert-scriptblock": (
                "$block = { Set-Alias scan codeql }\nscan database create db"
            ),
            "function-local-alias-does-not-leak": (
                "function Set-LocalAlias { Set-Alias scan codeql }\n"
                "Set-LocalAlias\nscan database create db"
            ),
        }
        for name, command in fixtures.items():
            with self.subTest(name=name):
                self.assertFalse(
                    codeql_preflight.contains_codeql_cli(command, "powershell")
                )

    def test_detects_codeql_in_powershell_command_positions(self) -> None:
        for name, command in {
            "assignment": "$result = codeql database create db",
            "typed-assignment": "[string]$result = codeql database create db",
            "member-assignment": "$result.Value = codeql database create db",
            "return": "return codeql database create db",
            "returned-function": (
                "function Invoke-Scan { codeql database create db }; return Invoke-Scan"
            ),
        }.items():
            with self.subTest(name=name):
                self.assertTrue(
                    codeql_preflight.contains_codeql_cli(command, "powershell")
                )

    def test_detects_powershell_function_invoked_through_alias(self) -> None:
        command = """
function Invoke-Scan {
  codeql database create db
}
Set-Alias scan Invoke-Scan
scan
"""
        self.assertTrue(codeql_preflight.contains_codeql_cli(command, "powershell"))

    def test_detects_powershell_alias_inside_reachable_function(self) -> None:
        command = """
function Invoke-Scan {
  codeql database create db
}
Set-Alias scan Invoke-Scan
function Invoke-Wrapper {
  scan
}
Invoke-Wrapper
"""
        self.assertTrue(codeql_preflight.contains_codeql_cli(command, "powershell"))

    def test_detects_powershell_alias_defined_inside_reachable_function(
        self,
    ) -> None:
        commands = {
            "multiline": """
function Invoke-Wrapper {
  Set-Alias scan codeql
  scan database create db
}
Invoke-Wrapper
""",
            "single-line": (
                "function Invoke-Wrapper { Set-Alias scan codeql; "
                "scan database create db }\nInvoke-Wrapper"
            ),
        }
        for name, command in commands.items():
            with self.subTest(name=name):
                self.assertTrue(
                    codeql_preflight.contains_codeql_cli(command, "powershell")
                )

    def test_detects_powershell_alias_with_reordered_named_parameters(
        self,
    ) -> None:
        command = """
Set-Alias -Value codeql -Name scan
scan database create db
"""
        self.assertTrue(codeql_preflight.contains_codeql_cli(command, "powershell"))

    def test_detects_powershell_function_in_control_flow_and_assignment(self) -> None:
        for name, invocation in {
            "if": "if ($true) { Invoke-Scan }",
            "assignment": "$result = Invoke-Scan",
        }.items():
            text = f"""
jobs:
  scan:
    runs-on: windows-latest
    steps:
      - shell: pwsh
        run: |
          function Invoke-Scan {{
            codeql database create db
          }}
          {invocation}
"""
            with self.subTest(name=name):
                self.assertTrue(
                    codeql_preflight.parse_workflow(text, name).has_advanced_setup
                )

    def test_ignores_nested_uninvoked_powershell_function(self) -> None:
        text = """
jobs:
  scan:
    runs-on: windows-latest
    steps:
      - shell: pwsh
        run: |
          function Outer {
            function Inner {
              codeql database create db
            }
          }
          Outer
"""
        self.assertFalse(
            codeql_preflight.parse_workflow(
                text, "nested-uninvoked-powershell-function"
            ).has_advanced_setup
        )

    def test_powershell_function_name_used_as_data_is_not_invoked(self) -> None:
        text = """
jobs:
  scan:
    runs-on: windows-latest
    steps:
      - shell: pwsh
        run: |
          function Invoke-Scan {
            codeql database create db
          }
          $metadata = @{ Invoke-Scan = 'documented command' }
          Write-Output $metadata
"""
        self.assertFalse(
            codeql_preflight.parse_workflow(
                text, "powershell-function-name-data"
            ).has_advanced_setup
        )

    def test_detects_nested_invoked_powershell_function(self) -> None:
        text = """
jobs:
  scan:
    runs-on: windows-latest
    steps:
      - shell: pwsh
        run: |
          function Outer {
            function Inner {
              codeql database create db
            }
            Inner
          }
          Outer
"""
        self.assertTrue(
            codeql_preflight.parse_workflow(
                text, "nested-invoked-powershell-function"
            ).has_advanced_setup
        )

    def test_unknown_shell_fails_closed(self) -> None:
        text = """
jobs:
  scan:
    runs-on: windows-latest
    steps:
      - shell: cmd
        run: cmd /c codeql database create db
"""
        with self.assertRaisesRegex(
            codeql_preflight.InspectionError, "unsupported shell"
        ):
            codeql_preflight.parse_workflow(text, "cmd-codeql")

    def test_ignores_powershell_start_process_text_used_as_data(self) -> None:
        fixtures = {
            "output-string": (
                "Write-Output \"Start-Process codeql -ArgumentList 'database create db'\""
            ),
            "assigned-string": (
                "$example = \"Start-Process codeql -ArgumentList 'database create db'\""
            ),
            "comment": "# Start-Process codeql -ArgumentList 'database create db'",
            "multiline-string": (
                '$example = "documentation\nStart-Process codeql '
                "-ArgumentList 'database create db'\n\""
            ),
            "block-comment": (
                "<# example\nStart-Process codeql "
                "-ArgumentList 'database create db'\n#>"
            ),
        }
        for name, run in fixtures.items():
            text = (
                "jobs:\n  scan:\n    runs-on: windows-latest\n    steps:\n"
                "      - shell: pwsh\n"
                "        run: |\n"
                + "".join(f"          {line}\n" for line in run.splitlines())
            )
            with self.subTest(name=name):
                self.assertFalse(
                    codeql_preflight.parse_workflow(text, name).has_advanced_setup
                )

    def test_ignores_plain_codeql_text_outside_powershell_subexpression(self) -> None:
        fixtures = {
            "unrelated-subexpression": (
                '@"\ncodeql database create db\n$(Get-Date)\n"@ | Set-Content example.ps1'
            ),
            "escaped-subexpression": (
                '@"\n`$(codeql database create db)\n"@ | Set-Content example.ps1'
            ),
            "invoke-expression-as-data": (
                "@'\ncodeql database create db\n'@ | Write-Output Invoke-Expression"
            ),
            "multiline-string": '$example = "documentation\ncodeql database create db\n"',
            "line-comment": "# codeql database create db",
            "block-comment": "<# example\ncodeql database create db\n#>",
            "invoke-expression-output-string": (
                "Write-Output \"Invoke-Expression 'codeql database create db'\""
            ),
            "scriptblock-output-string": (
                "Write-Output '& { codeql database create db }'"
            ),
            "nested-shell-output-string": (
                "Write-Output \"pwsh -Command 'codeql database create db'\""
            ),
        }
        for name, run in fixtures.items():
            text = (
                "jobs:\n  scan:\n    runs-on: windows-latest\n    steps:\n"
                "      - shell: pwsh\n"
                "        run: |\n"
                + "".join(f"          {line}\n" for line in run.splitlines())
            )
            with self.subTest(name=name):
                self.assertFalse(
                    codeql_preflight.parse_workflow(text, name).has_advanced_setup
                )

    def test_shell_defaults_are_applied_to_run_steps(self) -> None:
        text = """
defaults:
  run:
    shell: pwsh
jobs:
  scan:
    runs-on: self-hosted
    steps:
      - run: |
          @'
          codeql database create db
          '@
"""
        self.assertFalse(
            codeql_preflight.parse_workflow(text, "workflow-shell").has_advanced_setup
        )

    def test_reads_quoted_reusable_workflow_key(self) -> None:
        signals = codeql_preflight.parse_workflow(
            "jobs: {call: {'uses': octo/repo/.github/workflows/scan.yml@v1}}",
            "reusable",
        )
        self.assertEqual(
            signals.reusable_calls,
            ("octo/repo/.github/workflows/scan.yml@v1",),
        )

    def test_rejects_duplicate_keys(self) -> None:
        with self.assertRaises(codeql_preflight.InspectionError):
            codeql_preflight.parse_workflow(
                "jobs:\n  test:\n    runs-on: ubuntu-latest\n"
                "    runs-on: windows-latest\n    steps: [{run: echo ok}]\n",
                "duplicate",
            )

    def test_rejects_unhashable_keys_and_invalid_workflow_shapes(self) -> None:
        cases = [
            ("? [jobs]\n: value\n", "Could not parse workflow"),
            ("- invalid\n", "not a YAML mapping"),
            ("jobs: []\n", "no non-empty jobs mapping"),
            ("jobs: {}\n", "no non-empty jobs mapping"),
            ("jobs: {test: invalid}\n", "job 'test' is not a mapping"),
            ("jobs: {test: {uses: []}}\n", "unsupported reusable-workflow"),
            ("jobs: {test: {steps: {run: echo}}}\n", "steps is not a list"),
            ("jobs: {test: {steps: [invalid]}}\n", "step 0 is not a mapping"),
            (
                "jobs: {test: {steps: [{run: echo, shell: []}]}}\n",
                "shell is not a string",
            ),
        ]

        for text, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(codeql_preflight.InspectionError, message):
                    codeql_preflight.parse_workflow(text, "invalid")

    def test_parser_checks_deadline_before_during_and_after_workflow(self) -> None:
        checks = mock.Mock()
        signals = codeql_preflight.parse_workflow(
            "jobs: {test: {runs-on: ubuntu-latest, steps: [{run: echo ok}]}}",
            "deadline",
            checks,
        )

        self.assertFalse(signals.has_advanced_setup)
        self.assertGreaterEqual(checks.call_count, 4)

    def test_context_key_normalizes_remote_identity(self) -> None:
        context = codeql_preflight.WorkflowContext("exact", "Owner", "Repo", "A" * 40)
        self.assertEqual(context.key, ("exact", "owner", "repo", "a" * 40))


class BashParserHelperTests(unittest.TestCase):
    def test_escape_and_heredoc_helpers_cover_literal_and_error_paths(self) -> None:
        self.assertTrue(codeql_preflight._is_powershell_escaped('`"', 1))
        self.assertFalse(codeql_preflight._is_powershell_escaped('``"', 2))
        self.assertEqual(
            codeql_preflight._parse_heredoc_delimiter(r"\EOF", 0, 4)[0],
            "EOF",
        )
        self.assertEqual(
            codeql_preflight._parse_heredoc_delimiter(r'"E\"OF"', 0, 7)[0],
            'E"OF',
        )
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "incomplete"):
            codeql_preflight._parse_heredoc_delimiter("\\", 0, 1)
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "unterminated"):
            codeql_preflight._parse_heredoc_delimiter("'EOF", 0, 4)
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "no static"):
            codeql_preflight._parse_heredoc_delimiter("", 0, 0)

        self.assertTrue(codeql_preflight._bash_heredoc_executes_body("sh <<EOF", 3))
        self.assertTrue(codeql_preflight._bash_heredoc_executes_body("source <<EOF", 7))
        self.assertFalse(codeql_preflight._bash_heredoc_executes_body(" <<EOF", 1))
        self.assertFalse(codeql_preflight._bash_heredoc_executes_body("echo <<EOF", 5))
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "malformed"):
            codeql_preflight._bash_heredoc_executes_body("'broken <<EOF", 8)

    def test_command_unwrapping_covers_supported_options_and_fail_closed_paths(
        self,
    ) -> None:
        cases = [
            *(
                ([prefix, "codeql", "database"], ["codeql", "database"])
                for prefix in ("!", "if", "then", "until", "while", "do")
            ),
            (["UPPER=value", "lower_2=value", "codeql"], ["codeql"]),
            (["builtin", "codeql", "database"], ["codeql", "database"]),
            (["coproc", "codeql", "database"], ["codeql", "database"]),
            (["command", "-p", "codeql", "database"], ["codeql", "database"]),
            (["command", "-v", "codeql"], []),
            (["command", "-V", "codeql"], []),
            (["command", "--", "builtin", "codeql"], ["codeql"]),
            (["exec", "--", "codeql", "database"], ["codeql", "database"]),
            (["exec", "-c", "codeql", "database"], ["codeql", "database"]),
            (["exec", "-cl", "codeql"], ["codeql"]),
            (["exec", "-a", "argv0", "codeql"], ["codeql"]),
            (["exec", "-", "codeql"], ["-", "codeql"]),
            (["exec", "-a"], []),
            (["exec", "-x", "codeql"], []),
            (["env"], []),
            (
                ["env", "--split-string=codeql database", "create"],
                ["codeql", "database", "create"],
            ),
            (
                ["env", "-Scodeql database", "create"],
                ["codeql", "database", "create"],
            ),
            (
                ["env", "-S", "codeql database", "create"],
                ["codeql", "database", "create"],
            ),
            (
                ["env", "--split-string", "codeql database", "create"],
                ["codeql", "database", "create"],
            ),
            (
                ["env", "--split-string=codeql=database", "create"],
                ["create"],
            ),
            (["env", "NAME=value", "codeql"], ["codeql"]),
            (["env", "--unset=NAME", "codeql", "database"], ["codeql", "database"]),
            (["env", "--chdir=/tmp", "codeql"], ["codeql"]),
            (["env", "--argv0=name", "codeql"], ["codeql"]),
            (["env", "-u", "NAME", "codeql"], ["codeql"]),
            (["env", "-C", "/tmp", "codeql"], ["codeql"]),
            (["env", "--chdir", "/tmp", "codeql"], ["codeql"]),
            (["env", "--argv0", "name", "codeql"], ["codeql"]),
            (["env", "--unset"], []),
            (["env", "--unknown", "codeql", "database"], ["codeql", "database"]),
            (["sudo", "--user=root", "codeql", "database"], ["codeql", "database"]),
            (["sudo", "-u", "root", "codeql"], ["codeql"]),
            (["sudo", "--user", "root", "codeql"], ["codeql"]),
            (["sudo", "--", "codeql"], ["codeql"]),
            (["sudo", "-u"], []),
            (["timeout", "--signal=TERM", "5", "codeql"], ["codeql"]),
            (["timeout", "--kill-after=1", "5", "codeql"], ["codeql"]),
            (["timeout", "--signal", "TERM", "5", "codeql"], ["codeql"]),
            (["timeout", "-k", "1", "5", "codeql"], ["codeql"]),
            (["timeout", "-s"], []),
            (["timeout"], []),
            (["time", "--format=%E", "codeql"], ["codeql"]),
            (["time", "--output=file", "codeql"], ["codeql"]),
            (["time", "-f", "%E", "codeql"], ["codeql"]),
            (["nohup", "--", "codeql"], ["codeql"]),
            (["nohup", "--help"], []),
            (["nohup", "--version"], []),
            (["nice", "-n"], []),
            (["nice", "--adjustment", "5", "codeql"], ["codeql"]),
            (["nice", "-10", "codeql"], ["codeql"]),
            (["nice", "codeql"], ["codeql"]),
            (["stdbuf", "--output=L", "codeql"], ["codeql"]),
            (["stdbuf", "--input", "0", "codeql"], ["codeql"]),
            (["stdbuf", "--error=0", "codeql"], ["codeql"]),
            (["stdbuf", "-o", "L", "codeql"], ["codeql"]),
            (["stdbuf", "-o"], []),
        ]
        for words, expected in cases:
            with self.subTest(words=words):
                self.assertEqual(codeql_preflight._bash_unwrap_command(words), expected)

        with (
            mock.patch.object(codeql_preflight, "MAX_ENV_SPLIT_EXPANSIONS", 0),
            self.assertRaises(codeql_preflight.InspectionError) as raised,
        ):
            codeql_preflight._bash_unwrap_command(["env", "-Scodeql database"])
        self.assertEqual(
            str(raised.exception),
            "Shell env split-string nesting exceeds the safety limit.",
        )
        with self.assertRaises(codeql_preflight.InspectionError) as raised:
            codeql_preflight._bash_unwrap_command(["env", "-S$COMMAND"])
        self.assertEqual(
            str(raised.exception),
            "Shell env split-string has a non-literal payload.",
        )
        with self.assertRaises(codeql_preflight.InspectionError) as raised:
            codeql_preflight._bash_unwrap_command(["env", "-S'unterminated"])
        self.assertEqual(str(raised.exception), "Shell env split-string is malformed.")

        words = ["--", "value"]
        self.assertTrue(codeql_preflight._bash_pop_option(words, set(), set()))
        self.assertEqual(words, ["value"])

    def test_xargs_shell_and_dynamic_helpers_cover_option_and_payload_edges(
        self,
    ) -> None:
        xargs_cases = [
            ([], []),
            (["--", "codeql", "database"], ["codeql", "database"]),
            (["-0", "codeql", "database"], ["codeql", "database"]),
            (["-n", "2", "codeql"], ["codeql"]),
            (["-n"], []),
            (["-n2", "codeql"], ["codeql"]),
            (["--max-args=2", "codeql"], ["codeql"]),
            (["--unknown", "codeql"], []),
        ]
        for arguments, expected in xargs_cases:
            with self.subTest(arguments=arguments):
                self.assertEqual(
                    codeql_preflight._bash_xargs_command(arguments), expected
                )

        options_with_argument = (
            "-a",
            "--arg-file",
            "-d",
            "--delimiter",
            "-E",
            "--eof",
            "-I",
            "--replace",
            "-J",
            "-L",
            "--max-lines",
            "-n",
            "--max-args",
            "-P",
            "--max-procs",
            "--process-slot-var",
            "-R",
            "-s",
            "-S",
            "--max-chars",
        )
        for option in options_with_argument:
            with self.subTest(option=option):
                self.assertEqual(
                    codeql_preflight._bash_xargs_command([option, "value", "codeql"]),
                    ["codeql"],
                )
                self.assertEqual(codeql_preflight._bash_xargs_command([option]), [])

        options_without_argument = (
            "-0",
            "--null",
            "-o",
            "--open-tty",
            "-p",
            "--interactive",
            "-r",
            "--no-run-if-empty",
            "-t",
            "--verbose",
            "-x",
            "--exit",
        )
        for option in options_without_argument:
            with self.subTest(option=option):
                self.assertEqual(
                    codeql_preflight._bash_xargs_command([option, "codeql"]),
                    ["codeql"],
                )

        for option in (
            "-a",
            "-d",
            "-E",
            "-I",
            "-J",
            "-L",
            "-n",
            "-P",
            "-R",
            "-s",
            "-S",
        ):
            with self.subTest(attached=option):
                self.assertEqual(
                    codeql_preflight._bash_xargs_command([f"{option}value", "codeql"]),
                    ["codeql"],
                )
        for option in (
            "--arg-file",
            "--delimiter",
            "--eof",
            "--replace",
            "--max-lines",
            "--max-args",
            "--max-procs",
            "--process-slot-var",
            "--max-chars",
        ):
            with self.subTest(attached=option):
                self.assertEqual(
                    codeql_preflight._bash_xargs_command([f"{option}=value", "codeql"]),
                    ["codeql"],
                )

        shell_index_cases = [
            (["echo"], None),
            (["sh", "-c", "body"], 2),
            (["bash", "-O", "extglob", "-c", "body"], 4),
            (["bash", "--rcfile=file", "-c", "body"], 3),
            (["sh", "-c", "--", "body"], 3),
            (["sh", "script.sh"], None),
            (["sh", "-c"], 2),
            (["sh", "--verbose"], None),
        ]
        for shell_words, expected_index in shell_index_cases:
            with self.subTest(words=shell_words):
                self.assertEqual(
                    codeql_preflight._bash_shell_command_string_index(shell_words),
                    expected_index,
                )

        with self.assertRaisesRegex(codeql_preflight.InspectionError, "nesting"):
            codeql_preflight._bash_words_contain_codeql(
                ["codeql", "database"], codeql_preflight.MAX_DYNAMIC_EXECUTION_DEPTH + 1
            )
        with self.assertRaisesRegex(
            codeql_preflight.InspectionError, "no literal payload"
        ):
            codeql_preflight._bash_words_contain_codeql(["xargs", "sh", "-c"], 0)
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "non-literal"):
            codeql_preflight._bash_words_contain_codeql(
                ["xargs", "sh", "-c", "$COMMAND"], 0
            )
        self.assertTrue(
            codeql_preflight._bash_words_contain_codeql(
                ["xargs", "sh", "-c", "codeql database create db"], 0
            )
        )
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "malformed"):
            codeql_preflight._bash_contains_wrapped_codeql("'unterminated")
        self.assertFalse(
            codeql_preflight._bash_words_contain_codeql(["xargs", "echo", "ok"], 0)
        )
        self.assertFalse(
            codeql_preflight._bash_words_contain_codeql(
                [
                    "find",
                    ".",
                    "-exec",
                    "sh",
                    "-c",
                    "echo first",
                    ";",
                    "-exec",
                    "sh",
                    "-c",
                    "echo second",
                    ";",
                ],
                0,
            )
        )

    def test_dynamic_body_trap_here_string_and_alias_helpers(self) -> None:
        self.assertEqual(codeql_preflight._bash_here_string_payload(["sh", "<<<"]), "")
        self.assertEqual(
            codeql_preflight._bash_here_string_payload(["sh", "<<<payload"]),
            "payload",
        )
        self.assertIsNone(codeql_preflight._bash_here_string_payload(["sh"]))
        self.assertEqual(
            codeql_preflight._bash_trap_handler(["trap", "--", "handler", "EXIT"]),
            "handler",
        )
        self.assertIsNone(codeql_preflight._bash_trap_handler(["trap", "-l"]))
        self.assertIsNone(codeql_preflight._bash_trap_handler(["echo", "handler"]))

        self.assertTrue(
            codeql_preflight._bash_dynamic_execution_is_unresolved("echo x | sh")
        )
        self.assertTrue(
            codeql_preflight._bash_dynamic_execution_is_unresolved("sh <<< '$COMMAND'")
        )
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "malformed"):
            codeql_preflight._bash_dynamic_execution_is_unresolved("'unterminated")

        bodies = codeql_preflight._bash_dynamic_execution_bodies(
            "trap -- 'echo trap' EXIT; eval 'echo eval'; sh -c 'echo shell'; sh <<< 'echo here'"
        )
        self.assertEqual(bodies, ["echo trap", "echo eval", "echo shell", "echo here"])
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "malformed"):
            codeql_preflight._bash_dynamic_execution_bodies("'unterminated")

        aliases = codeql_preflight._bash_alias_expansions(
            "shopt -s expand_aliases\n"
            "alias scan='codeql database create db'\n"
            "scan\n"
            "unalias -- scan\n"
            "scan\n"
            "shopt -u expand_aliases\n"
        )
        self.assertIn("codeql database create db", aliases)
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "alias command"):
            codeql_preflight._bash_alias_expansions("alias 'unterminated")
        self.assertEqual(
            codeql_preflight._bash_alias_expansions(
                "shopt -s expand_aliases\n"
                "alias -- -p invalid scan='echo scan'\n"
                "unalias -a\n"
                "shopt -q expand_aliases\n"
                "alias bad.name='echo invalid'\n"
                "eval '$SCAN'\n"
                "eval 'echo safe'\n"
            ),
            [],
        )

    def test_alias_state_quoted_role_and_array_assignment_helpers(self) -> None:
        aliases, enabled, enabled_line = codeql_preflight._bash_alias_state(
            "shopt -s expand_aliases\n"
            "alias -- ignored bad-name=value scan='codeql database'\n"
        )
        self.assertTrue(enabled)
        self.assertGreater(enabled_line, 0)
        self.assertEqual(aliases["scan"][0], "codeql database")

        aliases, enabled, _line = codeql_preflight._bash_alias_state(
            "shopt -s expand_aliases\n"
            "alias one=value two=value\n"
            "unalias -- one\n"
            "unalias -a\n"
            "shopt -u expand_aliases\n"
            "shopt -q expand_aliases\n"
            "alias bad.name=value\n"
            "echo safe\n"
        )
        self.assertEqual(aliases, {})
        self.assertFalse(enabled)
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "alias command"):
            codeql_preflight._bash_alias_state("alias 'unterminated")

        self.assertFalse(
            codeql_preflight._bash_function_is_invoked(
                "export -f other; eval '$BODY'; export -f scan; sh -c '$BODY'",
                "scan",
            )
        )

        role_cases = [
            ('VALUE="text"', "literal"),
            ('"command"', "command"),
            ('sh -c "codeql database"', "shell-command"),
            ('xargs sh -c "codeql database"', "shell-command"),
            ('find . -exec sh -c "codeql database"', "shell-command"),
            ('eval "codeql database"', "eval"),
            ('echo "literal"', "literal"),
        ]
        for line, expected in role_cases:
            with self.subTest(line=line):
                self.assertEqual(
                    codeql_preflight._bash_quoted_string_role(line, line.index('"')),
                    expected,
                )
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "malformed"):
            codeql_preflight._bash_quoted_string_role("echo 'broken \"", 13)

        self.assertFalse(codeql_preflight._bash_opens_array_assignment("(", 0))
        self.assertTrue(codeql_preflight._bash_opens_array_assignment("items=(", 6))
        self.assertTrue(codeql_preflight._bash_opens_array_assignment("items+=(", 7))
        self.assertFalse(codeql_preflight._bash_opens_array_assignment("1items=(", 7))
        self.assertFalse(
            codeql_preflight._bash_opens_array_assignment("echo-items=(", 11)
        )

    def test_bash_executable_text_rejects_unterminated_constructs(self) -> None:
        cases = [
            ("cat <<EOF\nbody\n", "heredoc"),
            ("echo 'unterminated\n", "multiline string"),
            ("echo `date\n", "command substitution"),
            ("items=(one\n", "array assignment"),
            ("echo $((1 + 2\n", "arithmetic expression"),
        ]
        for text, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(codeql_preflight.InspectionError, message):
                    codeql_preflight.shell_executable_text(text, "bash")

    def test_bash_executable_text_preserves_executable_constructs_and_masks_data(
        self,
    ) -> None:
        cases = [
            ("cat <<-  'EOF'\n\tbody\n\tEOF\n", "cat", "body"),
            (
                'echo `printf \'single\' "double\\"quoted" \\ value`\n',
                "printf",
                None,
            ),
            (
                'echo "$(printf \'%s\' \\"double\\" \\ value $(date))"\n',
                "$(date)",
                None,
            ),
            ("echo \"`printf 'inside'`\"\n", "printf 'inside'", None),
            ("echo $((1 + (2)))\n", "$((1 + (2)))", None),
            ("items=($(echo (one)))\n", "$(echo (one))", None),
            ('echo "$(printf "a\\"b")"\n', 'printf "a\\"b"', None),
            ("echo $((1 + \\\n2))\n", "$((1 + \\\n2))", None),
        ]
        for text, executable_fragment, masked_fragment in cases:
            with self.subTest(text=text):
                executable_text = codeql_preflight.shell_executable_text(text, "bash")
                self.assertIn(executable_fragment, executable_text)
                if masked_fragment is not None:
                    self.assertNotIn(masked_fragment, executable_text)


class PowerShellParserHelperTests(unittest.TestCase):
    def test_here_string_start_and_subexpression_parsing(self) -> None:
        self.assertEqual(
            codeql_preflight._powershell_here_string_start("@' # comment", 12),
            (0, "'"),
        )
        self.assertIsNone(codeql_preflight._powershell_here_string_start("# @'", 4))
        self.assertIsNone(
            codeql_preflight._powershell_here_string_start('Write-Output "@\'"', 17)
        )

        visible = codeql_preflight._powershell_subexpression_text(
            "literal $(Write-Output ('nested')) tail"
        )
        self.assertIn("$(Write-Output ('nested'))", visible)
        self.assertNotIn("literal", visible)
        escaped = codeql_preflight._powershell_subexpression_text("literal `$(ignored)")
        self.assertNotIn("ignored", escaped)
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "not terminated"):
            codeql_preflight._powershell_subexpression_text("$(Write-Output")

        self.assertIsNone(
            codeql_preflight._powershell_here_string_start(
                '"doubled""quote" `"escaped @\' suffix', 36
            )
        )
        nested = codeql_preflight._powershell_subexpression_text(
            '$(Write-Output "escaped `"quote")'
        )
        self.assertIn("Write-Output", nested)
        here_line = '"escaped `" quote" @\''
        self.assertEqual(
            codeql_preflight._powershell_here_string_start(here_line, len(here_line)),
            (here_line.index("@"), "'"),
        )
        self.assertIsNone(
            codeql_preflight._powershell_here_string_start(
                "@' trailing", len("@' trailing")
            )
        )

    def test_here_string_execution_masking_and_termination(self) -> None:
        executed = codeql_preflight._powershell_executable_text(
            "iex @'\ncodeql database create db\n'@\n"
        )
        self.assertIn("codeql database create db", executed)
        piped = codeql_preflight._powershell_executable_text(
            "@'\ncodeql database create db\n'@ | iex\n"
        )
        self.assertIn("codeql database create db", piped)
        interpolated = codeql_preflight._powershell_executable_text(
            '@"\nliteral $(codeql database create db)\n"@\n'
        )
        self.assertIn("$(codeql database create db)", interpolated)
        literal = codeql_preflight._powershell_executable_text(
            "@'\ncodeql database create db\n'@\n"
        )
        self.assertNotIn("codeql database create db", literal)
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "here-string"):
            codeql_preflight._powershell_executable_text("@'\nunterminated\n")

    def test_comment_string_and_statement_masking(self) -> None:
        text = (
            "<# block ; | #> Write-Output 'literal;value'; "
            'Write-Output "$(codeql database create db)" # trailing\nnext'
        )
        masked = codeql_preflight._powershell_mask_comments_and_multiline_literals(text)
        self.assertNotIn("block", masked)
        self.assertNotIn("literal;value", masked)
        self.assertIn("$(codeql database create db)", masked)
        self.assertNotIn("trailing", masked)

        parts = codeql_preflight._powershell_statement_parts(text)
        self.assertGreaterEqual(len(parts), 3)
        self.assertTrue(
            any("literal;value" in segment for _separator, segment in parts)
        )
        self.assertEqual(
            codeql_preflight._powershell_statement_segments("one; 'two;three' | four"),
            ["one", " 'two;three' ", " four"],
        )

        with self.assertRaisesRegex(codeql_preflight.InspectionError, "block comment"):
            codeql_preflight._powershell_mask_comments_and_multiline_literals("<# open")
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "string literal"):
            codeql_preflight._powershell_mask_comments_and_multiline_literals("'open")

        edge_text = "# comment without newline\n\"escaped `\" quote\"; 'doubled''quote'"
        self.assertIsInstance(
            codeql_preflight._powershell_mask_comments_and_multiline_literals(
                edge_text
            ),
            str,
        )
        self.assertGreaterEqual(
            len(codeql_preflight._powershell_statement_parts(edge_text)), 2
        )
        self.assertEqual(
            codeql_preflight._powershell_mask_comments_and_multiline_literals(
                "# comment without newline"
            ).strip(),
            "",
        )

    def test_matching_brace_ignores_quotes_comments_and_nested_blocks(self) -> None:
        powershell = '{ "}" <# } #> { } # }\n }'
        self.assertEqual(
            codeql_preflight._matching_shell_brace(powershell, 0, "powershell"),
            len(powershell) - 1,
        )
        bash = '{ "}" { } # ignored }\n }'
        self.assertEqual(
            codeql_preflight._matching_shell_brace(bash, 0, "bash"),
            len(bash) - 1,
        )
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "not terminated"):
            codeql_preflight._matching_shell_brace("{ nested", 0, "bash")

        escaped_powershell = "{ \"escaped `\" }\"; 'doubled'' } quote'; }"
        self.assertEqual(
            codeql_preflight._matching_shell_brace(escaped_powershell, 0, "powershell"),
            len(escaped_powershell) - 1,
        )
        escaped_bash = '{ "escaped \\" } quote"; }'
        self.assertEqual(
            codeql_preflight._matching_shell_brace(escaped_bash, 0, "bash"),
            len(escaped_bash) - 1,
        )

    def test_function_definition_and_bash_invocation_paths(self) -> None:
        bash = "scan() { echo scan; }\nfunction other { echo other; }\n"
        self.assertEqual(
            [
                name
                for name, _start, _end in codeql_preflight._shell_function_definitions(
                    bash, "bash"
                )
            ],
            ["scan", "other"],
        )
        powershell = "function global:Scan { Write-Output scan }\n"
        self.assertEqual(
            [
                name
                for name, _start, _end in codeql_preflight._shell_function_definitions(
                    powershell, "powershell"
                )
            ],
            ["global:Scan"],
        )

        for text in (
            "scan",
            "! scan",
            "if scan",
            "then scan",
            "until scan",
            "while scan",
            "do scan",
            "NAME=value scan",
            "coproc scan",
            "time --verbose scan",
            "trap 'scan' EXIT",
            "trap -- 'scan' EXIT",
            "eval 'scan'",
            "eval echo '; scan'",
            "export -f scan; sh -c 'scan'",
            "declare -fx scan; bash -c 'scan'",
            "typeset -f scan; dash -c 'scan'",
            "export -f scan; ksh -c 'scan'",
            "export -f scan; zsh -c 'scan'",
        ):
            with self.subTest(text=text):
                self.assertTrue(
                    codeql_preflight._bash_function_is_invoked(text, "scan")
                )
        self.assertFalse(
            codeql_preflight._bash_function_is_invoked("echo scan", "scan")
        )
        for text in (
            "export -- scan; sh -c 'scan'",
            "export -f other; sh -c 'scan'",
            "trap '$HANDLER' EXIT",
            "trap '`handler`' EXIT",
            "eval '$BODY'",
            "eval '`body`'",
        ):
            with self.subTest(inert=text):
                self.assertFalse(
                    codeql_preflight._bash_function_is_invoked(text, "scan")
                )
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "nesting"):
            codeql_preflight._bash_function_is_invoked(
                "scan", "scan", codeql_preflight.MAX_DYNAMIC_EXECUTION_DEPTH + 1
            )
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "malformed"):
            codeql_preflight._bash_function_is_invoked("'unterminated", "scan")

    def test_powershell_alias_definition_resolution_and_invocation_paths(self) -> None:
        self.assertEqual(
            codeql_preflight._powershell_static_alias_value("'codeql'"), "codeql"
        )
        self.assertIsNone(codeql_preflight._powershell_static_alias_value("$dynamic"))
        self.assertIsNone(
            codeql_preflight._powershell_alias_definition("Write-Output ok")
        )
        self.assertEqual(
            codeql_preflight._powershell_alias_definition("Set-Alias scan codeql"),
            ("scan", "codeql"),
        )
        self.assertEqual(
            codeql_preflight._powershell_alias_definition(
                "Set-Alias -Name scan -Value codeql -Force -Scope Local"
            ),
            ("scan", "codeql"),
        )
        for definition in (
            "Set-Alias -Name",
            "Set-Alias -Unknown value",
            "Set-Alias only-name",
        ):
            with self.subTest(definition=definition):
                self.assertEqual(
                    codeql_preflight._powershell_alias_definition(definition),
                    (None, None),
                )

        text = (
            "Set-Alias scan codeql; "
            "scan database create db; "
            "Remove-Alias -Name scan; "
            "scan database create ignored"
        )
        invocations = codeql_preflight._powershell_alias_invocations(text)
        self.assertEqual(len(invocations), 1)
        self.assertEqual(invocations[0][0], "codeql")
        self.assertTrue(codeql_preflight._powershell_contains_codeql_alias(text))

        self.assertEqual(
            codeql_preflight._powershell_resolve_alias(
                "first", {"first": "second", "second": "codeql"}
            ),
            "codeql",
        )
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "cycle"):
            codeql_preflight._powershell_resolve_alias(
                "first", {"first": "second", "second": "first"}
            )

        self.assertTrue(
            codeql_preflight._powershell_function_is_invoked("& Scan", "scan")
        )
        self.assertTrue(
            codeql_preflight._powershell_function_is_invoked(
                "invoke", "scan", {"invoke": "scan"}
            )
        )
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "not a static"):
            codeql_preflight._powershell_function_is_invoked(
                "invoke", "scan", {"invoke": None}
            )

        states = codeql_preflight._powershell_function_invocation_states(
            "Set-Alias invoke scan; invoke", "scan"
        )
        self.assertEqual(len(states), 1)
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "not a static"):
            codeql_preflight._powershell_function_invocation_states(
                "invoke", "scan", {"invoke": None}
            )
        self.assertFalse(
            codeql_preflight._powershell_function_is_invoked(
                "invoke", "scan", {"invoke": "other"}
            )
        )

        segments = codeql_preflight._powershell_alias_segments(
            "Set-Alias $dynamic codeql; Remove-Alias $dynamic; Write-Output ok"
        )
        self.assertEqual(len(segments), 1)
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "not a static"):
            codeql_preflight._powershell_contains_codeql_alias(
                "Set-Alias scan $dynamic; scan database create db"
            )
        self.assertTrue(
            codeql_preflight._powershell_contains_codeql_alias(
                "function unused { Write-Output unused }; "
                "Set-Alias scan codeql; scan database create db"
            )
        )

    def test_quoted_dynamic_body_and_unresolved_execution_variants(self) -> None:
        self.assertIsNone(codeql_preflight._powershell_quoted_body("plain"))
        self.assertEqual(
            codeql_preflight._powershell_quoted_body('"escaped `"quote"'),
            'escaped `"quote',
        )
        self.assertEqual(
            codeql_preflight._powershell_quoted_body("'doubled''quote'"),
            "doubled''quote",
        )
        self.assertIsNone(codeql_preflight._powershell_quoted_body("'body' trailing"))
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "not terminated"):
            codeql_preflight._powershell_quoted_body("'unterminated")

        bodies = codeql_preflight._powershell_dynamic_execution_bodies(
            "iex 'echo iex'; "
            "pwsh -Command 'echo shell'; "
            "& ([scriptblock]::Create('echo invoked')); "
            ". [scriptblock]::Create('echo sourced'); "
            "[scriptblock]::Create('echo method').Invoke(); "
            "& { echo block }"
        )
        self.assertEqual(
            bodies,
            [
                "echo iex",
                "echo shell",
                "echo invoked",
                "echo sourced",
                "echo method",
                " echo block ",
            ],
        )
        for text in (
            "iex $dynamic",
            "pwsh -Command $dynamic",
            "& ([scriptblock]::Create($dynamic))",
            ". [scriptblock]::Create($dynamic)",
        ):
            with self.subTest(text=text):
                with self.assertRaisesRegex(
                    codeql_preflight.InspectionError, "non-literal"
                ):
                    codeql_preflight._powershell_dynamic_execution_bodies(text)

        unresolved = (
            "pwsh -EncodedCommand ZQBjAGgAbwA=",
            "Start-Process $program",
            "cmd /c codeql database create db",
            "& $command",
            "& (Get-Command codeql)",
            ". $command",
            ". (Get-Command codeql)",
        )
        for text in unresolved:
            with self.subTest(text=text):
                self.assertTrue(
                    codeql_preflight._powershell_dynamic_execution_is_unresolved(text)
                )
        self.assertFalse(
            codeql_preflight._powershell_dynamic_execution_is_unresolved(
                "Write-Output safe"
            )
        )
        self.assertEqual(
            codeql_preflight._powershell_dynamic_execution_bodies(
                "iex; pwsh script.ps1"
            ),
            [],
        )
        self.assertFalse(
            codeql_preflight._powershell_dynamic_execution_is_unresolved(
                ". ([scriptblock]::Create('Write-Output safe'))"
            )
        )
        self.assertFalse(
            codeql_preflight._powershell_dynamic_execution_is_unresolved(". ./safe.ps1")
        )

    def test_shell_configuration_inference_and_dispatch(self) -> None:
        self.assertIsNone(codeql_preflight._configured_shell(None))
        self.assertIsNone(codeql_preflight._configured_shell({"run": []}))
        self.assertIsNone(codeql_preflight._configured_shell({"run": {"shell": 7}}))
        self.assertEqual(
            codeql_preflight._configured_shell({"run": {"shell": "pwsh"}}),
            "pwsh",
        )

        shell_cases = [
            ("'/usr/bin/bash' -e {0}", None, "bash"),
            ("C:\\Tools\\pwsh.exe -File {0}", None, "powershell"),
            ("python", None, "unknown"),
            (None, "windows-latest", "powershell"),
            (None, ["ubuntu-latest", "self-hosted"], "bash"),
            (None, ["custom"], "unknown"),
            (None, 7, "unknown"),
        ]
        for shell, runs_on, expected in shell_cases:
            with self.subTest(shell=shell, runs_on=runs_on):
                self.assertEqual(codeql_preflight._shell_kind(shell, runs_on), expected)

        self.assertEqual(
            codeql_preflight.shell_executable_text("plain", "unknown"), "plain"
        )
        self.assertFalse(codeql_preflight._contains_direct_codeql("echo safe"))
        with self.assertRaisesRegex(
            codeql_preflight.InspectionError, "unsupported shell"
        ):
            codeql_preflight.contains_codeql_cli("echo safe", "unknown")
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "nesting"):
            codeql_preflight.contains_codeql_cli(
                "echo safe",
                "bash",
                codeql_preflight.MAX_DYNAMIC_EXECUTION_DEPTH + 1,
            )

    def test_reachable_powershell_function_cycle_is_visited_once(self) -> None:
        text = "function A { B }; function B { A }; A"
        definitions = codeql_preflight._shell_function_definitions(text, "powershell")
        top_level = list(text)
        for _name, start, end in definitions:
            codeql_preflight._mask(top_level, start, end)
        calls = codeql_preflight._powershell_reachable_function_calls(
            text, definitions, "".join(top_level)
        )
        self.assertEqual({name for name, _body, _aliases in calls}, {"A", "B"})


class ReusableWorkflowTraversalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = codeql_preflight.WorkflowContext("local")

    @staticmethod
    def calls(count: int) -> tuple[str, ...]:
        return tuple(
            f"octo/repo/.github/workflows/workflow-{index}.yml@v1"
            for index in range(count)
        )

    def test_limit_is_independent_for_each_top_level_caller(self) -> None:
        resolver = FlatResolver()
        signals = codeql_preflight.WorkflowSignals(False, self.calls(30))
        for root in ("root-a", "root-b"):
            self.assertEqual(
                codeql_preflight.inspect_root(
                    root, ("root", root), signals, self.context, resolver
                ),
                [],
            )
        self.assertEqual(resolver.calls, 60)

    def test_rejects_a_fifty_first_unique_called_workflow(self) -> None:
        resolver = FlatResolver()
        signals = codeql_preflight.WorkflowSignals(False, self.calls(51))
        with self.assertRaises(codeql_preflight.InspectionError):
            codeql_preflight.inspect_root(
                "too-many", ("root", "too-many"), signals, self.context, resolver
            )
        self.assertEqual(resolver.calls, 51)

    def test_allows_more_than_fifty_references_to_fewer_unique_workflows(
        self,
    ) -> None:
        signals = codeql_preflight.WorkflowSignals(
            False, tuple(f"parent-{index}" for index in range(26))
        )
        self.assertEqual(
            codeql_preflight.inspect_root(
                "fan-in", ("root", "fan-in"), signals, self.context, FanInResolver()
            ),
            [],
        )

    def test_resolution_failure_aborts_without_unbounded_requests(self) -> None:
        resolver = FailingResolver()
        signals = codeql_preflight.WorkflowSignals(False, self.calls(100))
        with self.assertRaises(codeql_preflight.InspectionError):
            codeql_preflight.inspect_root(
                "invalid", ("root", "invalid"), signals, self.context, resolver
            )
        self.assertEqual(resolver.calls, 1)

    def test_traversal_checks_the_shared_deadline_before_resolution(self) -> None:
        resolver = FlatResolver()
        signals = codeql_preflight.WorkflowSignals(False, self.calls(1))

        def expired() -> None:
            raise codeql_preflight.InspectionError("expired")

        with self.assertRaisesRegex(codeql_preflight.InspectionError, "expired"):
            codeql_preflight.inspect_root(
                "deadline",
                ("root", "deadline"),
                signals,
                self.context,
                resolver,
                expired,
            )
        self.assertEqual(resolver.calls, 0)

    def test_enforces_ten_total_workflow_levels(self) -> None:
        signals = codeql_preflight.WorkflowSignals(False, ("1",))
        codeql_preflight.inspect_root(
            "level-10", ("root", "level-10"), signals, self.context, ChainResolver(9)
        )
        with self.assertRaises(codeql_preflight.InspectionError):
            codeql_preflight.inspect_root(
                "level-11",
                ("root", "level-11"),
                signals,
                self.context,
                ChainResolver(10),
            )

    def test_rejects_reusable_workflow_cycle(self) -> None:
        resolver = GraphResolver({"a": ("a", ("root",)), "root": ("root", ())})
        signals = codeql_preflight.WorkflowSignals(False, ("a",))
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "cycle"):
            codeql_preflight.inspect_root(
                "cycle", ("graph", "root"), signals, self.context, resolver
            )

    def test_rechecks_shared_workflow_at_deeper_path(self) -> None:
        graph = {
            "shared-shallow": ("shared", ("tail",)),
            "branch": ("branch", ("2",)),
            "2": ("2", ("3",)),
            "3": ("3", ("4",)),
            "4": ("4", ("5",)),
            "5": ("5", ("6",)),
            "6": ("6", ("7",)),
            "7": ("7", ("8",)),
            "8": ("8", ("shared-deep",)),
            "shared-deep": ("shared", ("tail",)),
            "tail": ("tail", ()),
        }
        signals = codeql_preflight.WorkflowSignals(False, ("shared-shallow", "branch"))
        with self.assertRaisesRegex(
            codeql_preflight.InspectionError, "exceeds 10 levels"
        ):
            codeql_preflight.inspect_root(
                "deep-alias",
                ("graph", "root"),
                signals,
                self.context,
                GraphResolver(graph),
            )

    def test_reference_cap_and_advanced_workflow_evidence_are_reported(self) -> None:
        signals = codeql_preflight.WorkflowSignals(True, ("advanced",))
        advanced_node = codeql_preflight.WorkflowNode(
            ("advanced",),
            "called:advanced",
            self.context,
            codeql_preflight.WorkflowSignals(True, ()),
        )
        resolver = mock.Mock()
        resolver.resolve.return_value = advanced_node

        self.assertEqual(
            codeql_preflight.inspect_root(
                "root", ("root",), signals, self.context, resolver
            ),
            ["root", "called:advanced"],
        )

        with (
            mock.patch.object(codeql_preflight, "MAX_REUSABLE_REFERENCES_PER_ROOT", 0),
            self.assertRaisesRegex(codeql_preflight.InspectionError, "edge traversal"),
        ):
            codeql_preflight.inspect_root(
                "root",
                ("root",),
                codeql_preflight.WorkflowSignals(False, ("call",)),
                self.context,
                resolver,
            )


class WorkflowDiscoveryTests(unittest.TestCase):
    def test_only_direct_workflow_files_are_loaded(self) -> None:
        workflow = "jobs: {test: {runs-on: ubuntu-latest, steps: [{run: echo ok}]}}"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_root = root / ".github" / "workflows"
            nested = workflow_root / "nested"
            nested.mkdir(parents=True)
            (workflow_root / "direct.yml").write_text(workflow, encoding="utf-8")
            (nested / "ignored.yml").write_text(workflow, encoding="utf-8")
            loaded = codeql_preflight.load_local_workflows(root)
        self.assertEqual(set(loaded), {".github/workflows/direct.yml"})

    def test_direct_workflow_path_contract(self) -> None:
        self.assertTrue(
            codeql_preflight.is_direct_workflow_path(".github/workflows/ci.yml")
        )
        self.assertFalse(
            codeql_preflight.is_direct_workflow_path(".github/workflows/nested/ci.yml")
        )

    def test_safe_path_rejects_a_dangling_link_component(self) -> None:
        root = Path("C:/repository")
        linked_component = root / ".github"
        with (
            mock.patch.object(codeql_preflight, "require_safe_root"),
            mock.patch.object(
                codeql_preflight.os.path,
                "lexists",
                side_effect=lambda path: Path(path) == linked_component,
            ),
            mock.patch.object(
                Path,
                "is_symlink",
                autospec=True,
                side_effect=lambda path: path == linked_component,
            ),
        ):
            with self.assertRaisesRegex(codeql_preflight.InspectionError, "linked"):
                codeql_preflight.require_safe_path(linked_component / "workflows", root)

    def test_safe_root_rejects_a_linked_repository_root(self) -> None:
        root = Path("C:/repository") if os.name == "nt" else Path("/repository")
        with (
            mock.patch.object(codeql_preflight.os.path, "lexists", return_value=True),
            mock.patch.object(
                Path,
                "is_symlink",
                autospec=True,
                side_effect=lambda path: path == root,
            ),
            mock.patch.object(codeql_preflight, "is_reparse_point", return_value=False),
            mock.patch.object(codeql_preflight.os.path, "ismount", return_value=False),
        ):
            with self.assertRaisesRegex(
                codeql_preflight.InspectionError, "repository root"
            ):
                codeql_preflight.require_safe_root(root)

    def test_missing_workflow_root_is_safety_checked_before_skip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(codeql_preflight, "require_safe_path") as safe_path:
                self.assertEqual(codeql_preflight.load_local_workflows(root), {})
        safe_path.assert_called_once_with(root / ".github" / "workflows", root)

    def test_rejects_too_many_local_directory_entries(self) -> None:
        workflow = "jobs: {test: {runs-on: ubuntu-latest, steps: [{run: echo ok}]}}"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_root = root / ".github" / "workflows"
            workflow_root.mkdir(parents=True)
            (workflow_root / "ci.yml").write_text(workflow, encoding="utf-8")
            (workflow_root / "notes.txt").write_text("notes", encoding="utf-8")
            with mock.patch.object(codeql_preflight, "MAX_LOCAL_DIRECTORY_ENTRIES", 1):
                with self.assertRaisesRegex(
                    codeql_preflight.InspectionError, "directory entry"
                ):
                    codeql_preflight.load_local_workflows(root)

    def test_rejects_oversized_workflow_and_total_workflow_bytes(self) -> None:
        workflow = "jobs: {test: {runs-on: ubuntu-latest, steps: [{run: echo ok}]}}"
        with mock.patch.object(codeql_preflight, "MAX_WORKFLOW_BYTES", 32):
            with self.assertRaisesRegex(
                codeql_preflight.InspectionError, "byte safety cap"
            ):
                codeql_preflight.parse_workflow(workflow, "oversized")

        budget = codeql_preflight.WorkflowByteBudget()
        with mock.patch.object(
            codeql_preflight, "MAX_TOTAL_WORKFLOW_BYTES", len(workflow.encode())
        ):
            budget.parse(workflow, "first")
            with self.assertRaisesRegex(
                codeql_preflight.InspectionError, "total workflow byte"
            ):
                budget.parse(workflow, "second")

    def test_bash_quoted_string_analysis_has_a_complexity_budget(self) -> None:
        with mock.patch.object(
            codeql_preflight, "MAX_BASH_QUOTED_PREFIX_SCAN_CHARS", 10
        ):
            with self.assertRaisesRegex(
                codeql_preflight.InspectionError, "quoted-string analysis"
            ):
                codeql_preflight.contains_codeql_cli(
                    'echo "" "" "" ""',
                    "bash",
                )

    def test_shell_run_step_has_a_byte_budget(self) -> None:
        with mock.patch.object(codeql_preflight, "MAX_SHELL_RUN_BYTES", 4):
            with self.assertRaisesRegex(codeql_preflight.InspectionError, "run step"):
                codeql_preflight.contains_codeql_cli("echo ok", "bash")

    def test_direct_codeql_regex_has_a_command_segment_budget(self) -> None:
        with mock.patch.object(
            codeql_preflight, "MAX_CODEQL_COMMAND_SEGMENT_BYTES", 10
        ):
            with self.assertRaisesRegex(
                codeql_preflight.InspectionError, "segment containing CodeQL"
            ):
                codeql_preflight.contains_codeql_cli(
                    "echo padding codeql database create db",
                    "bash",
                )

    def test_workflow_byte_budget_enforces_its_deadline(self) -> None:
        budget = codeql_preflight.WorkflowByteBudget(deadline=1.0)
        with mock.patch.object(codeql_preflight.time, "monotonic", return_value=2.0):
            with self.assertRaisesRegex(
                codeql_preflight.InspectionError, "Workflow inspection exceeded"
            ):
                budget.parse(
                    "jobs: {test: {runs-on: ubuntu-latest, steps: []}}",
                    "expired",
                )

    def test_safe_root_and_path_reject_relative_root_anchor_missing_and_escape(
        self,
    ) -> None:
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "absolute path"):
            codeql_preflight.require_safe_root(Path("relative"))

        anchor = Path(Path.cwd().anchor)
        with self.assertRaisesRegex(
            codeql_preflight.InspectionError, "filesystem root"
        ):
            codeql_preflight.require_safe_root(anchor)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codeql_preflight.require_safe_root(root)
            with self.assertRaisesRegex(
                codeql_preflight.InspectionError, "does not exist"
            ):
                codeql_preflight.require_safe_root(root / "missing")
            with self.assertRaisesRegex(codeql_preflight.InspectionError, "escapes"):
                codeql_preflight.require_safe_path(root.parent, root)
            codeql_preflight.require_safe_path(root / "future" / "file.yml", root)

    def test_reparse_point_uses_platform_file_attributes(self) -> None:
        fake_stat = mock.Mock(st_file_attributes=4)
        with (
            mock.patch.object(codeql_preflight.Path, "lstat", return_value=fake_stat),
            mock.patch.object(
                codeql_preflight.stat,
                "FILE_ATTRIBUTE_REPARSE_POINT",
                4,
                create=True,
            ),
        ):
            self.assertTrue(codeql_preflight.is_reparse_point(Path("entry")))

    def test_local_workflow_loader_rejects_file_root_encoding_size_and_count(
        self,
    ) -> None:
        workflow = "jobs: {test: {runs-on: ubuntu-latest, steps: []}}"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_root = root / ".github" / "workflows"
            workflow_root.parent.mkdir(parents=True)
            workflow_root.write_text("not a directory", encoding="utf-8")
            with self.assertRaisesRegex(
                codeql_preflight.InspectionError, "not a directory"
            ):
                codeql_preflight.load_local_workflows(root)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_root = root / ".github" / "workflows"
            workflow_root.mkdir(parents=True)
            invalid = workflow_root / "invalid.yml"
            invalid.write_bytes(b"\xff")
            with self.assertRaisesRegex(
                codeql_preflight.InspectionError, "not valid UTF-8"
            ):
                codeql_preflight.load_local_workflows(root)

            invalid.write_text(workflow, encoding="utf-8")
            with (
                mock.patch.object(codeql_preflight, "MAX_WORKFLOW_BYTES", 8),
                self.assertRaisesRegex(
                    codeql_preflight.InspectionError, "byte safety cap"
                ),
            ):
                codeql_preflight.load_local_workflows(root)

            with (
                mock.patch.object(codeql_preflight, "MAX_LOCAL_WORKFLOWS", 0),
                self.assertRaisesRegex(
                    codeql_preflight.InspectionError, "workflow count"
                ),
            ):
                codeql_preflight.load_local_workflows(root)

    def test_local_workflow_loader_rechecks_bytes_and_wraps_read_errors(self) -> None:
        workflow = "jobs: {test: {runs-on: ubuntu-latest, steps: []}}"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_root = root / ".github" / "workflows"
            workflow_root.mkdir(parents=True)
            path = workflow_root / "ci.yml"
            path.write_text(workflow, encoding="utf-8")
            original_stat = Path.stat

            def small_stat(candidate: Path, *, follow_symlinks: bool = True) -> object:
                if candidate == path:
                    return mock.Mock(
                        st_size=0,
                        st_mode=original_stat(
                            candidate, follow_symlinks=follow_symlinks
                        ).st_mode,
                    )
                return original_stat(candidate, follow_symlinks=follow_symlinks)

            with (
                mock.patch.object(codeql_preflight, "require_safe_path"),
                mock.patch.object(codeql_preflight.Path, "stat", small_stat),
                mock.patch.object(codeql_preflight, "MAX_WORKFLOW_BYTES", 8),
                self.assertRaisesRegex(
                    codeql_preflight.InspectionError, "byte safety cap"
                ),
            ):
                codeql_preflight.load_local_workflows(root)

            with (
                mock.patch.object(
                    codeql_preflight.Path,
                    "read_bytes",
                    side_effect=OSError("read failed"),
                ),
                self.assertRaisesRegex(
                    codeql_preflight.InspectionError, "Could not read"
                ),
            ):
                codeql_preflight.load_local_workflows(root)


class WorkflowResolverTests(unittest.TestCase):
    WORKFLOW = "jobs: {test: {runs-on: ubuntu-latest, steps: []}}"

    def test_local_and_exact_local_calls_validate_paths_and_cache_content(self) -> None:
        path = ".github/workflows/reusable.yml"
        signals = codeql_preflight.WorkflowSignals(False, ())
        client = mock.Mock()
        client.raw.return_value = self.WORKFLOW
        resolver = codeql_preflight.WorkflowResolver(client, {path: signals})

        local = resolver.resolve(f"./{path}", codeql_preflight.WorkflowContext("local"))
        self.assertEqual(local.identity, ("local", path))
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "does not exist"):
            resolver.resolve(
                "./.github/workflows/missing.yml",
                codeql_preflight.WorkflowContext("local"),
            )
        for call, message in (
            ("./.github/workflows/../escape.yml", "traversal"),
            ("./.github/workflows/nested/reusable.yml", "not a direct"),
        ):
            with self.subTest(call=call):
                with self.assertRaisesRegex(codeql_preflight.InspectionError, message):
                    resolver.resolve(call, codeql_preflight.WorkflowContext("local"))

        exact_context = codeql_preflight.WorkflowContext(
            "exact", "Owner", "Repo", "A" * 40
        )
        exact = resolver.resolve(f"./{path}", exact_context)
        repeated = resolver.resolve(f"./{path}", exact_context)
        self.assertEqual(exact.identity, repeated.identity)
        client.raw.assert_called_once()

        seeded = codeql_preflight.WorkflowSignals(True, ())
        resolver.seed_exact_workflow("Owner", "Repo", "A" * 40, path, seeded)
        self.assertIs(
            resolver.resolve(f"./{path}", exact_context).signals,
            seeded,
        )

    def test_external_calls_resolve_ref_and_content_once_and_reject_bad_commits(
        self,
    ) -> None:
        client = mock.Mock()
        client.json.return_value = {"sha": "a" * 40}
        client.raw.return_value = self.WORKFLOW
        resolver = codeql_preflight.WorkflowResolver(client, {})
        call = "Owner/Repo/.github/workflows/reusable.yml@main"

        first = resolver.resolve(call, codeql_preflight.WorkflowContext("local"))
        second = resolver.resolve(call, codeql_preflight.WorkflowContext("local"))

        self.assertEqual(first.identity, second.identity)
        self.assertEqual(first.context.kind, "exact")
        client.json.assert_called_once()
        client.raw.assert_called_once()

        invalid_client = mock.Mock()
        invalid_client.json.return_value = {"sha": "short"}
        invalid = codeql_preflight.WorkflowResolver(invalid_client, {})
        with self.assertRaisesRegex(codeql_preflight.InspectionError, "full object ID"):
            invalid.resolve(call, codeql_preflight.WorkflowContext("local"))

        for invalid_call, message in (
            ("owner/repo/.github/workflows/../escape.yml@main", "traversal"),
            ("owner/repo/.github/workflows/nested/file.yml@main", "not a direct"),
            ("unsupported", "Unsupported"),
        ):
            with self.subTest(call=invalid_call):
                with self.assertRaisesRegex(codeql_preflight.InspectionError, message):
                    resolver.resolve(
                        invalid_call, codeql_preflight.WorkflowContext("local")
                    )

    def test_remote_default_branch_validates_commit_tree_count_blob_and_content(
        self,
    ) -> None:
        commit = "a" * 40
        blob = "b" * 40
        client = mock.Mock()
        client.json.side_effect = [
            {"sha": commit},
            {
                "truncated": False,
                "tree": [
                    {"type": "tree", "path": ".github/workflows"},
                    {"type": "blob", "path": "README.md", "sha": "c" * 40},
                    {
                        "type": "blob",
                        "path": ".github/workflows/ci.yml",
                        "sha": blob,
                    },
                ],
            },
        ]
        client.raw.return_value = self.WORKFLOW

        resolved_commit, workflows = codeql_preflight.load_remote_default_branch(
            client, "owner", "repo", "main"
        )

        self.assertEqual(resolved_commit, commit)
        self.assertEqual(set(workflows), {".github/workflows/ci.yml"})
        client.raw.assert_called_once_with(f"repos/owner/repo/git/blobs/{blob}")

        invalid_responses = [
            ([{}, {}], "full Git object ID"),
            ([{"sha": commit}, {"truncated": True}], "invalid, or truncated"),
            ([{"sha": commit}, {"truncated": False, "tree": {}}], "no tree array"),
            (
                [
                    {"sha": commit},
                    {
                        "truncated": False,
                        "tree": [
                            {
                                "type": "blob",
                                "path": ".github/workflows/ci.yml",
                                "sha": "short",
                            }
                        ],
                    },
                ],
                "invalid blob ID",
            ),
        ]
        for responses, message in invalid_responses:
            invalid_client = mock.Mock()
            invalid_client.json.side_effect = responses
            with self.subTest(message=message):
                with self.assertRaisesRegex(codeql_preflight.InspectionError, message):
                    codeql_preflight.load_remote_default_branch(
                        invalid_client, "owner", "repo", "main"
                    )

        many_client = mock.Mock()
        many_client.json.side_effect = [
            {"sha": commit},
            {
                "truncated": False,
                "tree": [
                    {
                        "type": "blob",
                        "path": ".github/workflows/ci.yml",
                        "sha": blob,
                    }
                ],
            },
        ]
        with (
            mock.patch.object(codeql_preflight, "MAX_REMOTE_WORKFLOWS", 0),
            self.assertRaisesRegex(codeql_preflight.InspectionError, "workflow count"),
        ):
            codeql_preflight.load_remote_default_branch(
                many_client, "owner", "repo", "main"
            )


class GitHubClientTests(unittest.TestCase):
    def client(self) -> object:
        with mock.patch.object(
            codeql_preflight, "resolve_path_executable", return_value="/tools/gh"
        ):
            return codeql_preflight.GitHubClient("github.com")

    def test_deeply_nested_api_json_fails_closed(self) -> None:
        client = codeql_preflight.GitHubClient("github.com")
        payload = "[" * 20_000 + "]" * 20_000
        with mock.patch.object(client, "_run", return_value=payload):
            with self.assertRaisesRegex(
                codeql_preflight.InspectionError, "nesting safety cap"
            ):
                client.json("repos/octo/repo")

    def test_json_nesting_characters_inside_strings_are_ignored(self) -> None:
        client = codeql_preflight.GitHubClient("github.com")
        payload = json.dumps({"value": "[" * 20_000})
        with mock.patch.object(client, "_run", return_value=payload):
            self.assertEqual(client.json("repos/octo/repo"), json.loads(payload))

        escaped_payload = '{"value":"escaped \\" quote and [ bracket"}'
        with mock.patch.object(client, "_run", return_value=escaped_payload):
            self.assertEqual(
                client.json("repos/octo/repo"), json.loads(escaped_payload)
            )

    def test_client_requires_gh_and_rejects_invalid_json(self) -> None:
        with (
            mock.patch.object(
                codeql_preflight, "resolve_path_executable", return_value=None
            ),
            self.assertRaisesRegex(codeql_preflight.InspectionError, "not found"),
        ):
            codeql_preflight.GitHubClient("github.com")

        client = cast(GitHubClientProtocol, self.client())
        with (
            mock.patch.object(client, "_run", return_value="{"),
            self.assertRaisesRegex(codeql_preflight.InspectionError, "invalid JSON"),
        ):
            client.json("repos/octo/repo")

    def test_api_request_and_deadline_caps_fail_before_process_start(self) -> None:
        client = cast(GitHubClientProtocol, self.client())
        client.request_count = codeql_preflight.MAX_GH_REQUESTS
        with self.assertRaisesRegex(
            codeql_preflight.InspectionError, "request safety cap"
        ):
            client.raw("repos/octo/repo")

        client.request_count = 0
        client.deadline = 1.0
        with (
            mock.patch.object(codeql_preflight.time, "monotonic", return_value=2.0),
            mock.patch.object(codeql_preflight.subprocess, "run") as run,
            self.assertRaisesRegex(
                codeql_preflight.InspectionError, "second safety cap"
            ),
        ):
            client.raw("repos/octo/repo")
        run.assert_not_called()

    def test_api_process_missing_failure_raw_header_and_total_size_cap(self) -> None:
        client = cast(GitHubClientProtocol, self.client())
        with (
            mock.patch.object(
                codeql_preflight.subprocess, "run", side_effect=FileNotFoundError()
            ),
            self.assertRaisesRegex(codeql_preflight.InspectionError, "not installed"),
        ):
            client.raw("repos/octo/repo")

        def fail_request(*_args: object, **kwargs: object) -> mock.Mock:
            cast(BinaryIO, kwargs["stderr"]).write(b"denied")
            return mock.Mock(returncode=1)

        with (
            mock.patch.object(
                codeql_preflight.subprocess, "run", side_effect=fail_request
            ),
            self.assertRaisesRegex(codeql_preflight.InspectionError, "denied"),
        ):
            client.raw("repos/octo/repo")

        def successful_raw(*args: object, **kwargs: object) -> mock.Mock:
            command = cast(list[str], args[0])
            self.assertIn("Accept: application/vnd.github.raw+json", command)
            cast(BinaryIO, kwargs["stdout"]).write(b"workflow")
            return mock.Mock(returncode=0)

        with mock.patch.object(
            codeql_preflight.subprocess, "run", side_effect=successful_raw
        ):
            self.assertEqual(client.raw("repos/octo/repo"), "workflow")

        client.response_bytes = 0

        def bounded_response(*_args: object, **kwargs: object) -> mock.Mock:
            cast(BinaryIO, kwargs["stdout"]).write(b"12345")
            cast(BinaryIO, kwargs["stderr"]).write(b"67890")
            return mock.Mock(returncode=0)

        with (
            mock.patch.object(codeql_preflight, "MAX_TOTAL_GH_RESPONSE_BYTES", 9),
            mock.patch.object(
                codeql_preflight.subprocess, "run", side_effect=bounded_response
            ),
            self.assertRaisesRegex(codeql_preflight.InspectionError, "total response"),
        ):
            client.raw("repos/octo/repo")

    def test_api_timeout_fails_closed(self) -> None:
        client = codeql_preflight.GitHubClient("github.com")
        with mock.patch.object(
            codeql_preflight.subprocess,
            "run",
            side_effect=codeql_preflight.subprocess.TimeoutExpired("gh", 60),
        ) as run_mock:
            with self.assertRaisesRegex(codeql_preflight.InspectionError, "timed out"):
                client.json("repos/octo/repo")
        command = run_mock.call_args.args[0]
        self.assertTrue(Path(command[0]).is_absolute())
        self.assertEqual(
            command[1:],
            ["api", "--hostname", "github.com", "repos/octo/repo"],
        )
        self.assertFalse(run_mock.call_args.kwargs.get("shell", False))
        self.assertEqual(run_mock.call_args.kwargs["timeout"], 60)

    def test_api_output_is_spooled_and_size_limited(self) -> None:
        def write_oversized_output(*args: object, **kwargs: object) -> mock.Mock:
            del args
            cast(BinaryIO, kwargs["stdout"]).write(b"x" * 65)
            return mock.Mock(returncode=0)

        client = codeql_preflight.GitHubClient("github.com")
        with (
            mock.patch.object(codeql_preflight, "MAX_GH_RESPONSE_BYTES", 64),
            mock.patch.object(
                codeql_preflight.subprocess,
                "run",
                side_effect=write_oversized_output,
            ),
        ):
            with self.assertRaisesRegex(
                codeql_preflight.InspectionError, "response byte"
            ):
                client.raw("repos/octo/repo")

    def test_api_output_rejects_invalid_utf8(self) -> None:
        def write_invalid_utf8(*_args: object, **kwargs: object) -> mock.Mock:
            cast(BinaryIO, kwargs["stdout"]).write(b"\xff")
            return mock.Mock(returncode=0)

        client = cast(GitHubClientProtocol, self.client())
        with (
            mock.patch.object(
                codeql_preflight.subprocess,
                "run",
                side_effect=write_invalid_utf8,
            ),
            self.assertRaisesRegex(codeql_preflight.InspectionError, "valid UTF-8"),
        ):
            client.raw("repos/octo/repo")


class InputValidationTests(unittest.TestCase):
    def test_rejects_repository_path_components(self) -> None:
        for repository in ("../repo", "owner/..", "./repo", "owner/."):
            with self.subTest(repository=repository):
                with self.assertRaisesRegex(
                    codeql_preflight.InspectionError, "explicit OWNER/REPO"
                ):
                    codeql_preflight.split_repository(repository)

    def test_rejects_reusable_workflow_repository_path_components(self) -> None:
        client = mock.Mock()
        resolver = codeql_preflight.WorkflowResolver(client, {})
        for call in (
            "../repo/.github/workflows/ci.yml@main",
            "owner/../.github/workflows/ci.yml@main",
        ):
            with self.subTest(call=call):
                with self.assertRaisesRegex(
                    codeql_preflight.InspectionError,
                    "invalid repository identifier",
                ):
                    resolver.resolve(
                        call,
                        codeql_preflight.WorkflowContext("local"),
                    )
        client.json.assert_not_called()

    def test_rejects_non_github_com_hostname_before_api_access(self) -> None:
        args = argparse.Namespace(
            repo_root=str(PLUGIN_ROOT),
            repository="octo/repo",
            default_branch="main",
            hostname="ghe.example.test",
            confirm_no_external_codeql=True,
        )
        with mock.patch.object(codeql_preflight, "GitHubClient") as client:
            with self.assertRaisesRegex(
                codeql_preflight.InspectionError, "GitHub.com only"
            ):
                codeql_preflight.run(args)
        client.assert_not_called()

    def test_rejects_unsafe_repository_root_before_api_access(self) -> None:
        args = argparse.Namespace(
            repo_root=str(PLUGIN_ROOT),
            repository="octo/repo",
            default_branch="main",
            hostname="github.com",
            confirm_no_external_codeql=True,
        )
        with (
            mock.patch.object(
                codeql_preflight,
                "require_safe_root",
                side_effect=codeql_preflight.InspectionError("linked root"),
            ) as safe_root,
            mock.patch.object(codeql_preflight, "GitHubClient") as client,
        ):
            with self.assertRaisesRegex(
                codeql_preflight.InspectionError, "linked root"
            ):
                codeql_preflight.run(args)
        safe_root.assert_called_once()
        client.assert_not_called()


class DefaultSetupDecisionTests(unittest.TestCase):
    def test_configured_default_setup_marks_uninspected_evidence_unknown(self) -> None:
        class FakeClient:
            request_count = 0
            endpoints: list[str] = []

            def __init__(
                self, hostname: str, *, forbidden_root: Path | None = None
            ) -> None:
                del hostname, forbidden_root

            def json(self, endpoint: str) -> object:
                self.endpoints.append(endpoint)
                return {"state": "configured"}

        args = argparse.Namespace(
            repo_root=str(PLUGIN_ROOT),
            repository="octo/repo",
            default_branch="main",
            hostname="github.com",
            confirm_no_external_codeql=False,
        )
        with mock.patch.object(codeql_preflight, "GitHubClient", FakeClient):
            result = codeql_preflight.run(args)

        self.assertEqual(
            FakeClient.endpoints,
            ["repos/octo/repo/code-scanning/default-setup"],
        )
        self.assertIsNone(result["advanced_workflows"])
        self.assertIsNone(result["has_codeql_analysis"])
        self.assertFalse(result["workflow_inspection_performed"])
        self.assertFalse(result["analysis_inspection_performed"])

    def test_unknown_default_setup_state_fails_closed(self) -> None:
        class FakeClient:
            request_count = 0
            deadline = float("inf")

            def __init__(
                self, hostname: str, *, forbidden_root: Path | None = None
            ) -> None:
                del hostname, forbidden_root

            def json(self, endpoint: str) -> object:
                self.request_count += 1
                if endpoint.endswith("code-scanning/default-setup"):
                    return {"state": "future-state"}
                raise AssertionError(endpoint)

        args = argparse.Namespace(
            repo_root=str(PLUGIN_ROOT),
            repository="octo/repo",
            default_branch="main",
            hostname="github.com",
            confirm_no_external_codeql=True,
        )
        with mock.patch.object(codeql_preflight, "GitHubClient", FakeClient):
            with self.assertRaisesRegex(
                codeql_preflight.InspectionError, "unknown state"
            ):
                codeql_preflight.run(args)

    def test_requires_confirmation_when_direct_inspection_finds_no_codeql(self) -> None:
        class FakeClient:
            request_count = 0
            deadline = float("inf")

            def __init__(
                self, hostname: str, *, forbidden_root: Path | None = None
            ) -> None:
                del hostname, forbidden_root

            def json(self, endpoint: str) -> object:
                if endpoint.endswith("code-scanning/default-setup"):
                    return {"state": "not-configured"}
                if "code-scanning/analyses" in endpoint:
                    return []
                raise AssertionError(endpoint)

        args = argparse.Namespace(
            repo_root=str(PLUGIN_ROOT),
            repository="octo/repo",
            default_branch="main",
            hostname="github.com",
            confirm_no_external_codeql=False,
        )
        patches = (
            mock.patch.object(codeql_preflight, "GitHubClient", FakeClient),
            mock.patch.object(
                codeql_preflight, "load_local_workflows", return_value={}
            ),
            mock.patch.object(
                codeql_preflight,
                "load_remote_default_branch",
                return_value=("a" * 40, {}),
            ),
        )
        with patches[0], patches[1], patches[2]:
            with self.assertRaisesRegex(
                codeql_preflight.InspectionError, "not explicitly confirmed"
            ):
                codeql_preflight.run(args)

        args.confirm_no_external_codeql = True
        with patches[0], patches[1], patches[2]:
            result = codeql_preflight.run(args)
        self.assertEqual(result["decision"], "may-offer-default-setup")
        self.assertTrue(result["external_codeql_absence_confirmed"])

    def test_quoted_heredoc_marker_cannot_downgrade_codeql_decision(self) -> None:
        class FakeClient:
            request_count = 0
            deadline = float("inf")

            def __init__(
                self, hostname: str, *, forbidden_root: Path | None = None
            ) -> None:
                del hostname, forbidden_root

            def json(self, endpoint: str) -> object:
                if endpoint.endswith("code-scanning/default-setup"):
                    return {"state": "not-configured"}
                if "code-scanning/analyses" in endpoint:
                    return []
                raise AssertionError(endpoint)

        workflow = """
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - run: |
          echo "example <<EOF"
          codeql database create db
"""
        signals = codeql_preflight.parse_workflow(workflow, "quoted-marker")
        args = argparse.Namespace(
            repo_root=str(PLUGIN_ROOT),
            repository="octo/repo",
            default_branch="main",
            hostname="github.com",
            confirm_no_external_codeql=False,
        )
        patches = (
            mock.patch.object(codeql_preflight, "GitHubClient", FakeClient),
            mock.patch.object(
                codeql_preflight,
                "load_local_workflows",
                return_value={".github/workflows/codeql.yml": signals},
            ),
            mock.patch.object(
                codeql_preflight,
                "load_remote_default_branch",
                return_value=("a" * 40, {}),
            ),
        )
        with patches[0], patches[1], patches[2]:
            with self.assertRaisesRegex(
                codeql_preflight.InspectionError, "not explicitly confirmed"
            ):
                codeql_preflight.run(args)

        args.confirm_no_external_codeql = True
        with patches[0], patches[1], patches[2]:
            result = codeql_preflight.run(args)
        self.assertEqual(result["decision"], "require-explicit-switch-confirmation")

    def test_run_rejects_nondirectory_root_and_invalid_default_setup_response(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root_file = Path(directory) / "repository"
            root_file.write_text("not a directory", encoding="utf-8")
            args = argparse.Namespace(
                repo_root=str(root_file),
                repository="octo/repo",
                default_branch="main",
                hostname="github.com",
                confirm_no_external_codeql=True,
            )
            with (
                mock.patch.object(codeql_preflight, "require_safe_root"),
                mock.patch.object(codeql_preflight, "GitHubClient") as client,
                self.assertRaisesRegex(
                    codeql_preflight.InspectionError, "not a directory"
                ),
            ):
                codeql_preflight.run(args)
            client.assert_not_called()

        class InvalidClient:
            request_count = 0

            def __init__(
                self, hostname: str, *, forbidden_root: Path | None = None
            ) -> None:
                del hostname, forbidden_root

            def json(self, _endpoint: str) -> object:
                return []

        args.repo_root = str(PLUGIN_ROOT)
        with (
            mock.patch.object(codeql_preflight, "GitHubClient", InvalidClient),
            self.assertRaisesRegex(
                codeql_preflight.InspectionError, "invalid response"
            ),
        ):
            codeql_preflight.run(args)

    def test_remote_workflows_are_seeded_inspected_and_combined_with_analysis(
        self,
    ) -> None:
        class FakeClient:
            request_count = 0
            deadline = float("inf")

            def __init__(
                self, hostname: str, *, forbidden_root: Path | None = None
            ) -> None:
                del hostname, forbidden_root

            def json(self, endpoint: str) -> object:
                self.request_count += 1
                if endpoint.endswith("code-scanning/default-setup"):
                    return {"state": "not-configured"}
                if "code-scanning/analyses" in endpoint:
                    return [{"id": 1}]
                raise AssertionError(endpoint)

        commit = "a" * 40
        path = ".github/workflows/codeql.yml"
        signals = codeql_preflight.WorkflowSignals(True, ())
        args = argparse.Namespace(
            repo_root=str(PLUGIN_ROOT),
            repository="octo/repo",
            default_branch="main",
            hostname="github.com",
            confirm_no_external_codeql=True,
        )
        with (
            mock.patch.object(codeql_preflight, "GitHubClient", FakeClient),
            mock.patch.object(
                codeql_preflight, "load_local_workflows", return_value={}
            ),
            mock.patch.object(
                codeql_preflight,
                "load_remote_default_branch",
                return_value=(commit, {path: signals}),
            ),
        ):
            result = codeql_preflight.run(args)

        self.assertEqual(result["decision"], "require-explicit-switch-confirmation")
        self.assertTrue(result["has_codeql_analysis"])
        self.assertEqual(result["advanced_workflows"], [f"remote:{path}@{commit}"])

    def test_invalid_analyses_response_fails_closed(self) -> None:
        class FakeClient:
            request_count = 0
            deadline = float("inf")

            def __init__(
                self, hostname: str, *, forbidden_root: Path | None = None
            ) -> None:
                del hostname, forbidden_root

            def json(self, endpoint: str) -> object:
                if endpoint.endswith("code-scanning/default-setup"):
                    return {"state": "not-configured"}
                if "code-scanning/analyses" in endpoint:
                    return {}
                raise AssertionError(endpoint)

        args = argparse.Namespace(
            repo_root=str(PLUGIN_ROOT),
            repository="octo/repo",
            default_branch="main",
            hostname="github.com",
            confirm_no_external_codeql=True,
        )
        with (
            mock.patch.object(codeql_preflight, "GitHubClient", FakeClient),
            mock.patch.object(
                codeql_preflight, "load_local_workflows", return_value={}
            ),
            mock.patch.object(
                codeql_preflight,
                "load_remote_default_branch",
                return_value=("a" * 40, {}),
            ),
            self.assertRaisesRegex(
                codeql_preflight.InspectionError, "analyses endpoint"
            ),
        ):
            codeql_preflight.run(args)


class CommandLineTests(unittest.TestCase):
    def test_parse_args_and_main_success_and_failure(self) -> None:
        argv = [
            str(SCRIPT_PATH),
            "--repo-root",
            str(PLUGIN_ROOT),
            "--repository",
            "octo/repo",
            "--default-branch",
            "main",
            "--confirm-no-external-codeql",
        ]
        with mock.patch.object(sys, "argv", argv):
            args = codeql_preflight.parse_args()
        self.assertTrue(args.confirm_no_external_codeql)
        self.assertEqual(args.hostname, "github.com")

        output = StringIO()
        with (
            mock.patch.object(codeql_preflight, "parse_args", return_value=args),
            mock.patch.object(
                codeql_preflight, "run", return_value={"decision": "safe"}
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(codeql_preflight.main(), 0)
        self.assertEqual(json.loads(output.getvalue()), {"decision": "safe"})

        output = StringIO()
        with (
            mock.patch.object(codeql_preflight, "parse_args", return_value=args),
            mock.patch.object(
                codeql_preflight,
                "run",
                side_effect=codeql_preflight.InspectionError("blocked"),
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(codeql_preflight.main(), 2)
        failure = json.loads(output.getvalue())
        self.assertFalse(failure["inspection_complete"])
        self.assertEqual(failure["decision"], "inconclusive")

    def test_script_entrypoint_returns_inconclusive_status(self) -> None:
        argv = [
            str(SCRIPT_PATH),
            "--repo-root",
            str(PLUGIN_ROOT),
            "--repository",
            "octo/repo",
            "--default-branch",
            "main",
            "--hostname",
            "invalid.example",
        ]
        output = StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            redirect_stdout(output),
            self.assertRaises(SystemExit) as raised,
        ):
            runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(json.loads(output.getvalue())["decision"], "inconclusive")


class PlaceholderContractTests(unittest.TestCase):
    def test_assets_use_only_namespaced_internal_markers(self) -> None:
        assets = PLUGIN_ROOT / "skills" / "repo-scaffold" / "assets"
        unprefixed = re.compile(
            r"\{\{(?:PROJECT_NAME|USER|OWNER|REPO|DEFAULT_BRANCH|"
            r"DEPENDABOT_PACKAGE_UPDATES)\}\}"
        )
        for path in assets.rglob("*"):
            if not path.is_file():
                continue
            with self.subTest(path=path.relative_to(PLUGIN_ROOT)):
                self.assertIsNone(unprefixed.search(path.read_text(encoding="utf-8")))

    def test_project_documentation_tokens_do_not_match_internal_markers(self) -> None:
        internal = {"{{REPO_SCAFFOLD_PROJECT_NAME}}"}
        documentation = "Template docs: {{PROJECT_NAME}} and {{USER}}"
        self.assertFalse(any(marker in documentation for marker in internal))

    def test_default_branch_glob_contract_masks_expression_openers(self) -> None:
        generation = (
            PLUGIN_ROOT
            / "skills"
            / "repo-scaffold"
            / "references"
            / "scaffold-generation.md"
        ).read_text(encoding="utf-8")
        self.assertIn("Replace every `${{` with `[$*]{{`", generation)

        branch = "${{true}}"
        pattern = branch.replace("!", r"\!").replace("+", r"\+")
        pattern = pattern.replace("${{", "[$*]{{")
        self.assertEqual(pattern, "[$*]{{true}}")
        self.assertNotIn("${{", pattern)

    def test_dependabot_workflow_never_disables_existing_auto_merge(self) -> None:
        workflow = (
            PLUGIN_ROOT
            / "skills"
            / "repo-scaffold"
            / "assets"
            / "workflows"
            / "dependabot-auto-merge.yml"
        ).read_text(encoding="utf-8")
        self.assertNotIn("--disable-auto", workflow)
        self.assertNotIn("enabledBy", workflow)
        self.assertIn(
            "version-update:semver-patch|version-update:semver-minor", workflow
        )
        self.assertIn("--match-head-commit", workflow)

    def test_release_workflow_never_clobbers_a_published_release(self) -> None:
        workflow = (
            PLUGIN_ROOT
            / "skills"
            / "repo-scaffold"
            / "assets"
            / "workflows"
            / "release.yml"
        ).read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"(?m)^\s+gh release upload", workflow)), 2)
        release_commands = re.findall(
            r"(?m)^\s+gh release (?:create|edit|upload).*$", workflow
        )
        self.assertEqual(len(release_commands), 4)
        self.assertTrue(
            all('--repo "${GITHUB_REPOSITORY}"' in line for line in release_commands)
        )
        self.assertIn("already published and mutable", workflow)
        self.assertNotIn("Backward compatibility for Releases", workflow)
        self.assertIn('"${#artifact_tag}" -gt 120', workflow)
        self.assertIn("sha256sum", workflow)

    def test_release_engine_supports_verified_manual_dispatch(self) -> None:
        installed_path = PLUGIN_ROOT / ".github" / "workflows" / "release.yml"
        asset_path = (
            PLUGIN_ROOT
            / "skills"
            / "repo-scaffold"
            / "assets"
            / "workflows"
            / "release.yml"
        )

        for path in (installed_path, asset_path):
            document = codeql_preflight.yaml.load(
                path.read_text(encoding="utf-8"),
                Loader=codeql_preflight.UniqueKeyBaseLoader,
            )
            with self.subTest(workflow=path.as_posix()):
                self.assertEqual(
                    set(document["on"]), {"workflow_call", "workflow_dispatch"}
                )
                for trigger in ("workflow_call", "workflow_dispatch"):
                    inputs = document["on"][trigger]["inputs"]
                    self.assertEqual(set(inputs), {"tag", "commit_sha"})
                    self.assertEqual(inputs["tag"]["required"], "true")
                    self.assertEqual(inputs["commit_sha"]["required"], "true")

    def test_release_please_mutations_are_serialized_per_branch(self) -> None:
        workflow = (
            PLUGIN_ROOT
            / "skills"
            / "repo-scaffold"
            / "assets"
            / "workflows"
            / "release-please.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("group: release-please-${{ github.ref }}", workflow)
        self.assertIn("cancel-in-progress: false", workflow)

    def test_commitlint_excludes_merge_commits_for_every_event(self) -> None:
        installed_path = PLUGIN_ROOT / ".github" / "workflows" / "commitlint.yml"
        asset_path = (
            PLUGIN_ROOT
            / "skills"
            / "repo-scaffold"
            / "assets"
            / "workflows"
            / "commitlint.yml"
        )

        for path in (installed_path, asset_path):
            workflow = path.read_text(encoding="utf-8")
            with self.subTest(workflow=path.as_posix()):
                self.assertIn(
                    "const revListArgs = ['rev-list', '--reverse', '--no-merges'];",
                    workflow,
                )
                self.assertNotIn(
                    "EVENT_NAME === 'merge_group') revListArgs.push('--no-merges')",
                    workflow,
                )
                self.assertIn(
                    "if (process.env.EVENT_NAME === 'pull_request')",
                    workflow,
                )

    def test_link_workflows_narrowly_ignore_prospective_release_compare(self) -> None:
        installed_path = PLUGIN_ROOT / ".github" / "workflows" / "links.yml"
        asset_path = (
            PLUGIN_ROOT
            / "skills"
            / "repo-scaffold"
            / "assets"
            / "workflows"
            / "links.yml"
        )

        for path in (installed_path, asset_path):
            document = codeql_preflight.yaml.load(
                path.read_text(encoding="utf-8"),
                Loader=codeql_preflight.UniqueKeyBaseLoader,
            )
            steps = document["jobs"]["links"]["steps"]
            with self.subTest(workflow=path.as_posix()):
                checkout = next(step for step in steps if step["name"] == "Checkout")
                self.assertEqual(checkout["with"]["fetch-tags"], "true")
                self.assertEqual(checkout["with"]["persist-credentials"], "false")

                prepare = next(
                    step
                    for step in steps
                    if step["name"] == "Prepare Release Please comparison exception"
                )
                condition = prepare["if"]
                self.assertIn("github.event_name == 'pull_request'", condition)
                self.assertIn(
                    "github.event.pull_request.head.repo.full_name == github.repository",
                    condition,
                )
                self.assertIn(
                    "startsWith(github.head_ref, 'release-please--branches--')",
                    condition,
                )
                self.assertEqual(
                    prepare["env"]["REPOSITORY"], "${{ github.repository }}"
                )
                script = prepare["run"]
                for required_guard in (
                    ".release-please-manifest.json",
                    "expected exactly one prospective release heading",
                    "comparison source tag does not exist",
                    "prospective tag already exists",
                    "re.escape(comparison_url)",
                    ".lycheeignore",
                ):
                    self.assertIn(required_guard, script)

    def test_release_exception_ignores_only_the_exact_future_comparison(self) -> None:
        workflow_path = PLUGIN_ROOT / ".github" / "workflows" / "links.yml"
        document = codeql_preflight.yaml.load(
            workflow_path.read_text(encoding="utf-8"),
            Loader=codeql_preflight.UniqueKeyBaseLoader,
        )
        prepare = next(
            step
            for step in document["jobs"]["links"]["steps"]
            if step["name"] == "Prepare Release Please comparison exception"
        )
        embedded_python = prepare["run"].split("python3 - <<'PY'\n", 1)[1]
        embedded_python = embedded_python.rsplit("\nPY", 1)[0]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / ".release-please-manifest.json"
            changelog_path = root / "CHANGELOG.md"
            ignore_path = root / ".lycheeignore"
            manifest_path.write_text('{".": "1.1.1"}\n', encoding="utf-8")
            changelog_path.write_text("# Changelog\n", encoding="utf-8")
            ignore_path.write_text(r"^https://example\.com$", encoding="utf-8")

            git_commands = (
                ["git", "init", "--quiet"],
                ["git", "config", "user.name", "Repo Scaffold Tests"],
                ["git", "config", "user.email", "tests@example.invalid"],
                ["git", "add", ".release-please-manifest.json", "CHANGELOG.md"],
                ["git", "commit", "--quiet", "-m", "test: seed release"],
                ["git", "tag", "v1.1.1"],
            )
            for command in git_commands:
                subprocess.run(command, cwd=root, check=True)

            repository = "MinhThang1009/repo-scaffold-plugin"
            comparison_url = f"https://github.com/{repository}/compare/v1.1.1...v1.2.0"
            manifest_path.write_text('{".": "1.2.0"}\n', encoding="utf-8")
            changelog_path.write_text(
                f"# Changelog\n\n## [1.2.0]({comparison_url}) (2026-08-11)\n",
                encoding="utf-8",
            )

            environment = {"REPOSITORY": repository}
            with (
                mock.patch.object(Path, "cwd", return_value=root),
                mock.patch.dict(os.environ, environment, clear=False),
            ):
                exec(compile(embedded_python, "links.yml", "exec"), {})
                exec(compile(embedded_python, "links.yml", "exec"), {})

            expected_entry = f"^{re.escape(comparison_url)}$"
            self.assertEqual(
                ignore_path.read_text(encoding="utf-8").splitlines(),
                [r"^https://example\.com$", expected_entry],
            )

    def test_codeql_advanced_setup_is_pinned_and_repository_managed(self) -> None:
        installed_path = PLUGIN_ROOT / ".github" / "workflows" / "codeql.yml"
        asset_path = (
            PLUGIN_ROOT
            / "skills"
            / "repo-scaffold"
            / "assets"
            / "workflows"
            / "codeql.yml"
        )
        codeql_shas = {}

        for path in (installed_path, asset_path):
            workflow = path.read_text(encoding="utf-8")
            document = codeql_preflight.yaml.load(
                workflow,
                Loader=codeql_preflight.UniqueKeyBaseLoader,
            )
            with self.subTest(workflow=path.as_posix()):
                self.assertEqual(document["permissions"]["actions"], "read")
                self.assertEqual(document["permissions"]["contents"], "read")
                self.assertEqual(document["permissions"]["packages"], "read")
                self.assertNotIn("security-events", document["permissions"])
                self.assertEqual(
                    document["jobs"]["analyze"]["permissions"],
                    {
                        "actions": "read",
                        "contents": "read",
                        "packages": "read",
                        "security-events": "write",
                    },
                )
                self.assertEqual(
                    set(document["on"]), {"push", "pull_request", "schedule"}
                )
                codeql_uses = re.findall(
                    r"github/codeql-action/(init|autobuild|analyze)@([0-9a-f]{40})",
                    workflow,
                )
                self.assertEqual(
                    [action for action, _sha in codeql_uses],
                    ["init", "autobuild", "analyze"],
                )
                self.assertEqual(len({sha for _action, sha in codeql_uses}), 1)
                codeql_shas[path] = codeql_uses[0][1]
                self.assertIn("if: contains(fromJSON(", workflow)
                init_step = next(
                    step
                    for step in document["jobs"]["analyze"]["steps"]
                    if step.get("uses", "").startswith("github/codeql-action/init@")
                )
                self.assertEqual(init_step["with"]["dependency-caching"], "true")

        self.assertEqual(codeql_shas[installed_path], codeql_shas[asset_path])

        installed = codeql_preflight.yaml.load(
            installed_path.read_text(encoding="utf-8"),
            Loader=codeql_preflight.UniqueKeyBaseLoader,
        )
        asset = codeql_preflight.yaml.load(
            asset_path.read_text(encoding="utf-8"),
            Loader=codeql_preflight.UniqueKeyBaseLoader,
        )
        self.assertEqual(
            installed["jobs"]["analyze"]["strategy"]["matrix"]["language"],
            ["actions", "python"],
        )
        self.assertEqual(
            asset["jobs"]["analyze"]["strategy"]["matrix"]["language"],
            ["actions", "{{REPO_SCAFFOLD_CODEQL_LANGUAGE}}"],
        )

    def test_scorecard_workflows_use_git_mode_and_publish_sarif(self) -> None:
        installed_path = PLUGIN_ROOT / ".github" / "workflows" / "scorecard.yml"
        asset_path = (
            PLUGIN_ROOT
            / "skills"
            / "repo-scaffold"
            / "assets"
            / "workflows"
            / "scorecard.yml"
        )
        expected_actions = [
            "actions/checkout",
            "ossf/scorecard-action",
            "actions/upload-artifact",
            "github/codeql-action/upload-sarif",
        ]

        documents = {}
        uses_by_path = {}
        for path in (installed_path, asset_path):
            document = codeql_preflight.yaml.load(
                path.read_text(encoding="utf-8"),
                Loader=codeql_preflight.UniqueKeyBaseLoader,
            )
            documents[path] = document
            job = document["jobs"]["analysis"]
            with self.subTest(workflow=path.as_posix()):
                self.assertEqual(document["permissions"], {})
                self.assertEqual(
                    set(document["on"]),
                    {"branch_protection_rule", "push", "schedule"},
                )
                self.assertEqual(job["permissions"]["contents"], "read")
                self.assertEqual(job["permissions"]["id-token"], "write")
                self.assertEqual(job["permissions"]["security-events"], "write")
                action_uses = [step["uses"] for step in job["steps"] if "uses" in step]
                uses_by_path[path] = action_uses
                parsed_uses = [
                    re.fullmatch(r"([^@]+)@([0-9a-f]{40})", uses)
                    for uses in action_uses
                ]
                self.assertTrue(all(match is not None for match in parsed_uses))
                self.assertEqual(
                    [match.group(1) for match in parsed_uses if match is not None],
                    expected_actions,
                )
                scorecard_step = next(
                    step
                    for step in job["steps"]
                    if step.get("uses", "").startswith("ossf/scorecard-action@")
                )
                self.assertEqual(scorecard_step["with"]["file_mode"], "git")
                self.assertEqual(scorecard_step["with"]["publish_results"], "true")
                self.assertEqual(scorecard_step["with"]["results_format"], "sarif")

        self.assertEqual(uses_by_path[installed_path], uses_by_path[asset_path])
        codeql_workflow = (
            PLUGIN_ROOT / ".github" / "workflows" / "codeql.yml"
        ).read_text(encoding="utf-8")
        codeql_sha = re.search(
            r"github/codeql-action/init@([0-9a-f]{40})",
            codeql_workflow,
        )
        self.assertIsNotNone(codeql_sha)
        assert codeql_sha is not None
        self.assertEqual(
            uses_by_path[installed_path][-1],
            f"github/codeql-action/upload-sarif@{codeql_sha.group(1)}",
        )

        self.assertEqual(documents[installed_path]["on"]["push"]["branches"], ["main"])
        self.assertEqual(
            documents[asset_path]["on"]["push"]["branches"],
            ["{{REPO_SCAFFOLD_DEFAULT_BRANCH_GLOB_JSON_ESCAPED}}"],
        )

    def test_regular_workflow_jobs_have_finite_timeouts(self) -> None:
        workflow_root = (
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "assets" / "workflows"
        )
        for path in workflow_root.glob("*.yml"):
            document = codeql_preflight.yaml.load(
                path.read_text(encoding="utf-8"),
                Loader=codeql_preflight.UniqueKeyBaseLoader,
            )
            for job_name, job in document.get("jobs", {}).items():
                if "runs-on" not in job:
                    continue
                with self.subTest(workflow=path.name, job=job_name):
                    timeout = int(job.get("timeout-minutes", "0"))
                    self.assertGreater(timeout, 0)
                    self.assertLessEqual(timeout, 360)

    def test_checkout_steps_do_not_persist_credentials(self) -> None:
        workflow_roots = (
            PLUGIN_ROOT / ".github" / "workflows",
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "assets" / "workflows",
        )
        checkout_count = 0
        for workflow_root in workflow_roots:
            for path in workflow_root.glob("*.yml"):
                document = codeql_preflight.yaml.load(
                    path.read_text(encoding="utf-8"),
                    Loader=codeql_preflight.UniqueKeyBaseLoader,
                )
                for job_name, job in document.get("jobs", {}).items():
                    for index, step in enumerate(job.get("steps", [])):
                        if not str(step.get("uses", "")).startswith(
                            "actions/checkout@"
                        ):
                            continue
                        checkout_count += 1
                        with self.subTest(
                            workflow=path.name,
                            job=job_name,
                            step=index,
                        ):
                            self.assertEqual(
                                step.get("with", {}).get("persist-credentials"),
                                "false",
                            )
        self.assertGreater(checkout_count, 0)

    def test_community_survey_guards_links_before_traversal(self) -> None:
        setup = (
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "references" / "github-setup.md"
        ).read_text(encoding="utf-8")
        self.assertIn("function Assert-NotRepositoryLink", setup)
        self.assertIn("function Assert-RepositorySurveyPath", setup)
        self.assertIn("function Get-RepositorySurveyFile", setup)
        self.assertIn("[PlatformID]::Win32NT", setup)
        self.assertIn("[StringComparison]::Ordinal", setup)
        self.assertIn("$pathComparison", setup)
        self.assertNotIn(
            "Get-ChildItem -LiteralPath $localIssueTemplateDirectory -Force -File -Recurse",
            setup,
        )

    def test_remote_identity_rejects_repository_path_components(self) -> None:
        setup = (
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "references" / "github-setup.md"
        ).read_text(encoding="utf-8")
        identity = setup.split("## Repository identity preflight", 1)[1].split(
            "\n## ", 1
        )[0]
        self.assertIn('$owner -in @(".", "..")', identity)
        self.assertIn("$repo -notmatch '^[A-Za-z0-9_.-]+$'", identity)

    def test_merge_settings_preserve_effective_rule_methods(self) -> None:
        setup = (
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "references" / "github-setup.md"
        ).read_text(encoding="utf-8")
        self.assertIn("parameters.merge_method", setup)
        self.assertIn("parameters.allowed_merge_methods", setup)
        self.assertIn('$requiredRepositoryMergeMethods.Contains("merge")', setup)
        self.assertIn('$requiredRepositoryMergeMethods.Contains("rebase")', setup)
        self.assertNotIn("--enable-merge-commit=false `", setup)

        merge_settings = setup.split("## Merge settings", 1)[1].split("\n## ", 1)[0]
        self.assertIn("--paginate --slurp", merge_settings)
        self.assertIn("?per_page=100", merge_settings)
        self.assertIn("$finalMergeSettings", merge_settings)
        self.assertIn("$mergeSettingProblems", merge_settings)

    def test_label_creation_requeries_and_verifies_final_state(self) -> None:
        setup = (
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "references" / "github-setup.md"
        ).read_text(encoding="utf-8")
        labels = setup.split("## Labels", 1)[1].split("\n## ", 1)[0]
        self.assertIn("[Uri]::EscapeDataString($Name)", labels)
        self.assertIn("$createExitCode", labels)
        self.assertIn("$labelMatches", labels)
        self.assertIn("concurrent matching label", labels)

    def test_branch_protection_mutations_verify_exact_final_state(self) -> None:
        setup = (
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "references" / "github-setup.md"
        ).read_text(encoding="utf-8")
        protection = setup.split("## Branch protection (classic)", 1)[1].split(
            "\n## ", 1
        )[0]
        self.assertIn("function Assert-ClassicProtectionState", protection)
        self.assertEqual(
            protection.count("Assert-ClassicProtectionState"),
            3,
        )
        self.assertIn("required_status_checks.strict", protection)
        self.assertIn("unexpectedly required", protection)
        self.assertNotIn("contexts = @()", protection)
        self.assertIn("Send only `checks`", protection)

    def test_codeql_preflight_reads_the_tooling_python_policy(self) -> None:
        readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
        setup = (
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "references" / "github-setup.md"
        ).read_text(encoding="utf-8")
        self.assertIn(".github/python-support.json", readme)
        self.assertIn("tooling-python-minimum", setup)
        self.assertIn("sys.version_info[:2] >= required", setup)
        self.assertNotIn("Python 3.10 or newer", setup)


if __name__ == "__main__":
    unittest.main()
