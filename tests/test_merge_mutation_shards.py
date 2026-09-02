from __future__ import annotations

import importlib.util
import json
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
        mutants.mkdir(parents=True)
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
            target.mkdir(parents=True)
            (target / "source.meta").write_text(
                json.dumps(self.document(results)), encoding="utf-8"
            )

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


if __name__ == "__main__":
    unittest.main()
