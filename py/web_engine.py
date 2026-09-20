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
import tokenize
import unittest

from operators import ALL_OPERATORS as GENERIC_OPERATORS, Candidate
from project_operators import ALL_PROJECT_OPERATORS

SRC_DIR = "/practice/src"
TEST_DIR = "/practice/tests"
MAX_ATTEMPTS = 25
OPERATOR_SETS = ("generic", "project", "all")


def _select_operators(operator_set: str) -> list:
    if operator_set == "generic":
        return GENERIC_OPERATORS
    if operator_set == "project":
        return ALL_PROJECT_OPERATORS
    return [*GENERIC_OPERATORS, *ALL_PROJECT_OPERATORS]


def _apply(source: str, candidate: Candidate) -> str:
    return _replace_span(
        source, candidate.lineno, candidate.col_offset, candidate.end_lineno, candidate.end_col_offset,
        candidate.replacement,
    )


def _replace_span(source: str, lineno: int, col: int, end_lineno: int, end_col: int, replacement: str) -> str:
    lines = source.splitlines(keepends=True)
    if lineno == end_lineno:
        line = lines[lineno - 1]
        lines[lineno - 1] = line[:col] + replacement + line[end_col:]
        return "".join(lines)
    before = lines[lineno - 1][:col]
    after = lines[end_lineno - 1][end_col:]
    return "".join(lines[:lineno - 1]) + before + replacement + after + "".join(lines[end_lineno:])


def _strip_docstrings(source: str, tree: ast.Module) -> str:
    """Blank out every module/class/function docstring, preserving all other formatting.

    A docstring is the first statement of a Module/ClassDef/FunctionDef body
    when it's a bare string-constant expression. Replaced with nothing, except
    when it's the body's *only* statement, where a `pass` keeps the syntax
    valid (crucially: replacing a *module* docstring must never insert
    anything, even `pass` -- a `from __future__ import ...` immediately after
    it must remain the literal first statement, which `pass` would violate).
    """
    doc_pairs = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                doc_pairs.append((node, body[0]))
    doc_pairs.sort(key=lambda pair: (pair[1].lineno, pair[1].col_offset), reverse=True)
    for node, doc_expr in doc_pairs:
        needs_placeholder = not isinstance(node, ast.Module) and len(node.body) == 1
        replacement = (" " * doc_expr.col_offset + "pass") if needs_placeholder else ""
        source = _replace_span(source, doc_expr.lineno, doc_expr.col_offset, doc_expr.end_lineno, doc_expr.end_col_offset, replacement)
    return source


def _strip_comments(source: str) -> str:
    out_tokens = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            continue
        out_tokens.append(tok)
    return tokenize.untokenize(out_tokens)


def _clean_source(source: str) -> str:
    """Strip comments and docstrings so the hunt is pure code, no hint text."""
    tree = ast.parse(source)
    cleaned = _strip_comments(_strip_docstrings(source, tree))
    # cosmetic: the now-empty lines left behind by removed docstrings still
    # carry their original indentation as trailing whitespace -- tidy those
    # (and only those) down to genuinely blank lines.
    return "".join(line if line.strip() else "\n" for line in cleaned.splitlines(keepends=True))


def _write(path: str, text: str) -> None:
    with open(path, "w") as f:
        f.write(text)


# Every module name ever written to SRC_DIR/TEST_DIR this session, so a name
# that drops out of the *current* source set (a removed/renamed tab) still
# gets evicted from sys.modules -- otherwise Python's import cache happily
# keeps serving the stale module object from an earlier plant, even after
# its .py file is gone from the virtual filesystem, silently masking a
# missing dependency instead of raising ModuleNotFoundError for it.
_known_modules: set[str] = set()


def _evict_known_modules() -> None:
    for name in _known_modules:
        sys.modules.pop(name, None)


def _clear_py_files(path: str) -> None:
    """Remove any stale .py files left behind by a previous plant_json call

    (e.g. a dependency the user has since removed or renamed a tab for) so
    the virtual filesystem always reflects exactly the current file set,
    never a leftover module a test could still (wrongly) import.
    """
    if not os.path.isdir(path):
        return
    for name in os.listdir(path):
        if name.endswith(".py"):
            os.remove(os.path.join(path, name))


