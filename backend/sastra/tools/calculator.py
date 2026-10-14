"""Deterministic, safe arithmetic (AST whitelist — no eval of arbitrary code)."""
from __future__ import annotations

import ast
import math
import operator
import re

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UN = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {"sqrt": math.sqrt, "log": math.log, "log10": math.log10, "ln": math.log, "exp": math.exp,
          "sin": math.sin, "cos": math.cos, "tan": math.tan, "abs": abs, "round": round,
          "floor": math.floor, "ceil": math.ceil, "min": min, "max": max}
_CONSTS = {"pi": math.pi, "e": math.e, "π": math.pi}


class CalcError(ValueError):
    pass


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > 1000 or abs(left) > 1e6):
            raise CalcError("Exponent too large")
        return _BIN[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UN:
        return _UN[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
        return _FUNCS[node.func.id](*[_eval(a) for a in node.args])
    raise CalcError(f"Unsupported expression element: {type(node).__name__}")


def calculate(expr: str) -> str:
    expr = expr.replace("^", "**").replace("×", "*").replace("÷", "/")
    expr = re.sub(r"(?<=\d),(?=\d{3}\b)", "", expr)  # 1,000 -> 1000; min(1,2) untouched
    if len(expr) > 300:
        raise CalcError("Expression too long")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise CalcError("Not a valid expression") from e
    try:
        value = _eval(tree)
    except ZeroDivisionError as e:
        raise CalcError("Division by zero") from e
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return f"{value:.10g}"
    return str(value)
