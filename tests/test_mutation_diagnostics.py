from __future__ import annotations

import os
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/run_mutation_shard.sh"


def sampler() -> dict[str, Any]:
    """Load only the checked-in inline sampler, without launching its monitor."""
    code = (
        SCRIPT.read_text(encoding="utf-8")
        .split("<<'PY' &\n", 1)[1]
        .split("\nPY\n", 1)[0]
    )
    namespace: dict[str, Any] = {"__name__": "mutation_diagnostic_test"}
    exec(compile(code, str(SCRIPT), "exec"), namespace)
    return namespace


class MutationDiagnosticsTests(unittest.TestCase):
    def write_process(
        self, proc: Path, pid: int, parent: int, birth: int, title: bytes
    ) -> None:
        target = proc / str(pid)
        target.mkdir()
        fields = ["S", str(parent), *(["0"] * 17), str(birth)]
        (target / "stat").write_text(
            f"{pid} (name with ) parentheses) " + " ".join(fields), encoding="utf-8"
        )
        (target / "comm").write_bytes(b"mutmut: scripts.\n")
        (target / "cmdline").write_bytes(title)
        (target / "status").write_bytes(b"VmRSS:\t123 kB\nVmHWM:\t456 kB\n")
        (target / "cgroup").write_bytes(b"0::/job\n")

    def test_owned_worker_names_are_whitelisted_and_unrelated_values_not_emitted(
        self,
    ) -> None:
        namespace = sampler()
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory) / "proc"
            proc.mkdir()
            cgroups = Path(directory) / "cgroups"
            (cgroups / "job").mkdir(parents=True)
            (cgroups / "job/memory.current").write_bytes(b"1024\n")
            (cgroups / "job/memory.events").write_bytes(b"oom 0\noom_kill 1\n")
            (proc / "meminfo").write_bytes(b"MemTotal: 999 kB\nMemAvailable: 777 kB\n")
            key = "scripts.example.x_decision__mutmut_1"
            self.write_process(proc, 10, 1, 100, b"engine\0")
            self.write_process(proc, 11, 10, 101, ("mutmut: " + key).encode() + b"\0")
            self.write_process(proc, 12, 10, 102, b"mutmut: synthetic-secret\0")
            self.write_process(proc, 13, 1, 103, ("mutmut: " + key).encode() + b"\0")
            result = namespace["sample"](proc, cgroups, 10, 100, {key})
        self.assertEqual(
            result["workers"],
            [{"pid": 11, "mutant": key, "memory_kib": {"VmRSS": 123, "VmHWM": 456}}],
        )
        self.assertEqual(
            result["cgroup"],
            {"memory_bytes": 1024, "events": {"oom": 0, "oom_kill": 1}},
        )
        self.assertEqual(
            result["system_memory_kib"], {"MemTotal": 999, "MemAvailable": 777}
        )
        self.assertNotIn("synthetic-secret", str(result))

    def test_owned_process_memory_does_not_claim_unknown_mutant_identity(self) -> None:
        namespace = sampler()
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            self.write_process(proc, 10, 1, 100, b"engine\0")
            self.write_process(proc, 11, 10, 101, b"synthetic-private-argument\0")
            self.write_process(proc, 12, 1, 102, b"unrelated\0")
            (proc / "11/comm").write_bytes(b"python\n")
            result = namespace["sample"](proc, proc, 10, 100, set())
            self.assertEqual(result["worker_identity_status"], "unavailable")
            self.assertEqual(result["workers"], [])
            self.assertEqual(result["engine_memory_kib"], {"VmRSS": 123, "VmHWM": 456})
            self.assertEqual(
                result["processes"],
                [{"pid": 11, "memory_kib": {"VmRSS": 123, "VmHWM": 456}}],
            )
            self.assertNotIn("synthetic-private-argument", str(result))
            namespace["MAX_WORKERS"] = 0
            limited = namespace["sample"](proc, proc, 10, 100, set())
            self.assertTrue(limited["scan_partial"])
            self.assertEqual(limited["processes"], [])

    def test_missing_oversized_and_changed_process_inputs_remain_unavailable(
        self,
    ) -> None:
        namespace = sampler()
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            path = proc / "oversized"
            path.write_bytes(b"12345")
            self.assertIsNone(namespace["read"](path, 4))
            self.assertIsNone(namespace["read"](proc / "missing"))
            self.write_process(proc, 10, 1, 101, b"engine\0")
            self.assertIsNone(namespace["sample"](proc, proc, 10, 100, set()))
            self.assertIsNone(namespace["number"]("9" * 21))
            self.assertIsNone(namespace["number"]("-1"))
            self.assertIsNone(namespace["number"]("١"))

    def test_scan_budget_is_reported_and_cgroup_traversal_is_not_followed(self) -> None:
        namespace = sampler()
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory) / "proc"
            proc.mkdir()
            self.write_process(proc, 10, 1, 100, b"engine\0")
            (proc / "10/cgroup").write_bytes(b"0::/../outside\n")
            namespace["MAX_PROCESSES"] = 0
            result = namespace["sample"](
                proc, Path(directory) / "cgroups", 10, 100, set()
            )
            self.assertTrue(result["scan_partial"])
            self.assertIsNone(result["cgroup"])
            self.assertIsNone(result["system_memory_kib"])
            with mock.patch.dict(
                namespace, {"process": mock.Mock(side_effect=[(1, 100), (1, 101)])}
            ):
                self.assertIsNone(namespace["sample"](proc, proc, 10, 100, set()))

    def test_wrapper_preserves_engine_status_even_when_diagnostics_unavailable(
        self,
    ) -> None:
        bash = (
            shutil.which("bash")
            if os.name != "nt"
            else "C:/Program Files/Git/bin/bash.exe"
        )
        if bash is None or not Path(bash).is_file():
            self.skipTest("requires bash for the wrapper smoke test")
        environment = os.environ.copy()
        environment["PATH"] = (
            str(Path(sys.executable).parent) + os.pathsep + environment["PATH"]
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "scripts").mkdir()
            engine = target / "scripts/run_mutation_testing.py"
            for status in (0, 7, 143):
                engine.write_text(
                    "import os, sys\n"
                    "assert os.environ.get('REPO_SCAFFOLD_MUTATION_DIAGNOSTICS') == '1'\n"
                    "assert sys.argv[1:] == ['--max-children', '4', '--shard-index', '0']\n"
                    f"raise SystemExit({status})\n",
                    encoding="utf-8",
                )
                result = subprocess.run(
                    [bash, str(SCRIPT), "0"],
                    cwd=target,
                    env=environment,
                    capture_output=True,
                    timeout=20,
                )
                with self.subTest(status=status):
                    self.assertEqual(result.returncode, status, result.stderr)
            result = subprocess.run(
                [bash, str(SCRIPT), "128"],
                cwd=target,
                env=environment,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 2)

    def test_sampler_limits_output_and_sample_count_without_changing_verdicts(
        self,
    ) -> None:
        for output_limit, expected_samples in ((4096, 2), (256, 1)):
            namespace = sampler()
            namespace.update({"MAX_OUTPUT": output_limit, "MAX_SAMPLES": 2})
            sample = mock.Mock(return_value={"workers": []})
            namespace.update(
                {
                    "read": mock.Mock(
                        return_value=json.dumps({"shards": [[]]}).encode()
                    ),
                    "process": mock.Mock(return_value=(1, 100)),
                    "sample": sample,
                }
            )
            output = StringIO()
            with (
                self.subTest(output_limit=output_limit),
                mock.patch.object(sys, "argv", ["observer", "10", "0"]),
                mock.patch.object(
                    namespace["os"], "pidfd_open", return_value=42, create=True
                ),
                mock.patch.object(namespace["os"], "close") as close,
                mock.patch.object(
                    namespace["select"], "select", return_value=([], [], [])
                ) as wait,
                redirect_stdout(output),
            ):
                namespace["main"]()
            self.assertEqual(sample.call_count, expected_samples)
            self.assertLessEqual(len(output.getvalue().encode()), output_limit)
            self.assertEqual(wait.call_count, 2 if output_limit == 4096 else 0)
            close.assert_called_once_with(42)

    def test_resource_and_worker_diagnostics_share_the_two_mebibyte_budget(
        self,
    ) -> None:
        namespace = sampler()
        from scripts import run_mutation_testing

        self.assertEqual(
            namespace["MAX_OUTPUT"] + run_mutation_testing.MAX_WORKER_DIAGNOSTIC_BYTES,
            2 * 1024 * 1024,
        )


if __name__ == "__main__":
    unittest.main()
