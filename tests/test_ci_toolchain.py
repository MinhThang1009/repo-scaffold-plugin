from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from typing import Any, cast
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "skills" / "repo-scaffold" / "scripts" / "ci_toolchain.py"
SPEC = importlib.util.spec_from_file_location("ci_toolchain", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load ci_toolchain.py")
ci_toolchain = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ci_toolchain
SPEC.loader.exec_module(ci_toolchain)


def policy_document() -> dict[str, object]:
    return {
        "schema-version": 1,
        "documentation-python": "3.x",
        "tooling-python-minimum": "3.10",
        "npm-tools": {
            "markdownlint-cli2": {
                "package": "markdownlint-cli2",
                "version": "0.23.2",
            }
        },
        "standalone-tools": {
            "example": {
                "repository": "owner/example",
                "version": "1.2.3",
                "tag-template": "release-{version}",
                "asset-template": "example_{version}_linux.tar.gz",
                "archive-format": "tar.gz",
                "executable-path-template": "example-{version}/example",
                "sha256": "a" * 64,
            }
        },
    }


class FakeResponse(BytesIO):
    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class CiToolchainPolicyTests(unittest.TestCase):
    def test_policy_emits_runtime_and_tool_outputs(self) -> None:
        policy = ci_toolchain.parse_policy(policy_document())

        self.assertEqual(
            ci_toolchain.github_output_lines(policy),
            [
                "documentation_python=3.x",
                "tooling_python_minimum=3.10",
                "markdownlint_cli2_package=markdownlint-cli2",
                "markdownlint_cli2_version=0.23.2",
                "example_repository=owner/example",
                "example_version=1.2.3",
                "example_tag=release-1.2.3",
                "example_asset=example_1.2.3_linux.tar.gz",
                "example_archive_format=tar.gz",
                "example_executable_path=example-1.2.3/example",
                f"example_sha256={'a' * 64}",
            ],
        )

    def test_policy_rejects_fixed_documentation_python(self) -> None:
        document = policy_document()
        document["documentation-python"] = "3.10"

        with self.assertRaisesRegex(
            ci_toolchain.ToolchainError, "rolling stable CPython selector"
        ):
            ci_toolchain.parse_policy(document)

    def test_policy_rejects_invalid_tooling_python_minimum(self) -> None:
        document = policy_document()
        document["tooling-python-minimum"] = "latest"

        with self.assertRaisesRegex(
            ci_toolchain.ToolchainError, "tooling-python-minimum"
        ):
            ci_toolchain.parse_policy(document)

    def test_policy_rejects_invalid_asset_template(self) -> None:
        document = policy_document()
        standalone_tools = cast(dict[str, object], document["standalone-tools"])
        tool = cast(dict[str, object], standalone_tools["example"])
        tool["asset-template"] = "../example.tar.gz"

        with self.assertRaisesRegex(ci_toolchain.ToolchainError, "asset-template"):
            ci_toolchain.parse_policy(document)

    def test_policy_rejects_invalid_digest(self) -> None:
        document = policy_document()
        standalone_tools = cast(dict[str, object], document["standalone-tools"])
        tool = cast(dict[str, object], standalone_tools["example"])
        tool["sha256"] = "not-a-digest"

        with self.assertRaisesRegex(ci_toolchain.ToolchainError, "64 lowercase hex"):
            ci_toolchain.parse_policy(document)

    def test_policy_rejects_unsafe_executable_path(self) -> None:
        document = policy_document()
        standalone_tools = cast(dict[str, object], document["standalone-tools"])
        tool = cast(dict[str, object], standalone_tools["example"])
        tool["executable-path-template"] = "../example"

        with self.assertRaisesRegex(
            ci_toolchain.ToolchainError, "executable-path-template"
        ):
            ci_toolchain.parse_policy(document)

    def test_duplicate_json_members_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text('{"schema-version":1,"schema-version":1}', encoding="utf-8")

            with self.assertRaisesRegex(
                ci_toolchain.ToolchainError, "duplicate JSON member"
            ):
                ci_toolchain.load_policy(path)

    def test_latest_release_verifies_tag_asset_and_digest(self) -> None:
        policy = ci_toolchain.parse_policy(policy_document())
        tool = policy.tools[0]
        release = {
            "tag_name": "release-1.2.3",
            "assets": [
                {
                    "name": "example_1.2.3_linux.tar.gz",
                    "digest": f"sha256:{'a' * 64}",
                }
            ],
        }
        opener_calls: list[tuple[str, int]] = []

        def opener(request: Any, *, timeout: int) -> FakeResponse:
            opener_calls.append((request.full_url, timeout))
            return FakeResponse(json.dumps(release).encode())

        document = ci_toolchain.fetch_latest_release(tool, opener=opener)
        ci_toolchain.verify_latest_release(tool, document)

        self.assertEqual(
            opener_calls,
            [("https://api.github.com/repos/owner/example/releases/latest", 15)],
        )

    def test_latest_release_detects_version_and_digest_drift(self) -> None:
        tool = ci_toolchain.parse_policy(policy_document()).tools[0]

        with self.assertRaisesRegex(ci_toolchain.ToolchainError, "latest release"):
            ci_toolchain.verify_latest_release(
                tool, {"tag_name": "v1.2.4", "assets": []}
            )
        with self.assertRaisesRegex(ci_toolchain.ToolchainError, "digest differs"):
            ci_toolchain.verify_latest_release(
                tool,
                {
                    "tag_name": "release-1.2.3",
                    "assets": [
                        {
                            "name": "example_1.2.3_linux.tar.gz",
                            "digest": f"sha256:{'b' * 64}",
                        }
                    ],
                },
            )

    def test_latest_npm_release_uses_registry_and_detects_drift(self) -> None:
        tool = ci_toolchain.parse_policy(policy_document()).npm_tools[0]
        opener_calls: list[tuple[str, int]] = []

        def opener(request: Any, *, timeout: int) -> FakeResponse:
            opener_calls.append((request.full_url, timeout))
            return FakeResponse(b'{"version":"0.23.2"}')

        document = ci_toolchain.fetch_latest_npm_tool(tool, opener=opener)
        ci_toolchain.verify_latest_npm_tool(tool, document)

        self.assertEqual(
            opener_calls,
            [("https://registry.npmjs.org/markdownlint-cli2/latest", 15)],
        )
        with self.assertRaisesRegex(ci_toolchain.ToolchainError, "latest npm"):
            ci_toolchain.verify_latest_npm_tool(tool, {"version": "0.23.3"})

    def test_markdownlint_command_consumes_the_policy_pin_without_a_shell(self) -> None:
        policy = ci_toolchain.parse_policy(policy_document())
        completed = mock.Mock(returncode=0)
        with (
            mock.patch.object(
                ci_toolchain, "resolve_path_executable", return_value="/tools/npx"
            ),
            mock.patch.object(
                ci_toolchain.subprocess, "run", return_value=completed
            ) as run,
        ):
            self.assertEqual(ci_toolchain.run_markdownlint(policy), 0)

        run.assert_called_once_with(
            [
                "/tools/npx",
                "--yes",
                "markdownlint-cli2@0.23.2",
                *ci_toolchain.MARKDOWNLINT_GLOBS,
            ],
            cwd=Path.cwd(),
            check=False,
            timeout=300,
        )


if __name__ == "__main__":
    unittest.main()
