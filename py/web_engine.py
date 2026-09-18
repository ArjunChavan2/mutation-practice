"""In-browser (Pyodide) mutation engine.

Adapts the same operator-based plant/verify logic as the local CLI tool
(~/mutation-tester/engine.py) to run entirely inside a single Pyodide
interpreter, with no course code ever baked into this repo: every source and
test file is pasted in by the user at runtime and lives only in Pyodide's
in-memory virtual filesystem for the duration of the browser tab.

The one real adaptation from the CLI version: there's no subprocess.run in a
browser sandbox, so verification runs unittest in-process instead of
shelling out, evicting every involved module from sys.modules before each
run so a rewritten file is actually re-imported rather than served from the
Python import cache.

Every function here takes/returns a JSON string, not native Python/JS
objects -- this keeps the JS<->Pyodide calling boundary to plain strings in
both directions, avoiding proxy-object conversion edge cases.
"""
from __future__ import annotations

import ast
import difflib
import io
import json
import os
import random
import sys
import unittest

from operators import ALL_OPERATORS, Candidate

SRC_DIR = "/practice/src"
TEST_DIR = "/practice/tests"
MAX_ATTEMPTS = 25


def _apply(source: str, candidate: Candidate) -> str:
    lines = source.splitlines(keepends=True)
    if candidate.lineno == candidate.end_lineno:
        line = lines[candidate.lineno - 1]
        lines[candidate.lineno - 1] = (
            line[:candidate.col_offset] + candidate.replacement + line[candidate.end_col_offset:]
        )
        return "".join(lines)
    before = lines[candidate.lineno - 1][:candidate.col_offset]
    after = lines[candidate.end_lineno - 1][candidate.end_col_offset:]
    new_block = before + candidate.replacement + after
    return "".join(lines[:candidate.lineno - 1]) + new_block + "".join(lines[candidate.end_lineno:])


def _write(path: str, text: str) -> None:
    with open(path, "w") as f:
        f.write(text)


def _evict(module_names: list[str]) -> None:
    for name in module_names:
        sys.modules.pop(name, None)


def _setup_modules(sources: dict[str, str], test_name: str, test_source: str) -> None:
    os.makedirs(SRC_DIR, exist_ok=True)
    os.makedirs(TEST_DIR, exist_ok=True)
    for name, code in sources.items():
        _write(f"{SRC_DIR}/{name}.py", code)
    _write(f"{TEST_DIR}/{test_name}.py", test_source)
    if SRC_DIR not in sys.path:
        sys.path.insert(0, SRC_DIR)
    if TEST_DIR not in sys.path:
        sys.path.insert(0, TEST_DIR)


def _run_tests(test_name: str, all_module_names: list[str]) -> bool:
    """True if tests pass, False if they fail (or fail to even import/collect)."""
    _evict(all_module_names)
    loader = unittest.TestLoader()
    try:
        suite = loader.loadTestsFromName(test_name)
    except Exception:
        return False
    runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0)
    result = runner.run(suite)
    return result.wasSuccessful()


def plant_json(payload_json: str) -> str:
    """payload: {target, sources: {name: code}, test_name, test_source}."""
    payload = json.loads(payload_json)
    target = payload["target"]
    sources: dict[str, str] = payload["sources"]
    test_name = payload["test_name"]
    test_source = payload["test_source"]
    all_names = [*sources.keys(), test_name]

    _setup_modules(sources, test_name, test_source)

    if not _run_tests(test_name, all_names):
        return json.dumps({
            "ok": False,
            "error": (
                "Your pasted code doesn't pass its own test file yet -- fix that first, "
                "then come back to practice finding a planted mutation."
            ),
        })

    original_source = sources[target]
    try:
        tree = ast.parse(original_source, filename=f"{target}.py")
    except SyntaxError as exc:
        return json.dumps({"ok": False, "error": f"Could not parse {target}.py: {exc}"})

    candidates: list[Candidate] = []
    for operator_fn in ALL_OPERATORS:
        candidates.extend(operator_fn(original_source, tree))
    if not candidates:
        return json.dumps({"ok": False, "error": f"No mutation candidates found in {target}.py."})
    random.shuffle(candidates)

    target_path = f"{SRC_DIR}/{target}.py"
    tried = 0
    for candidate in candidates[:MAX_ATTEMPTS]:
        tried += 1
        mutated = _apply(original_source, candidate)
        _write(target_path, mutated)
        if not _run_tests(test_name, all_names):
            return json.dumps({
                "ok": True,
                "mutated_source": mutated,
                "operator": candidate.operator,
                "description": candidate.description,
                "tried": tried,
            })
        _write(target_path, original_source)

    _write(target_path, original_source)
    return json.dumps({
        "ok": False,
        "error": f"Tried {tried} mutation(s) in {target}.py; none changed test-suite behavior.",
    })


def check_fix_json(payload_json: str) -> str:
    """payload: {target, candidate_source, all_module_names, test_name}."""
    payload = json.loads(payload_json)
    target = payload["target"]
    candidate_source = payload["candidate_source"]
    all_module_names: list[str] = payload["all_module_names"]
    test_name = payload["test_name"]

    _write(f"{SRC_DIR}/{target}.py", candidate_source)
    passed = _run_tests(test_name, all_module_names)
    return json.dumps({"passed": passed})


def diff_json(payload_json: str) -> str:
    """payload: {original, mutated}."""
    payload = json.loads(payload_json)
    diff_lines = difflib.unified_diff(
        payload["original"].splitlines(keepends=True),
        payload["mutated"].splitlines(keepends=True),
        fromfile="your original (correct) code",
        tofile="the planted mutation",
    )
    return json.dumps({"diff": "".join(diff_lines)})
