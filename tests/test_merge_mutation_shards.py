from __future__ import annotations

import importlib.util
from contextlib import redirect_stderr
from io import StringIO
import json
import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "scripts.merge_mutation_shards", ROOT / "scripts" / "merge_mutation_shards.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load merge_mutation_shards.py")
merge_mutation_shards = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = merge_mutation_shards
SPEC.loader.exec_module(merge_mutation_shards)


class MergeMutationShardsTests(unittest.TestCase):
    @staticmethod
    def document(results: dict[str, int | None]) -> dict[str, object]:
        return {
            "exit_code_by_key": results,
            "type_check_error_by_key": {key: None for key in results},
            "durations_by_key": {key: None for key in results},
            "estimated_durations_by_key": {key: None for key in results},
        }

    def fixture(self, root: Path) -> None:
        mutants = root / "mutants"
        mutants.mkdir(parents=True, exist_ok=True)
        (mutants / "mutation-shards.json").write_text(
            json.dumps({"shards": [["alpha"], ["beta"]]}), encoding="utf-8"
        )
        (mutants / "source.meta").write_text(
            json.dumps(self.document({"alpha": None, "beta": None})), encoding="utf-8"
        )
        for index, results in enumerate(
            ({"alpha": 1, "beta": None}, {"alpha": None, "beta": 0})
        ):
            target = root / "mutation-shards" / f"mutation-shard-{index}"
            target.mkdir(parents=True, exist_ok=True)
            (target / "source.meta").write_text(
                json.dumps(self.document(results)), encoding="utf-8"
            )

    @staticmethod
    def set_overlay_value(
        root: Path, shard: int, field: str, name: str, value: object
    ) -> None:
        overlay = root / "mutation-shards" / f"mutation-shard-{shard}" / "source.meta"
        document = json.loads(overlay.read_text(encoding="utf-8"))
        document[field][name] = value
        overlay.write_text(json.dumps(document), encoding="utf-8")

    def test_merges_a_complete_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            merge_mutation_shards.merge(root, root / "mutation-shards")
            result = json.loads(
                (root / "mutants" / "source.meta").read_text(encoding="utf-8")
            )
            self.assertEqual(result["exit_code_by_key"], {"alpha": 1, "beta": 0})

    def test_rejects_invalid_plan_and_incomplete_or_unassigned_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            plan = root / "mutants" / "mutation-shards.json"
            plan.write_text('{"shards": []}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid schema"):
                merge_mutation_shards.merge(root, root / "mutation-shards")

            self.fixture(root / "second")
            root = root / "second"
            overlay = root / "mutation-shards" / "mutation-shard-1" / "source.meta"
            document = json.loads(overlay.read_text(encoding="utf-8"))
            document["exit_code_by_key"]["beta"] = None
            overlay.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "did not finish"):
                merge_mutation_shards.merge(root, root / "mutation-shards")

            self.fixture(root / "third")
            root = root / "third"
            overlay = root / "mutation-shards" / "mutation-shard-1" / "source.meta"
            document = json.loads(overlay.read_text(encoding="utf-8"))
            document["exit_code_by_key"]["alpha"] = 1
            overlay.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unassigned"):
                merge_mutation_shards.merge(root, root / "mutation-shards")

    def test_load_json_rejects_unreadable_and_nonobject_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata.json"
            with self.assertRaisesRegex(ValueError, "could not read"):
                merge_mutation_shards.load_json(path)
            path.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not an object"):
                merge_mutation_shards.load_json(path)

    def test_rejects_duplicate_names_and_missing_base_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            plan = root / "mutants" / "mutation-shards.json"
            plan.write_text(
                json.dumps({"shards": [["alpha"], ["alpha"]]}), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "duplicate or invalid"):
                merge_mutation_shards.merge(root, root / "mutation-shards")

            (root / "mutants" / "source.meta").unlink()
            plan.write_text(
                json.dumps({"shards": [["alpha"], ["beta"]]}), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "metadata is missing"):
                merge_mutation_shards.merge(root, root / "mutation-shards")

    def test_rejects_base_metadata_with_wrong_results_or_completed_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            base = root / "mutants" / "source.meta"
            document = json.loads(base.read_text(encoding="utf-8"))
            document["exit_code_by_key"] = []
            base.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "lacks mutation results"):
                merge_mutation_shards.merge(root, root / "mutation-shards")

            self.fixture(root)
            document = json.loads(base.read_text(encoding="utf-8"))
            document["exit_code_by_key"]["alpha"] = 0
            base.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "already contains"):
                merge_mutation_shards.merge(root, root / "mutation-shards")

    def test_rejects_metadata_that_does_not_match_assignments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            base = root / "mutants" / "source.meta"
            document = json.loads(base.read_text(encoding="utf-8"))
            document["exit_code_by_key"]["unknown"] = None
            base.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match"):
                merge_mutation_shards.merge(root, root / "mutation-shards")

            self.fixture(root)
            plan = root / "mutants" / "mutation-shards.json"
            plan.write_text(
                json.dumps({"shards": [["alpha"], ["beta"], ["gamma"]]}),
                encoding="utf-8",
            )
            third_overlay = root / "mutation-shards" / "mutation-shard-2"
            third_overlay.mkdir()
            (third_overlay / "source.meta").write_text(
                json.dumps(self.document({"alpha": None, "beta": None})),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "does not match"):
                merge_mutation_shards.merge(root, root / "mutation-shards")

            self.fixture(root)
            second = root / "mutants" / "second.meta"
            second.write_text(
                json.dumps(self.document({"alpha": None, "beta": None})),
                encoding="utf-8",
            )
            for index, results in enumerate(
                ({"alpha": 1, "beta": None}, {"alpha": None, "beta": 0})
            ):
                (
                    root / "mutation-shards" / f"mutation-shard-{index}" / "second.meta"
                ).write_text(json.dumps(self.document(results)), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match"):
                merge_mutation_shards.merge(root, root / "mutation-shards")

    def test_rejects_invalid_overlay_fields_and_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            self.set_overlay_value(root, 0, "durations_by_key", "alpha", 1.0)
            overlay = root / "mutation-shards" / "mutation-shard-0" / "source.meta"
            document = json.loads(overlay.read_text(encoding="utf-8"))
            document["durations_by_key"] = []
            overlay.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError, "field 'durations_by_key' is invalid"
            ):
                merge_mutation_shards.merge(root, root / "mutation-shards")

            self.fixture(root)
            self.set_overlay_value(root, 0, "durations_by_key", "beta", None)
            overlay = root / "mutation-shards" / "mutation-shard-0" / "source.meta"
            document = json.loads(overlay.read_text(encoding="utf-8"))
            document["durations_by_key"].pop("beta")
            overlay.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "keys differ"):
                merge_mutation_shards.merge(root, root / "mutation-shards")

    def test_main_and_entrypoint_return_expected_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            previous = Path.cwd()
            try:
                os.chdir(root)
                self.assertEqual(merge_mutation_shards.main(), 0)
                self.fixture(root)
                with self.assertRaises(SystemExit) as success:
                    runpy.run_path(
                        str(ROOT / "scripts" / "merge_mutation_shards.py"),
                        run_name="__main__",
                    )
                self.assertEqual(success.exception.code, 0)
            finally:
                os.chdir(previous)

        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            error = StringIO()
            try:
                os.chdir(directory)
                with redirect_stderr(error):
                    self.assertEqual(merge_mutation_shards.main(), 1)
                self.assertIn("error: could not read", error.getvalue())
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
