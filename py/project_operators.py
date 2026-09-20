"""Project-specific mutation operators.

operators.py holds general-purpose, cross-project operators (comparison
swaps, boolean logic, arithmetic, constants, boundary offsets). This file is
for patterns worth practicing against a *particular* course project that
don't belong in the generic set -- either because they're too narrow to be
broadly useful, or because they target something specific to that project's
domain (e.g. a pattern unique to how a particular assignment's grader
mutates code).

Same contract as operators.py: each operator is a function
``(source: str, tree: ast.Module) -> list[Candidate]``. Reuse the shared
``Candidate`` dataclass and the text-span helpers in operators.py
(``_node_text``, ``_text_between``, or import them directly) rather than
duplicating that logic.

Currently empty: everything found so far while comparing against the real
EECS 367 Project 1 mutator (e.g. the `self.width` -> `self.width - 1`
boundary case) turned out to be general enough to add to operators.py
instead, as `boundary_offset`. Add a function here, following the pattern
below, the next time a genuinely project-specific shape turns up.

Example template (not registered -- remove the leading underscore and add it
to ALL_PROJECT_OPERATORS once it's a real operator):

    def _example_operator(source: str, tree) -> list[Candidate]:
        candidates: list[Candidate] = []
        # walk `tree`, find mutation sites specific to this project, and
        # append Candidate(...) entries -- see operators.py's operators for
        # the exact pattern (locate a node/span, compute its offsets,
        # produce one or more replacement candidates).
        return candidates
"""
from __future__ import annotations

from operators import Candidate  # noqa: F401  (re-exported for operators added here)

ALL_PROJECT_OPERATORS: list = []
