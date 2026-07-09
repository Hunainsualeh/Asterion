"""Lightweight code analysis — no external deps.

For Python files this uses the stdlib `ast` module to get real numbers
(function count, max nesting depth, a rough cyclomatic-complexity count per
function). For anything else it falls back to a line/keyword heuristic. Good
enough to flag "this function is doing too much" without pulling in a linter
dependency for languages we don't control ahead of time.
"""
from __future__ import annotations

import ast

from app.tools.registry import ToolContext, register


def _python_metrics(source: str) -> dict:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {"error": f"syntax error: {exc}"}

    functions = []
    branch_nodes = (ast.If, ast.For, ast.While, ast.Try, ast.With, ast.BoolOp)

    def max_depth(node: ast.AST, depth: int = 0) -> int:
        child_depths = [
            max_depth(child, depth + 1) if isinstance(child, branch_nodes) else max_depth(child, depth)
            for child in ast.iter_child_nodes(node)
        ]
        return max(child_depths, default=depth)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            complexity = 1 + sum(1 for n in ast.walk(node) if isinstance(n, branch_nodes))
            functions.append(
                {
                    "name": node.name,
                    "lines": (node.end_lineno or node.lineno) - node.lineno + 1,
                    "cyclomatic_complexity": complexity,
                    "max_nesting_depth": max_depth(node),
                }
            )

    flags = [
        f"{fn['name']}: cyclomatic complexity {fn['cyclomatic_complexity']} (consider splitting)"
        for fn in functions
        if fn["cyclomatic_complexity"] > 10
    ] + [
        f"{fn['name']}: nesting depth {fn['max_nesting_depth']} (consider flattening)"
        for fn in functions
        if fn["max_nesting_depth"] > 4
    ]
    return {"language": "python", "functions": functions, "flags": flags}


def _generic_metrics(source: str) -> dict:
    lines = source.splitlines()
    max_indent = 0
    for line in lines:
        stripped = line.lstrip(" ")
        indent = (len(line) - len(stripped)) // 2
        max_indent = max(max_indent, indent)
    flags = []
    if len(lines) > 400:
        flags.append(f"file is {len(lines)} lines long (consider splitting)")
    if max_indent > 6:
        flags.append(f"deep indentation detected (~{max_indent} levels; consider flattening)")
    return {"language": "generic", "line_count": len(lines), "max_indent_level": max_indent, "flags": flags}


@register(
    name="analyze_code",
    description=(
        "Analyze a source file for size/complexity red flags (cyclomatic complexity, "
        "nesting depth, file length). Use before approving/flagging a review."
    ),
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Relative file path to analyze."}},
        "required": ["path"],
    },
    agents=["architect", "reviewer", "debugger"],
)
async def analyze_code(ctx: ToolContext, path: str) -> dict:
    from app.tools.fs_tools import _resolve

    target = _resolve(ctx, path)
    if not target.exists():
        return {"error": f"no such file: {path}"}
    source = target.read_text(encoding="utf-8", errors="replace")
    if target.suffix == ".py":
        return {"path": path, **_python_metrics(source)}
    return {"path": path, **_generic_metrics(source)}