def _setup_modules(sources: dict[str, str], tests: dict[str, str]) -> None:
    os.makedirs(SRC_DIR, exist_ok=True)
    os.makedirs(TEST_DIR, exist_ok=True)
    _clear_py_files(SRC_DIR)
    _clear_py_files(TEST_DIR)
    for name, code in sources.items():
        _write(f"{SRC_DIR}/{name}.py", code)
        _known_modules.add(name)
    for name, code in tests.items():
        _write(f"{TEST_DIR}/{name}.py", code)
        _known_modules.add(name)
    if SRC_DIR not in sys.path:
        sys.path.insert(0, SRC_DIR)
    if TEST_DIR not in sys.path:
        sys.path.insert(0, TEST_DIR)


def _collect_suite(test_names: list[str]):
    """Try to load and combine every named test module into one suite.

    Returns (suite, None) or (None, error_str) -- a single test module that
    fails to import fails the whole combined collection, same as `unittest
    discover` would report a collection error for the whole run.
    """
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for name in test_names:
        try:
            suite.addTests(loader.loadTestsFromName(name))
        except Exception as exc:
            return None, f"{name}: {exc}"
    return suite, None


def _run_tests(test_names: list[str]) -> bool:
    """True if every test file's tests pass, False otherwise (or on a collection failure).

    Output is always discarded here -- used only during plant_json's silent
    mutation search (trying candidates until one changes behavior), never
    shown to the user, so it can't leak which candidate was accepted.
    """
    _evict_known_modules()
    suite, _err = _collect_suite(test_names)
    if suite is None:
        return False
    runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0)
    result = runner.run(suite)
    return result.wasSuccessful()


