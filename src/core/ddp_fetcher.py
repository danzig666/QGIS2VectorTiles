"""
ddp_fetcher.py

DataDefinedPropertiesFetcher — recursively walks a QGIS symbol/label object
tree and collects all active data-defined properties, returning
(field_type, expression, field_name) triples for use as calculated fields.
"""

import re

from qgis.core import (QgsExpression, QgsProperty, QgsPropertyDefinition, QgsSymbol,
                       QgsSymbolLayerUtils)

from ..utils.config import QVariant
from .fidelity.qgis_expr import with_map_scale


def _to_color_hex_expr(inner_expr: str) -> str:
    """Wrap a color expression to produce an 8-character hex RRGGBBAA string."""
    return (
        f"'#' || with_variable('hex', array_cat(generate_series(0,9),"
        f"array('A','B','C','D','E','F')), array_to_string("
        f"array_foreach(array('red','green','blue','alpha'),"
        f"with_variable('colo', color_part({inner_expr}, @element),"
        f"@hex[floor(@colo/16)] || @hex[@colo%16])), ''))"
    )


_RANDOM_FUNCTIONS = frozenset({"rand", "randf"})
# Symbol layers whose sub-symbol markers become one repeated texture.
_TEXTURE_PATTERNS = ("QgsPointPatternFillSymbolLayer", "QgsRandomMarkerFillSymbolLayer")


def is_random_only(expression: str) -> bool:
    """True if ``expression`` varies only by chance (``rand``/``randf``), not
    with the feature: QGIS draws a new value for every marker it renders."""
    parsed = QgsExpression(expression)
    if parsed.hasParserError() or parsed.referencedColumns() - {""} or parsed.needsGeometry():
        return False
    functions = {name.lower() for name in parsed.referencedFunctions()}
    return bool(functions & _RANDOM_FUNCTIONS) and not (
        set(parsed.referencedVariables()) & DataDefinedPropertiesFetcher.FEATURE_VARIABLES)


