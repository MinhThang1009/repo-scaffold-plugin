from __future__ import annotations

import importlib.util
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import BinaryIO, cast
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT / "skills" / "repo-scaffold" / "scripts" / "codeql_preflight.py"
)
SPEC = importlib.util.spec_from_file_location("codeql_preflight", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load codeql_preflight.py")
codeql_preflight = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = codeql_preflight
SPEC.loader.exec_module(codeql_preflight)

VALIDATOR_PATH = PLUGIN_ROOT / "scripts" / "validate_workflows.py"
VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "validate_workflows", VALIDATOR_PATH
)
if VALIDATOR_SPEC is None or VALIDATOR_SPEC.loader is None:
    raise RuntimeError("Could not load validate_workflows.py")
validate_workflows = importlib.util.module_from_spec(VALIDATOR_SPEC)
sys.modules[VALIDATOR_SPEC.name] = validate_workflows
VALIDATOR_SPEC.loader.exec_module(validate_workflows)


class ExecutableResolutionTests(unittest.TestCase):
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

module = runpy.run_path(sys.argv[1])
if sys.argv[2] == "env":
    payload = "\n_=" + ('"" _=' * 128) + "!"
    module["CODEQL_CLI"].search(payload)
elif sys.argv[2] == "alias":
    payload = 'Set-Alias -Name "' + ('`!' * 128)
    module["_powershell_alias_definition"](payload)
else:
    raise AssertionError(f"Unknown probe: {sys.argv[2]}")
"""
        subprocess.run(
            [sys.executable, "-c", probe, str(SCRIPT_PATH), case],
            check=True,
            capture_output=True,
            timeout=2,
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


class GitHubClientTests(unittest.TestCase):
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
        skill = (PLUGIN_ROOT / "skills" / "repo-scaffold" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("replace every `${{` with `[$*]{{`", skill)

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
        expected_sha = "f205ea1c3313d32999d8d6a48b4f6530d4437b38"

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
                self.assertEqual(document["permissions"]["security-events"], "write")
                self.assertEqual(
                    set(document["on"]), {"push", "pull_request", "schedule"}
                )
                codeql_uses = re.findall(
                    r"github/codeql-action/(init|autobuild|analyze)@([0-9a-f]{40})",
                    workflow,
                )
                self.assertEqual(
                    codeql_uses,
                    [
                        ("init", expected_sha),
                        ("autobuild", expected_sha),
                        ("analyze", expected_sha),
                    ],
                )
                self.assertIn("if: contains(fromJSON(", workflow)

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
        expected_uses = [
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "ossf/scorecard-action@2d1146689b8cda280b9bc96326124645441f03bc",
            "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
            "github/codeql-action/upload-sarif@f205ea1c3313d32999d8d6a48b4f6530d4437b38",
        ]

        documents = {}
        for path in (installed_path, asset_path):
            document = codeql_preflight.yaml.load(
                path.read_text(encoding="utf-8"),
                Loader=codeql_preflight.UniqueKeyBaseLoader,
            )
            documents[path] = document
            job = document["jobs"]["analysis"]
            with self.subTest(workflow=path.as_posix()):
                self.assertEqual(document["permissions"], "read-all")
                self.assertEqual(
                    set(document["on"]),
                    {"branch_protection_rule", "push", "schedule"},
                )
                self.assertEqual(job["permissions"]["contents"], "read")
                self.assertEqual(job["permissions"]["id-token"], "write")
                self.assertEqual(job["permissions"]["security-events"], "write")
                self.assertEqual(
                    [step["uses"] for step in job["steps"] if "uses" in step],
                    expected_uses,
                )
                scorecard_step = next(
                    step
                    for step in job["steps"]
                    if step.get("uses", "").startswith("ossf/scorecard-action@")
                )
                self.assertEqual(scorecard_step["with"]["file_mode"], "git")
                self.assertEqual(scorecard_step["with"]["publish_results"], "true")
                self.assertEqual(scorecard_step["with"]["results_format"], "sarif")

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
