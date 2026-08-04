from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from typing import Any, cast


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
        "standalone-tools": {
            "example": {
                "repository": "owner/example",
                "version": "1.2.3",
                "asset-template": "example_{version}_linux.tar.gz",
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
                "example_version=1.2.3",
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
            "tag_name": "v1.2.3",
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
                    "tag_name": "v1.2.3",
                    "assets": [
                        {
                            "name": "example_1.2.3_linux.tar.gz",
                            "digest": f"sha256:{'b' * 64}",
                        }
                    ],
                },
            )


if __name__ == "__main__":
    unittest.main()