def _run_tests_with_output(test_names: list[str]) -> tuple[bool, str]:
    """Like _run_tests, but captures and returns the real unittest output.

    Used by check_fix_json (the Hunt-mode "Run Tests" button) -- unlike the
    silent verification during planting, this is a result the user
    deliberately asked to see, the same way running your own test suite
    locally would show you exactly which test failed and why.
    """
    _evict_known_modules()
    suite, err = _collect_suite(test_names)
    if suite is None:
        return False, f"Could not load tests: {err}"
    stream = io.StringIO()
    runner = unittest.TextTestRunner(stream=stream, verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful(), stream.getvalue()


def _last_line(text: str) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else text.strip()


def _run_tests_detailed(test_names: list[str]) -> tuple[bool, str | None]:
    """Like _run_tests, but on failure also returns *why*, when available.

    Only used for the pre-mutation sanity check in plant_json -- surfacing
    e.g. a missing-dependency ImportError there isn't a spoiler (nothing has
    been mutated yet), and it turns a confusing generic failure into an
    actionable one ("ModuleNotFoundError: No module named 'heap'" -- prompting
    "did you add it as another source file tab?").

    Note loadTestsFromName doesn't raise on an import error inside the module
    under test -- it wraps it into a synthetic failing test instead, so the
    detail has to come from the *result's* errors/failures, not a try/except
    around collection.
    """
    _evict_known_modules()
    suite, err = _collect_suite(test_names)
    if suite is None:
        return False, err
    stream = io.StringIO()
    runner = unittest.TextTestRunner(stream=stream, verbosity=0)
    result = runner.run(suite)
    if result.wasSuccessful():
        return True, None
    if result.errors:
        return False, _last_line(result.errors[0][1])
    if result.failures:
        return False, _last_line(result.failures[0][1])
    return False, None


def plant_json(payload_json: str) -> str:
    """payload: {candidates: [name, ...], sources: {name: code}, tests: {name: code}, operator_set}.

    operator_set is one of "generic" | "project" | "all" (default "all" if
    omitted or not one of those three). ``tests`` may hold more than one test
    file -- they're all combined into a single suite for verification, the
    same way `unittest discover` runs a whole tests/ directory at once.
    ``candidates`` names which of ``sources`` are eligible to be mutated
    (others are loaded as plain, always-correct dependencies); the engine
    draws from *all* of their mutation sites as one combined pool and picks
    whichever one lands first, so which file actually ends up mutated is not
    decided by the caller -- only the eligible set is.
    """
    payload = json.loads(payload_json)
    candidate_names: list[str] = payload["candidates"]
    raw_sources: dict[str, str] = payload["sources"]
    raw_tests: dict[str, str] = payload["tests"]
    operator_set = payload.get("operator_set", "all")
    if operator_set not in OPERATOR_SETS:
        operator_set = "all"

    # Comments/docstrings are stripped from every source file (not just the
    # target) and every test file before anything else happens, so the hunt
    # is pure code with no hint text -- and so the diff shown on reveal is
    # clean too, rather than comparing a commented original against an
    # uncommented mutation.
    sources: dict[str, str] = {}
    for name, code in raw_sources.items():
        try:
            sources[name] = _clean_source(code)
        except SyntaxError as exc:
            return json.dumps({"ok": False, "error": f"Could not parse {name}.py: {exc}"})

    tests: dict[str, str] = {}
    for name, code in raw_tests.items():
        try:
            tests[name] = _clean_source(code)
        except SyntaxError as exc:
            return json.dumps({"ok": False, "error": f"Could not parse {name}.py: {exc}"})

    test_names = list(tests.keys())
    _setup_modules(sources, tests)

    passed, detail = _run_tests_detailed(test_names)
    if not passed:
        msg = (
            "Your pasted code doesn't pass its own test file yet -- fix that first, "
            "then come back to practice finding a planted mutation."
        )
        if detail:
            msg += f" Detail: {detail}"
        return json.dumps({"ok": False, "error": msg})

    operators = _select_operators(operator_set)

    # One combined pool across every eligible file -- each entry remembers
    # which file it came from, so the search below can try candidates from
    # different files in the same random shuffle, rather than exhausting one
    # file before ever considering another.
    pool: list[tuple[str, Candidate]] = []
    for name in candidate_names:
        source = sources.get(name)
        if source is None:
            return json.dumps({"ok": False, "error": f"{name}.py was marked as a mutation candidate but has no content."})
        try:
            tree = ast.parse(source, filename=f"{name}.py")
        except SyntaxError as exc:
            return json.dumps({"ok": False, "error": f"Could not parse {name}.py: {exc}"})
        for operator_fn in operators:
            for candidate in operator_fn(source, tree):
                pool.append((name, candidate))

    if not pool:
        return json.dumps({
            "ok": False,
            "error": f"No mutation candidates found across {', '.join(candidate_names)} with the '{operator_set}' operator set.",
        })
    random.shuffle(pool)

    tried = 0
    for file_name, candidate in pool[:MAX_ATTEMPTS]:
        tried += 1
        original_source = sources[file_name]
        mutated = _apply(original_source, candidate)
        path = f"{SRC_DIR}/{file_name}.py"
        _write(path, mutated)
        if not _run_tests(test_names):
            return json.dumps({
                "ok": True,
                "target": file_name,  # which file the mutation actually landed in
                "original_source": original_source,
                "mutated_source": mutated,
                "operator": candidate.operator,
                "description": candidate.description,
                "tried": tried,
                # every file's *cleaned* content, mutated file included -- lets
                # the UI show a hunt view listing every candidate file (not
                # just the one that got mutated) with comments/docstrings
                # already stripped from all of them.
                "cleaned_sources": {**sources, file_name: mutated},
            })
        _write(path, original_source)  # inert -- restore before trying the next candidate

    return json.dumps({
        "ok": False,
        "error": f"Tried {tried} mutation(s) across {', '.join(candidate_names)}; none changed test-suite behavior.",
    })


def check_fix_json(payload_json: str) -> str:
    """payload: {sources: {name: code}, test_names: [name, ...]}.

    Writes every file in ``sources`` as given (Hunt mode shows every
    candidate file, not just the one that was actually mutated, so the user
    may have edited any of them), reruns the full combined test suite
    against that, and returns the real unittest output for display.
    """
    payload = json.loads(payload_json)
    sources: dict[str, str] = payload["sources"]
    test_names: list[str] = payload["test_names"]

    for name, code in sources.items():
        _write(f"{SRC_DIR}/{name}.py", code)
    passed, output = _run_tests_with_output(test_names)
    return json.dumps({"passed": passed, "output": output})


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
