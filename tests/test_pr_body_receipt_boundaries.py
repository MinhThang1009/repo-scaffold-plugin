from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = (
    ROOT / ".github/workflows/pr-body-sync.yml",
    ROOT / "skills/repo-scaffold/assets/workflows/pr-body-sync.yml",
)


class PullRequestReceiptBoundaryTests(unittest.TestCase):
    def test_pagination_rejects_duplicate_members_before_reencoding(self) -> None:
        for workflow in WORKFLOWS:
            job = yaml.load(
                workflow.read_text(encoding="utf-8"), Loader=yaml.BaseLoader
            )["jobs"]["update"]
            blocks = re.findall(r"<<'PY'\n(.*?)\nPY", job["steps"][1]["run"], re.DOTALL)
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory)
                page = target / "1.json"
                for duplicate in (False, True):
                    page.write_text(
                        '[{"sha":"a"' + (',"sha":"b"' if duplicate else "") + "}]",
                        encoding="utf-8",
                    )
                    for index, arguments in (
                        (1, [str(page), "commits"]),
                        (2, [str(target), str(target / "aggregate.data"), "10485760"]),
                    ):
                        result = subprocess.run(
                            [sys.executable, "-", *arguments],
                            input=blocks[index],
                            text=True,
                            capture_output=True,
                            timeout=20,
                            check=False,
                        )
                        with self.subTest(
                            workflow=workflow, stage=index, duplicate=duplicate
                        ):
                            self.assertEqual(
                                result.returncode == 0, not duplicate, result.stderr
                            )
                            self.assertNotIn("Traceback", result.stderr)

    def test_initial_premutation_and_postmutation_receipts_are_target_and_type_bound(
        self,
    ) -> None:
        for workflow in WORKFLOWS:
            job = yaml.load(
                workflow.read_text(encoding="utf-8"), Loader=yaml.BaseLoader
            )["jobs"]["update"]
            blocks = re.findall(r"<<'PY'\n(.*?)\nPY", job["steps"][1]["run"], re.DOTALL)
            self.assertEqual(len(blocks), 5)
            for index in (0, 3, 4):
                with tempfile.TemporaryDirectory() as directory:
                    target = Path(directory)
                    body = target / "body.md"
                    body.write_bytes(b"Synthetic body\n")
                    initial: dict[str, Any] = {
                        "number": 7,
                        "state": "open",
                        "title": "fix: synthetic",
                        "body": "Synthetic body\n",
                        "commits": 1,
                        "changed_files": 1,
                        "head": {
                            "sha": "a" * 40,
                            "repo": {"id": 2, "full_name": "fork/project"},
                        },
                        "base": {
                            "sha": "b" * 40,
                            "repo": {
                                "id": 42,
                                "full_name": "octo/project",
                                "archived": False,
                                "disabled": False,
                            },
                        },
                    }
                    initial_path = target / "initial.json"
                    initial_path.write_text(json.dumps(initial), encoding="utf-8")
                    for case in (
                        "positive",
                        "bool-count",
                        "float-count",
                        "foreign-pr",
                        "bool-pr",
                        "foreign-base",
                        "foreign-head",
                        "inactive",
                        "wrong-base-name",
                        "duplicate-member",
                        "nonobject",
                        "string-count",
                        "string-base-id",
                        "bool-head-id",
                        "wrong-active-type",
                    ):
                        receipt = copy.deepcopy(initial)
                        if case == "bool-count":
                            receipt["commits"] = True
                        elif case == "float-count":
                            receipt["changed_files"] = 1.0
                        elif case == "foreign-pr":
                            receipt["number"] = 8
                        elif case == "bool-pr":
                            receipt["number"] = True
                        elif case == "foreign-base":
                            receipt["base"]["repo"]["id"] = 43
                        elif case == "foreign-head":
                            receipt["head"]["repo"]["id"] = 3
                        elif case == "inactive":
                            receipt["base"]["repo"]["archived"] = True
                        elif case == "wrong-base-name":
                            receipt["base"]["repo"]["full_name"] = "other/project"
                        elif case == "string-count":
                            receipt["changed_files"] = "1"
                        elif case == "string-base-id":
                            receipt["base"]["repo"]["id"] = "42"
                        elif case == "bool-head-id":
                            receipt["head"]["repo"]["id"] = True
                        elif case == "wrong-active-type":
                            receipt["base"]["repo"]["archived"] = 0
                        raw = json.dumps(receipt)
                        if case == "nonobject":
                            raw = "[]"
                        if case == "duplicate-member":
                            raw = raw.replace(
                                '"number": 7', '"number": 8, "number": 7', 1
                            )
                        payload = target / "payload.json"
                        payload.write_text(raw, encoding="utf-8")
                        arguments = [
                            str(payload),
                            str(body),
                            "a" * 40,
                            "fork/project",
                            "fix: synthetic",
                            "b" * 40,
                        ]
                        if index != 0:
                            arguments.append(str(initial_path))
                        result = subprocess.run(
                            [sys.executable, "-", *arguments],
                            input=blocks[index],
                            text=True,
                            capture_output=True,
                            env={
                                **os.environ,
                                "PR_NUMBER": "7",
                                "REPOSITORY": "octo/project",
                                "REPOSITORY_ID": "42",
                                "PR_HEAD_REPOSITORY_ID": "2",
                            },
                            timeout=20,
                            check=False,
                        )
                        with self.subTest(workflow=workflow, stage=index, case=case):
                            self.assertEqual(
                                result.returncode == 0,
                                case == "positive",
                                result.stderr,
                            )
                            if case != "positive":
                                self.assertNotIn("Traceback", result.stderr)
