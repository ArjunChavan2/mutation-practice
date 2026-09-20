"""Mutation operators: pure functions that scan a parsed module for mutation
candidates and describe how to apply each one via a direct text-offset
substitution on the original source (never by re-serializing the AST, which
would reformat the whole file instead of producing a one-line diff).

Each operator has the signature ``(source: str, tree: ast.Module) -> list[Candidate]``.
Candidates that would require replacing a span crossing a comment or otherwise
ambiguous text are simply skipped (not raised) -- it's fine for a given site
to yield zero candidates; the engine draws from whatever the whole operator
set finds across the file.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass
class Candidate:
    lineno: int
    col_offset: int
    end_lineno: int
    end_col_offset: int
    replacement: str
    operator: str
    description: str


def _text_between(lines: list[str], start: tuple[int, int], end: tuple[int, int]) -> str:
    """Source text strictly between two (1-indexed line, 0-indexed col) positions."""
    s_line, s_col = start
    e_line, e_col = end
    if s_line == e_line:
        return lines[s_line - 1][s_col:e_col]
    parts = [lines[s_line - 1][s_col:]]
    parts.extend(lines[s_line:e_line - 1])
    parts.append(lines[e_line - 1][:e_col])
    return "\n".join(parts)


def _node_text(lines: list[str], node: ast.AST) -> str:
    return _text_between(lines, (node.lineno, node.col_offset), (node.end_lineno, node.end_col_offset))


# -- comparison operator swap ------------------------------------------------

_CMP_SYMBOLS: dict[type, str] = {
    ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==", ast.NotEq: "!=",
}
_ALL_CMP_SYMBOLS = list(_CMP_SYMBOLS.values())


def comparison_swap(source: str, tree: ast.AST) -> list[Candidate]:
    lines = source.splitlines()
    candidates: list[Candidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        for i, op in enumerate(node.ops):
            symbol = _CMP_SYMBOLS.get(type(op))
            if symbol is None:  # Is/IsNot/In/NotIn -- out of scope for this operator
                continue
            left, right = operands[i], operands[i + 1]
            start = (left.end_lineno, left.end_col_offset)
            end = (right.lineno, right.col_offset)
            if start[0] != end[0]:
                continue  # keep the offset math simple: skip multi-line comparisons
            between = lines[start[0] - 1][start[1]:end[1]]
            idx = between.find(symbol)
            if idx == -1:
                continue
            abs_col = start[1] + idx
            for other in _ALL_CMP_SYMBOLS:
                if other == symbol:
                    continue
                candidates.append(Candidate(
                    start[0], abs_col, start[0], abs_col + len(symbol), other,
                    "comparison_swap", f"Changed `{symbol}` to `{other}` on line {start[0]}.",
                ))
    return candidates


# -- boolean operator swap (and/or) ------------------------------------------

def boolean_swap(source: str, tree: ast.AST) -> list[Candidate]:
    lines = source.splitlines()
    candidates: list[Candidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.BoolOp):
            continue
        symbol = "and" if isinstance(node.op, ast.And) else "or"
        other = "or" if symbol == "and" else "and"
        for i in range(len(node.values) - 1):
            left, right = node.values[i], node.values[i + 1]
            start = (left.end_lineno, left.end_col_offset)
            end = (right.lineno, right.col_offset)
            if start[0] != end[0]:
                continue
            between = lines[start[0] - 1][start[1]:end[1]]
            idx = between.find(symbol)
            if idx == -1:
                continue
            abs_col = start[1] + idx
            candidates.append(Candidate(
                start[0], abs_col, start[0], abs_col + len(symbol), other,
                "boolean_swap", f"Changed `{symbol}` to `{other}` on line {start[0]}.",
            ))
    return candidates


# -- boolean negation on if/while conditions ---------------------------------

def boolean_negate(source: str, tree: ast.AST) -> list[Candidate]:
    lines = source.splitlines()
    candidates: list[Candidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.If, ast.While)):
            continue
        test = node.test
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            inner_text = _node_text(lines, test.operand)
            candidates.append(Candidate(
                test.lineno, test.col_offset, test.end_lineno, test.end_col_offset, inner_text,
                "boolean_negate", f"Removed `not` from the condition on line {test.lineno}.",
            ))
        else:
            span_text = _node_text(lines, test)
            candidates.append(Candidate(
                test.lineno, test.col_offset, test.end_lineno, test.end_col_offset,
                f"not ({span_text})", "boolean_negate", f"Wrapped the condition on line {test.lineno} in `not (...)`.",
            ))
    return candidates


# -- arithmetic operator swap -------------------------------------------------

_ARITH_SYMBOLS: dict[type, str] = {
    ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.FloorDiv: "//",
}
_ALL_ARITH_SYMBOLS = list(_ARITH_SYMBOLS.values())


def arithmetic_swap(source: str, tree: ast.AST) -> list[Candidate]:
    lines = source.splitlines()
    candidates: list[Candidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp):
            continue
        symbol = _ARITH_SYMBOLS.get(type(node.op))
        if symbol is None:  # Div, Mod, Pow, bitwise ops, etc. -- out of scope
            continue
        start = (node.left.end_lineno, node.left.end_col_offset)
        end = (node.right.lineno, node.right.col_offset)
        if start[0] != end[0]:
            continue
        between = lines[start[0] - 1][start[1]:end[1]]
        idx = between.find(symbol)
        if idx == -1:
            continue
        abs_col = start[1] + idx
        for other in _ALL_ARITH_SYMBOLS:
            if other == symbol:
                continue
            candidates.append(Candidate(
                start[0], abs_col, start[0], abs_col + len(symbol), other,
                "arithmetic_swap", f"Changed `{symbol}` to `{other}` on line {start[0]}.",
            ))
    return candidates


# -- off-by-one / boolean constant tweak -------------------------------------

def constant_tweak(source: str, tree: ast.AST) -> list[Candidate]:
    candidates: list[Candidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant):
            continue
        value = node.value
        span = (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset)
        if isinstance(value, bool):
            other = not value
            candidates.append(Candidate(
                *span, str(other), "constant_tweak", f"Changed `{value}` to `{other}` on line {node.lineno}.",
            ))
        elif isinstance(value, int):
            for delta in (1, -1):
                new_value = value + delta
                candidates.append(Candidate(
                    *span, str(new_value), "constant_tweak",
                    f"Changed `{value}` to `{new_value}` on line {node.lineno}.",
                ))
    return candidates


# -- boundary offset (off-by-one on a non-constant comparison operand) ------

def boundary_offset(source: str, tree: ast.AST) -> list[Candidate]:
    """Off-by-one offset on a comparison operand that isn't a bare literal.

    Targets a shape `constant_tweak` can't reach: a boundary check like
    `cell_x < self.width` has no literal Constant node to tweak and no
    existing arithmetic BinOp to swap -- the mutation has to *introduce* a
    `- 1`/`+ 1` around the whole operand. Skips bare Constant operands
    (`constant_tweak` already covers those) to avoid a redundant, weaker
    duplicate of that operator's job.
    """
    lines = source.splitlines()
    candidates: list[Candidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for operand in (node.left, *node.comparators):
            if isinstance(operand, ast.Constant):
                continue
            span_text = _node_text(lines, operand)
            for symbol in ("-", "+"):
                candidates.append(Candidate(
                    operand.lineno, operand.col_offset, operand.end_lineno, operand.end_col_offset,
                    f"({span_text} {symbol} 1)", "boundary_offset",
                    f"Changed `{span_text}` to `({span_text} {symbol} 1)` on line {operand.lineno} "
                    f"(off-by-one on a comparison boundary).",
                ))
    return candidates


ALL_OPERATORS = [
    comparison_swap, boolean_swap, boolean_negate, arithmetic_swap, constant_tweak, boundary_offset,
]
