#!/usr/bin/env python3
"""Validate mutmut CI statistics without accepting incomplete mutation runs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


COUNT_FIELDS = (
    "killed",
    "survived",
    "no_tests",
    "skipped",
    "suspicious",
    "timeout",
    "check_was_interrupted_by_user",
    "segfault",
)
EXPECTED_FIELDS = {*COUNT_FIELDS, "total"}
MINIMUM_MUTATION_SCORE_BASIS_POINTS = 7_880
UNSAFE_RESULT_FIELDS = (
    "no_tests",
    "skipped",
    "suspicious",
    "check_was_interrupted_by_user",
    "segfault",
)


class DuplicateJsonMember(ValueError):
    """Raised when mutation statistics contain duplicate JSON members."""


def unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting ambiguous duplicate members."""
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise DuplicateJsonMember(f"duplicate JSON member {key!r}")
        document[key] = value
    return document


def load_statistics(path: Path) -> dict[str, int]:
    """Load strict, nonnegative mutmut counters from a JSON artifact."""
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=unique_json_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError, DuplicateJsonMember) as error:
        raise ValueError(f"could not read mutation statistics: {error}") from error
    if not isinstance(document, dict):
        raise ValueError("mutation statistics root must be a JSON object")

    fields = set(document)
    if fields != EXPECTED_FIELDS:
        missing = sorted(EXPECTED_FIELDS - fields)
        unexpected = sorted(fields - EXPECTED_FIELDS)
        raise ValueError(
            f"mutation statistics fields differ: missing={missing}, "
            f"unexpected={unexpected}"
        )

    statistics: dict[str, int] = {}
    for field in sorted(EXPECTED_FIELDS):
        value = document[field]
        if type(value) is not int or value < 0:
            raise ValueError(
                f"mutation statistic {field!r} must be a nonnegative integer"
            )
        statistics[field] = value
    return statistics


def validate_statistics(statistics: dict[str, int]) -> list[str]:
    """Reject incomplete, unsafe, or below-threshold mutation results."""
    problems: list[str] = []
    total = statistics["total"]
    if total == 0:
        problems.append("mutation run generated no mutants")
    accounted = sum(statistics[field] for field in COUNT_FIELDS)
    if accounted != total:
        problems.append(
            f"mutation counters account for {accounted} results but total is {total}"
        )
    for field in UNSAFE_RESULT_FIELDS:
        if statistics[field]:
            problems.append(
                f"mutation run has {statistics[field]} result(s) classified as {field}"
            )

    detected = statistics["killed"] + statistics["timeout"]
    testable = detected + statistics["survived"]
    score_basis_points = detected * 10_000 // testable if testable else 0
    if score_basis_points < MINIMUM_MUTATION_SCORE_BASIS_POINTS:
        problems.append(
            f"mutation score {score_basis_points / 100:.2f}% is below required "
            f"{MINIMUM_MUTATION_SCORE_BASIS_POINTS / 100:.2f}% "
            f"({detected} detected of {testable} testable mutants)"
        )
    return problems


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the mutmut statistics path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "statistics",
        nargs="?",
        type=Path,
        default=Path("mutants/mutmut-cicd-stats.json"),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Validate and summarize the mutation result artifact."""
    arguments = parse_args(argv)
    try:
        statistics = load_statistics(arguments.statistics)
        problems = validate_statistics(statistics)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    detected = statistics["killed"] + statistics["timeout"]
    testable = detected + statistics["survived"]
    score = detected * 100 / testable
    print(
        "Mutation testing is complete: "
        f"{statistics['killed']}/{statistics['total']} mutants were killed, "
        f"{statistics['timeout']} timed out; mutation score {score:.2f}%."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
