"""
qgis_expr.py

Pure-string helpers for QGIS expressions that must be evaluated outside their
original render context (field calculators, filters, geometry generators).

Values are bound with ``with_variable`` rather than by rewriting expression
text, so string literals and identifiers that merely *contain* a variable
name are left untouched.
"""

import re


def with_variable(name: str, value_expression: str, expression: str) -> str:
    return f"with_variable('{name}', {value_expression}, ({expression}))"


def with_map_scale(expression: str, scale) -> str:
    """Bind ``@map_scale`` to ``scale`` for ``expression``."""
    if not expression or "map_scale" not in expression:
        return expression
    return with_variable("map_scale", repr(float(scale)), expression)


_TOKEN_RE = re.compile(
    r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|[$@]geometry\b", re.IGNORECASE)


def substitute_geometry(expression: str, replacement: str) -> str:
    """Replace ``@geometry`` and ``$geometry`` outside string literals and
    quoted identifiers by ``(replacement)``.

    ``with_variable('geometry', ...)`` cannot be used for this: QGIS resolves
    ``@geometry`` from the context feature, not from the variable stack
    (verified in ``tests/integration/test_end_to_end.py``).
    """
    def swap(match):
        token = match.group(0)
        return f"({replacement})" if token.lower() in ("@geometry", "$geometry") else token
    return _TOKEN_RE.sub(swap, expression or "")


def bind_geometry(expression: str, geometry_expression: str) -> str:
    """Evaluate ``expression`` with its feature geometry replaced by another geometry."""
    return substitute_geometry(expression, geometry_expression)