class DataDefinedPropertiesFetcher:
    """Recursively fetch active data-defined properties from a QGIS symbol/label object."""

    # Attribute name fragments whose getters are unsafe to call reflectively.
    _SKIP_FRAGMENTS: frozenset = frozenset({
        "_", "value", "index", "available", "config", "next", "attr",
        "clone", "function", "flag", "capabil", "remove", "symbols",
        "clear", "prepare", "dump", "copy", "create", "update", "replace",
    })

    _DATA_TYPE_MAP: dict = {
        QgsPropertyDefinition.DataType.DataTypeString: QVariant.String,
        QgsPropertyDefinition.DataType.DataTypeNumeric: QVariant.Double,
        QgsPropertyDefinition.DataType.DataTypeBoolean: QVariant.Bool,
    }

    FIELD_PREFIX = "q2vt"

    def __init__(self, qgis_object, min_scale, suffix=0, diagnostics=None, context=None):
        self._root = qgis_object
        self._min_scale = float(min_scale)
        self._suffix = f"_{suffix:02d}"
        self._results: list = []
        # Nested symbol layers (e.g. two font markers inside one marker)
        # share property keys: each collected object gets its own name part.
        self._objects = 0
        self._diagnostics = diagnostics
        self._context = context or {}
        # Colour of the symbol being walked: QGIS sets @symbol_color only
        # while drawing a symbol, so expressions reading it are given it here.
        self._symbol_colors: list = []
        self._in_texture = 0  # inside a pattern fill drawn as one texture

    def fetch(self) -> list:
        """Return [[field_type, expression, field_name], ...] for all active DDPs."""
        self._walk(self._root)
        return self._results

    def _is_safe_attr(self, attr: str) -> bool:
        lower = attr.lower()
        return not (
            any(frag in lower for frag in self._SKIP_FRAGMENTS)
            or (attr.startswith("set") and attr != lower)
            or attr[0].isupper()
        )

    def _walk(self, obj):
        """Recursively introspect obj's QGIS sub-objects for data-defined properties."""
        texture = type(obj).__name__ in _TEXTURE_PATTERNS
        self._in_texture += texture
        try:
            if isinstance(obj, QgsSymbol):
                self._symbol_colors.append(obj.color())
                try:
                    self._walk_attributes(obj)
                finally:
                    self._symbol_colors.pop()
            else:
                self._walk_attributes(obj)
        finally:
            self._in_texture -= texture

    def _walk_attributes(self, obj):
        for attr in dir(obj):
            if not self._is_safe_attr(attr):
                continue
            try:
                getter = getattr(obj, attr)
                if not callable(getter):
                    continue

                result = getter()
                children = result if isinstance(result, list) else [result]
                if not children:
                    continue

                first = children[0]
                if (
                    isinstance(first, type(obj))
                    or "qgis." not in str(type(first))
                    or first in self._results
                ):
                    continue

                prop_defs = self._resolve_prop_definitions(first, obj)
                if not prop_defs:
                    continue

                for child in children:
                    if hasattr(child, "dataDefinedProperties"):
                        self._collect_from(child, prop_defs)
                    self._walk(child)

            except (NameError, ValueError, AttributeError, TypeError):
                continue

    def _resolve_prop_definitions(self, child, parent):
        if hasattr(child, "propertyDefinitions"):
            return child.propertyDefinitions()
        if hasattr(parent, "propertyDefinitions"):
            return parent.propertyDefinitions()
        return None

    def _collect_from(self, obj, prop_defs):
        """Extract active DDP entries from obj and append to results."""
        props = obj.dataDefinedProperties()
        suffix = self._suffix if self._objects == 0 else f"{self._suffix}_{self._objects}"
        self._objects += 1
        for key in sorted(props.propertyKeys()):  # a set: fixed order across sessions
            prop = props.property(key)
            if not prop or not prop.isActive():
                continue

            property_kind = prop.propertyType()
            if property_kind not in (2, 3):
                continue

            prop_def = prop_defs.get(key)
            if prop_def is None:
                continue
            self._bind_symbol_color(prop)
            if self._in_texture and prop.propertyType() == 3 and \
                    is_random_only(prop.expressionString()):
                continue  # a new value per marker: drawn into the texture
            columns = QgsExpression(prop.asExpression()).referencedColumns()
            if columns and all(c.startswith(f"{self.FIELD_PREFIX}_property_") for c in columns):
                continue  # already replaced by a generated field
            data_type = prop_def.dataType()
            field_type = self._DATA_TYPE_MAP.get(data_type)
            field_name = self.field_name(prop_def, key, suffix)

            if data_type == QgsPropertyDefinition.DataType.DataTypeBoolean:
                expression = self._process_boolean_prop(prop, props, key, field_name)
            else:
                expression = self._process_expression_prop(
                    prop, props, key, prop_def, field_type, field_name
                )
                if expression is None:
                    continue

            self._results.append([field_type, expression, field_name])

    def _bind_symbol_color(self, prop) -> None:
        """Replace ``@symbol_color`` with the walked symbol's colour (as
        QGIS does while drawing it; e.g. sub-symbols following the line colour)."""
        if prop.propertyType() != 3 or not self._symbol_colors:
            return
        expression = prop.expressionString()
        if "symbol_color" not in QgsExpression(expression).referencedVariables():
            return
        color = QgsSymbolLayerUtils.encodeColor(self._symbol_colors[-1])
        prop.setExpressionString(re.sub(r"@symbol_color\b", f"'{color}'", expression))

    @classmethod
    def field_name(cls, prop_def, key, suffix: str) -> str:
        """Stable generated field name.

        Built from the non-localized property name and numeric key; the
        legacy name used ``description()``, which is translated in localized
        QGIS builds.
        """
        name = re.sub(r"[^0-9a-z]+", "_", (prop_def.name() or "").lower()).strip("_")
        return f"{cls.FIELD_PREFIX}_property_{name or 'p'}_{int(key)}{suffix}"

    def _process_boolean_prop(self, prop, props_collection, key: int, field_name: str) -> str:
        """Replace boolean property with a field reference; return original expression."""
        exp_prop = QgsProperty()
        exp_prop.setExpressionString(prop.asExpression())
        expression = exp_prop.expressionString()
        exp_prop.setExpressionString(f'"{field_name}"')
        props_collection.setProperty(key, exp_prop)
        return expression

    def _process_expression_prop(
        self, prop, props_collection, key, prop_def, field_type, field_name
    ):
        """
        Build the calculated-field expression for string/numeric DDPs.
        Returns None if the expression evaluates to a static value (no field needed).
        """
        original = prop.asExpression()
        raw = with_map_scale(original, self._min_scale)
        is_color = prop_def and "color" in prop_def.name().lower() and field_type == 10

        expression = _to_color_hex_expr(raw) if is_color else raw

        if self._is_static(original):
            # Feature-independent (possibly scale-dependent, resolved for
            # this rule's zoom): store the evaluated value as a static
            # property and create no field. The legacy code tested
            # truthiness, so 0/False/'' became per-feature fields, and stored
            # the value as a quoted string (turning 3 into '3').
            value = QgsExpression(raw).evaluate()
            props_collection.setProperty(key, QgsProperty.fromValue(value))
            return None

        field_ref = f'"{field_name}"'
        if is_color:
            field_ref = (
                f"with_variable('color', \"{field_name}\", "
                f"'#' || substr(@color,8,2) || substr(@color,2,6))"
            )
        if "array" in expression:
            expression = f"try(array_to_string({expression}), {expression})"

        prop.setExpressionString(field_ref)
        return expression

    # Functions/variables whose value depends on the feature, the render
    # viewport or time. Expressions using them are never folded to constants.
    _FEATURE_FUNCTIONS = frozenset({
        "$id", "$area", "$length", "$perimeter", "$x", "$y", "$geometry",
        "$currentfeature", "attribute", "attributes", "get_feature", "get_feature_by_id",
        "aggregate", "relation_aggregate", "rand", "randf", "uuid", "now",
        "represent_value", "is_selected", "num_selected",
    })
    FEATURE_VARIABLES = frozenset({
        "feature", "id", "geometry", "geometry_part_num", "geometry_part_count",
        "geometry_point_num", "geometry_point_count", "map_extent", "map_extent_center",
        "map_rotation", "canvas_cursor_point", "symbol_color", "symbol_angle",
    })

    @classmethod
    def _is_static(cls, expression: str) -> bool:
        """True if ``expression`` has the same value for every feature.

        Must be given the expression *before* ``with_map_scale`` wrapping:
        QGIS reports every ``with_variable`` expression as reading all
        attributes and the geometry.
        """
        qexpr = QgsExpression(with_map_scale(expression, 1.0))
        dependencies = QgsExpression(expression)
        if qexpr.hasParserError() or dependencies.hasParserError():
            return False
        if dependencies.referencedColumns() or dependencies.needsGeometry():
            return False
        functions = {name.lower() for name in dependencies.referencedFunctions()}
        if functions & cls._FEATURE_FUNCTIONS:
            return False
        if set(dependencies.referencedVariables()) & cls.FEATURE_VARIABLES:
            return False
        qexpr.evaluate()
        return not qexpr.hasEvalError()
