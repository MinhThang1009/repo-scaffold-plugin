from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
RUN_INTEGRATION = os.environ.get("REPO_SCAFFOLD_MUTMUT_INTEGRATION") == "1"


@unittest.skipUnless(
    sys.platform.startswith("linux") and RUN_INTEGRATION,
    "requires the Linux mutation integration job",
)
class LinuxMutationRunnerIntegrationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
