"""
rules_flattener.py

RulesFlattener — walks every visible vector layer in the current QGIS project,
converts any non-rule-based renderer / labeling system to rule-based, then
recursively flattens the rule hierarchy with full property inheritance
(scale range, filter expression, symbol layers).

Depends on: config, zoom_levels, flattened_rule
"""

import math
from typing import List, Optional, Union

from qgis.core import (
    Qgis,
    QgsProject,
    QgsVectorLayer,
    QgsRuleBasedRenderer,
    QgsRuleBasedLabeling,
    QgsPalLayerSettings,
    QgsTextFormat,
    QgsTextBackgroundSettings,
    QgsGraduatedSymbolRenderer,
    QgsCategorizedSymbolRenderer,
    QgsReadWriteContext,
    QgsExpression,
    QgsSimpleLineSymbolLayer,
    QgsFillSymbol,
    QgsSymbolLayer,
    QgsSimpleFillSymbolLayer,
    QgsProperty
    )

from ..utils.config import Qt
from ..utils.config import QDomDocument
from ..utils.flattened_rule import FlattenedRule
from ..utils.zoom_levels import ZoomLevels
from .fidelity.diagnostics import DiagnosticCollector
from .fidelity.model import ZoomInterval
from .fidelity import zoom as fidelity_zoom
from .fidelity.qgis_expr import (and_filters, enabled_condition, substitute_geometry,
                                 with_map_scale)
from .fidelity.materialize import coerce_to_symbol_type
from .materializer import SymbolMaterializer



def combine_scale_ranges(low_a: float, high_a: float, low_b: float, high_b: float):
    """Intersection of two QGIS scale ranges (minimum = zoomed-out limit,
    maximum = zoomed-in limit; 0 = no limit)."""
    lows = [v for v in (low_a, low_b) if v]
    highs = [v for v in (high_a, high_b) if v]
    return (min(lows) if lows else 0.0), (max(highs) if highs else 0.0)


class RulesFlattener:
    """Flatten QGIS rule-based styling with full property inheritance."""

    RULE_TYPES = {0: "renderer", 1: "labeling"}

    def __init__(self, min_zoom: int, max_zoom: int, utils_dir, feedback,
                 diagnostics: Optional[DiagnosticCollector] = None, layer_ids=None,
                 scale_limits=None, extent=None):
        self.min_zoom = min_zoom
        self.extent = extent  # export extent (EPSG:3857), for renderer statistics
        # {layer id: (min scale, max scale)}: extra scale range of a layer
        # (publishing, web only), on top of its own; 0 = no limit.
        self.scale_limits = dict(scale_limits or {})
        self.max_zoom = max_zoom
        self.utils_dir = utils_dir
        self.layer_tree_root = QgsProject.instance().layerTreeRoot()
        # Publishing: exactly these layers, visible or not (None: visible layers).
        self.layer_ids = None if layer_ids is None else set(layer_ids)
        self._legend_keys: dict = {}
        self._current_provenance = None
        self.flattened_rules: List[FlattenedRule] = []
        self.feedback = feedback
        self.diagnostics = diagnostics or DiagnosticCollector()
        # Converted/cloned rule systems are kept alive here instead of being
        # assigned to the project layers: exporting must never modify the
        # user's project (renderer, labeling, ELSE rules, scale visibility).
        self._rule_systems: list = []
        self.materializer = SymbolMaterializer(self.diagnostics, max_zoom=max_zoom)
        # Tree-unique counter; reset per (layer, rule_type) pass. Used only to
        # disambiguate output_dataset when sibling subtrees share (l,t,d,r,...).
        self._unique_counter = 0
        # Draw order: rule sequence within a layer (QGIS draws a feature's
        # rules in tree pre-order, later rules on top) and whether the layer's
        # renderer honours symbol-layer rendering passes.
        self._draw_seq = 0
        self._honor_passes = False

    def flatten_all_rules(self) -> List[FlattenedRule]:
        """Extract and flatten all rules from visible vector layers."""
        for layer_idx, node in enumerate(self.layer_tree_root.findLayers()):
            if self._is_valid_layer(node.layer()):
                self._process_layer_rules(node.layer(), layer_idx)

        if self.flattened_rules:
            self._clone_rule_objects()

        self._remove_unusable_rules()
        return self.flattened_rules

    def _remove_unusable_rules(self):
        """Remove rules without a symbol (renderer) or label expression (labeling)."""
        for flat_rule in list(self.flattened_rules):
            usable = False
            if flat_rule.get_attr("t") == 0 and flat_rule.rule.symbol():
                usable = True
            elif flat_rule.get_attr("t") == 1 and flat_rule.rule.settings():
                if flat_rule.rule.settings().getLabelExpression().expression():
                    usable = True
            if not usable:
                self.flattened_rules.remove(flat_rule)

    def _clone_rule_objects(self):
        """Clone each rule's underlying object to prevent shared-property mutations."""
        for flat_rule in self.flattened_rules:
            rule = flat_rule.rule
            rule_type = flat_rule.get_attr("t")

            if rule_type == 0:
                clone = QgsRuleBasedRenderer.Rule(None)
            else:
                clone = QgsRuleBasedLabeling.Rule(None)

            clone.setDescription(rule.description())
            clone.setFilterExpression(rule.filterExpression())
            clone.setMinimumScale(rule.minimumScale())
            clone.setMaximumScale(rule.maximumScale())

            if rule_type == 0 and rule.symbol():
                clone.setSymbol(rule.symbol().clone())
            elif rule_type == 1 and rule.settings():
                new_settings = QgsPalLayerSettings(rule.settings())
                new_format = QgsTextFormat(new_settings.format())
                new_bg = QgsTextBackgroundSettings(new_format.background())
                if new_bg.markerSymbol():
                    new_bg.setMarkerSymbol(new_bg.markerSymbol().clone())
                new_format.setBackground(new_bg)
                new_settings.setFormat(new_format)
                clone.setSettings(new_settings)

            flat_rule.rule = clone

    def _is_valid_layer(self, layer) -> bool:
        """Return True if the layer is a visible non-geometry-collection vector layer."""
        is_vector = layer.type() == 0 and layer.geometryType() != 4
        if self.layer_ids is not None:
            return is_vector and layer.id() in self.layer_ids
        node = self.layer_tree_root.findLayer(layer.id())
        is_visible = node.isVisible() if node is not None else False
        return is_vector and is_visible

    def _process_layer_rules(self, layer: QgsVectorLayer, layer_idx: int):
        """Process both renderer and labeling rules for a single layer."""
        for rule_type, type_name in self.RULE_TYPES.items():
            rule_system = self._get_or_convert_rule_system(layer, rule_type)
            if not rule_system:
                continue
            self._legend_keys = self._legend_key_map(layer, rule_system, rule_type)
            self._rule_systems.append(rule_system)
            self._drop_missing_field_properties(rule_system, layer, rule_type)
            root_rule = self._prepare_root_rule(rule_system, layer)
            if rule_type == 0:
                self._draw_seq = 0
                self._honor_passes = self._renderer_honors_passes(layer.renderer())
            if root_rule:
                # Reset per (layer, rule_type) pass; values must stay < 100
                # because FlattenedRule.set_attr formats as 2 digits.
                self._unique_counter = 0
                before = len(self.flattened_rules)
                self._flatten_rule(layer, layer_idx, root_rule, rule_type, 0, 0)
                mode = self._merge_mode(layer.renderer()) if rule_type == 0 else ""
                heatmap = self._heatmap_spec(layer) if rule_type == 0 and \
                    layer.renderer() is not None and layer.renderer().type() == "heatmapRenderer" else None
                for flat_rule in self.flattened_rules[before:]:
                    flat_rule.merge = mode
                    flat_rule.heatmap = heatmap

    @staticmethod
    @staticmethod
    def _leader_provenance(pinned):
        import dataclasses  # pylint: disable=import-outside-toplevel
        if pinned.provenance is None:
            return None
        return dataclasses.replace(pinned.provenance, component="leader")

    @staticmethod
    def _legend_key_map(layer, rule_system, rule_type: int) -> dict:
        """{rule key in ``rule_system``: (legend key, legend label)} of the
        original renderer, for publication provenance.

        Rule-based renderers keep their rule keys when cloned (identity);
        single/categorized/graduated renderers were converted to rules in
        the order of their active legend items, which carry the stable
        category/range keys. Labeling rules keep their own keys."""
        mapping = {}
        if rule_type != 0:
            for index, rule in enumerate(rule_system.rootRule().children()):
                mapping[rule.ruleKey()] = (rule.ruleKey() if isinstance(layer.labeling(), QgsRuleBasedLabeling)
                                           else "labels", rule.description() or "")
            return mapping
        renderer = layer.renderer()
        if renderer is None or isinstance(renderer, QgsRuleBasedRenderer):
            return mapping
        try:
            items = [item for item in renderer.legendSymbolItems()
                     if item.symbol() is not None]
        except (AttributeError, RuntimeError):
            return mapping
        if isinstance(renderer, (QgsCategorizedSymbolRenderer, QgsGraduatedSymbolRenderer)):
            states = (renderer.categories() if isinstance(renderer, QgsCategorizedSymbolRenderer)
                      else renderer.ranges())
            items = [item for item, state in zip(items, states) if state.renderState()]
        for rule, item in zip(rule_system.rootRule().children(), items):
            mapping[rule.ruleKey()] = (item.ruleKey() or "single", item.label() or "")
        return mapping

    def _provenance(self, layer, rule, rule_type: int, origin, ancestors) -> object:
        """RuleProvenance of a rule about to be flattened (see
        publishing.provenance); ``origin`` is the rule an ELSE variant was
        split from."""
        from ..publishing.provenance import RuleProvenance  # pylint: disable=import-outside-toplevel
        source = origin if origin is not None else rule
        key = self._rule_key(source)
        legend_key, label = self._legend_keys.get(key, (key, ""))
        if not label:
            label = (source.label() if rule_type == 0 else source.description()) or ""
        parents = tuple(self._legend_keys.get(self._rule_key(a), (self._rule_key(a), ""))[0]
                        for a in ancestors if self._rule_key(a))
        return RuleProvenance(
            layer_id=layer.id(), layer_name=layer.name(),
            kind="symbology" if rule_type == 0 else "labeling",
            rule_key=legend_key, rule_label=label, parent_keys=parents,
            else_rule=self._is_else_rule(source))

    def _drop_missing_field_properties(self, rule_system, layer, rule_type: int) -> None:
        """Switch off data-defined properties that read a field the layer does
        not have. QGIS cannot prepare such a property and draws the static
        value (Szabályozás övezetkódok: a colour rule on "beep_szant" leaves
        the labels dark grey in QGIS; the export evaluated it to blue).

        Works on the cloned rule system: the project is not changed."""
        fields = {field.name().lower() for field in layer.fields()}
        # Qgis.PropertyType (3.36+) or QgsProperty.Type (older).
        kinds = getattr(Qgis, "PropertyType", None)
        field_kind = getattr(kinds, "Field", None) if kinds else QgsProperty.FieldBasedProperty
        expression_kind = getattr(kinds, "Expression", None) if kinds else \
            QgsProperty.ExpressionBasedProperty

        def missing(prop) -> List[str]:
            if prop.propertyType() == field_kind:
                names = [prop.field()]
            elif prop.propertyType() == expression_kind:
                names = list(QgsExpression(prop.asExpression()).referencedColumns())
            else:
                return []
            return [name for name in names if name and not name.startswith("#!")
                    and name.lower() not in fields]

        def clean(collection, where: str) -> bool:
            changed = False
            for key in collection.propertyKeys():
                prop = collection.property(key)
                if not prop.isActive():
                    continue
                absent = missing(prop)
                if absent:
                    prop.setActive(False)
                    collection.setProperty(key, prop)
                    changed = True
                    self.diagnostics.add(
                        "Q2VT_DDP_MISSING_FIELD",
                        f'Layer "{layer.name()}", {where}: field(s) {", ".join(sorted(set(absent)))} '
                        f"not found; the static value is used, as in QGIS.",
                        layer_id=layer.id(), detail=prop.asExpression())
            return changed

        def clean_symbol(symbol, where: str) -> None:
            if symbol is None:
                return
            properties = symbol.dataDefinedProperties()
            if clean(properties, where):
                symbol.setDataDefinedProperties(properties)
            for layer_index in range(symbol.symbolLayerCount()):
                symbol_layer = symbol.symbolLayer(layer_index)
                properties = symbol_layer.dataDefinedProperties()
                if clean(properties, f"{where}, symbol layer {layer_index + 1}"):
                    symbol_layer.setDataDefinedProperties(properties)
                clean_symbol(symbol_layer.subSymbol(), where)

        for rule in rule_system.rootRule().descendants():
            if rule_type == 0:
                clean_symbol(rule.symbol(), f"symbol of rule '{rule.label() or rule.description()}'")
                continue
            settings = rule.settings()
            if settings is None:
                continue
            where = f"labels of rule '{rule.description()}'" if rule.description() else "labels"
            properties = settings.dataDefinedProperties()
            changed = clean(properties, where)
            text_format = settings.format()
            format_properties = text_format.dataDefinedProperties()
            if clean(format_properties, where):
                text_format.setDataDefinedProperties(format_properties)
                settings.setFormat(text_format)
                changed = True
            if changed:  # in place: setSettings() would delete these very settings
                settings.setDataDefinedProperties(properties)

    @staticmethod
    def _renderer_honors_passes(renderer) -> bool:
        """QGIS orders symbol layers by rendering pass for rule-based renderers
        always, for other renderers only with symbol levels enabled."""
        if renderer is None:
            return False
        if isinstance(renderer, QgsRuleBasedRenderer):
            return True
        try:
            return bool(renderer.usingSymbolLevels())
        except (AttributeError, RuntimeError):
            return False

    def _get_or_convert_rule_system(self, layer: QgsVectorLayer, rule_type: int):
        """Return the layer's rule system, converting from single/graduated/categorized if needed."""
        if rule_type == 0:
            return self._convert_renderer_to_rules(layer)
        return self._convert_labeling_to_rules(layer)

    @staticmethod
    def _heatmap_placeholder(heatmap):
        """One rule exporting the points; its marker only carries the weight
        expression (Size) into the tiles. The style draws a MapLibre heatmap
        instead (FlattenedRule.heatmap)."""
        from qgis.core import QgsMarkerSymbol  # pylint: disable=import-outside-toplevel
        symbol = QgsMarkerSymbol.createSimple({"name": "circle", "size": "1"})
        weight = heatmap.weightExpression()
        if weight:
            symbol.symbolLayer(0).setDataDefinedProperty(QgsSymbolLayer.Property.PropertySize,
                                                         QgsProperty.fromExpression(weight))
        root = QgsRuleBasedRenderer.Rule(None)
        root.appendChild(QgsRuleBasedRenderer.Rule(symbol, 0, 0, "", "Heatmap"))
        return QgsRuleBasedRenderer(root)

    def _heatmap_spec(self, layer):
        """fidelity.heatmap spec of a heatmap renderer: ramp, radius and the
        maximum density per zoom, from the points inside the export extent."""
        from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform,  # pylint: disable=import-outside-toplevel
                               QgsExpressionContext, QgsExpressionContextUtils,
                               QgsFeatureRequest, QgsPointXY)
        from .fidelity import heatmap as hm  # pylint: disable=import-outside-toplevel
        from .fidelity.units import normalize_unit  # pylint: disable=import-outside-toplevel
        from .materializer import _to_mm  # pylint: disable=import-outside-toplevel
        renderer = layer.renderer()
        project = QgsProject.instance()
        crs = project.crs() if project.crs().isValid() else layer.crs()
        to_project = QgsCoordinateTransform(layer.crs(), crs, project.transformContext())
        request = QgsFeatureRequest()
        latitude = 0.0
        if self.extent is not None:
            web = QgsCoordinateReferenceSystem("EPSG:3857")
            request.setFilterRect(QgsCoordinateTransform(web, layer.crs(), project.transformContext())
                                  .transformBoundingBox(self.extent))
            center = QgsCoordinateTransform(web, QgsCoordinateReferenceSystem("EPSG:4326"),
                                            project.transformContext()).transform(self.extent.center())
            latitude = center.y()
        context = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
        weight = QgsExpression(renderer.weightExpression()) if renderer.weightExpression() else None
        if weight is not None:
            weight.prepare(context)
        points = []
        for feature in layer.getFeatures(request):
            geometry = feature.geometry()
            if geometry.isEmpty():
                continue
            context.setFeature(feature)
            value = 1.0
            if weight is not None:
                try:
                    value = float(weight.evaluate(context))
                except (TypeError, ValueError):
                    continue
            for part in geometry.constParts():
                point = to_project.transform(QgsPointXY(part.x(), part.y()))
                points.append((point.x(), point.y(), value))
            if len(points) > 200000:
                break
        unit = renderer.radiusUnit()
        screen = normalize_unit(unit) != "map" and _to_mm(1.0, unit) is not None
        zooms = list(range(int(self.min_zoom), int(self.max_zoom) + 1))
        if crs.isGeographic():
            self.diagnostics.add("Q2VT_HEATMAP_APPROXIMATE",
                                 "Heatmap in a geographic project CRS: map-unit radius and density "
                                 "are approximated.", layer_id=layer.id())
        if not renderer.maximumValue():
            self.diagnostics.add("Q2VT_HEATMAP_APPROXIMATE",
                                 "Heatmap with an automatic maximum: QGIS scales it to the densest "
                                 "spot in view; the web map uses the densest spot of the exported "
                                 "data at each zoom.", layer_id=layer.id())
        return hm.build_spec(renderer.colorRamp(), renderer.radius(), "screen" if screen else "map",
                             float(renderer.maximumValue() or 0.0), points, zooms,
                             latitude=latitude, mercator=crs.authid() == "EPSG:3857",
                             mm_per_unit=_to_mm(1.0, unit) if screen else None)

    @staticmethod
    def _merge_mode(renderer) -> str:
        """"merge" / "invert" for renderers drawing a symbol's features
        together (merged features, inverted polygons), else ""."""
        kind = renderer.type() if renderer is not None else ""
        return {"mergedFeatureRenderer": "merge", "invertedPolygonRenderer": "invert"}.get(kind, "")

    def _convert_renderer_to_rules(self, layer: QgsVectorLayer):
        """Convert any renderer to a QgsRuleBasedRenderer, preserving active items only."""
        system = layer.renderer()
        if not system:
            return None

        system = system.clone()
        if isinstance(system, QgsRuleBasedRenderer):
            return system
        if system.type() == "heatmapRenderer":
            return self._heatmap_placeholder(system)

        inactive_indices = self._get_inactive_item_indices(system)
        rule_renderer = QgsRuleBasedRenderer.convertFromRenderer(system)

        if rule_renderer and inactive_indices:
            for idx in sorted(inactive_indices, reverse=True):
                rule_renderer.rootRule().removeChildAt(idx)

        return rule_renderer

    def _get_inactive_item_indices(self, system) -> List[int]:
        """Return indices of inactive items from graduated or categorized renderers."""
        items_method = None
        if isinstance(system, QgsGraduatedSymbolRenderer):
            items_method = "ranges"
        elif isinstance(system, QgsCategorizedSymbolRenderer):
            items_method = "categories"

        if items_method:
            items = getattr(system, items_method)()
            return [i for i, item in enumerate(items) if not item.renderState()]
        return []

    def _convert_labeling_to_rules(self, layer: QgsVectorLayer):
        """Convert any labeling system to QgsRuleBasedLabeling."""
        system = layer.labeling()
        if not system or not layer.labelsEnabled():
            return None

        system = system.clone()
        if isinstance(system, QgsRuleBasedLabeling):
            return system

        rule = QgsRuleBasedLabeling.Rule(QgsPalLayerSettings(system.settings()))
        root = QgsRuleBasedLabeling.Rule(QgsPalLayerSettings())
        root.appendChild(rule)
        return QgsRuleBasedLabeling(root)

    def _prepare_root_rule(self, rule_system, layer: QgsVectorLayer):
        """Set layer-level scale visibility on the root rule."""
        root_rule = rule_system.rootRule()
        low, high = (layer.minimumScale(), layer.maximumScale()) \
            if layer.hasScaleBasedVisibility() else (0.0, 0.0)
        extra_low, extra_high = self.scale_limits.get(layer.id(), (0.0, 0.0))
        low, high = combine_scale_ranges(low, high, extra_low or 0.0, extra_high or 0.0)
        if low or high:
            root_rule.setMinimumScale(low)
            root_rule.setMaximumScale(high)
        return root_rule

    def _set_rule_attributes(
        self,
        flat_rule: FlattenedRule,
        layer_idx: int,
        rule_type: int,
        rule_level: int,
        rule_idx: int,
    ):
        """Stamp identification and zoom attributes onto the flat rule."""
        flat_rule.set_attr("l", layer_idx)
        flat_rule.set_attr("t", rule_type)
        flat_rule.set_attr("d", rule_level)
        flat_rule.set_attr("r", rule_idx)
        flat_rule.set_attr("g", flat_rule.layer.geometryType())
        flat_rule.set_attr("c", flat_rule.layer.geometryType())
        flat_rule.visibility = self._rule_visibility(flat_rule.rule)
        flat_rule.set_attr("o", self._rule_min_zoom(flat_rule.rule))
        flat_rule.set_attr("i", self._rule_max_zoom(flat_rule.rule))
        # "s" = symbol layer index for renderer; "f" = renderer index for labeling
        flat_rule.set_attr("s" if rule_type == 0 else "f", 0)

    def _rule_min_zoom(self, rule) -> int:
        return int(ZoomLevels.scale_to_zoom(rule.minimumScale(), "o"))

    def _rule_max_zoom(self, rule) -> int:
        return int(ZoomLevels.scale_to_zoom(rule.maximumScale(), "i"))

    def _rule_visibility(self, rule) -> ZoomInterval:
        """Exact browser interval of an (inherited) rule's scale range.

        Inheritance replaces "unbounded" by the extreme zoom-0/zoom-22 scales;
        an upper bound at the last tile zoom is therefore treated as open, so
        the overzoom policy (not an accidental constant) decides what happens
        beyond the archive's maxzoom.
        """
        interval = fidelity_zoom.interval_from_scales(rule.minimumScale(), rule.maximumScale())
        if interval.max_zoom is not None and interval.max_zoom >= fidelity_zoom.MAX_TILE_ZOOM - 1e-6:
            interval = ZoomInterval(interval.min_zoom, None)
        return interval.intersect(ZoomInterval(float(self.min_zoom), None))

    def _flatten_rule(
        self,
        layer: QgsVectorLayer,
        layer_idx: int,
        rule: Union[QgsRuleBasedLabeling.Rule, QgsRuleBasedRenderer.Rule],
        rule_type: int,
        rule_level: int,
        rule_idx: int,
        inherited_parent=None,
        origin=None,
        ancestors=(),
    ):
        """Recursively flatten the rule hierarchy with property inheritance.

        ``origin``: the original rule of a split ELSE variant; ``ancestors``:
        original parent rules (both only for publication provenance)."""
        # Children of an unprocessed node (the root) inherit from the node itself;
        # children of a processed node inherit from its accumulated (flattened) state.
        parent_for_children = rule
        if rule.parent() or rule_level > 0:  # split ELSE variants are parentless clones
            self._current_provenance = self._provenance(layer, rule, rule_type, origin, ancestors)
            inheritance_source = self._process_rule(
                layer, layer_idx, rule, rule_type, rule_level, rule_idx, inherited_parent
            )
            if inheritance_source is not None:
                parent_for_children = inheritance_source
        child_ancestors = tuple(ancestors) + ((origin or rule,) if rule_level > 0 else ())
        for child_idx, child in enumerate(rule.children()):
            if not child.active():
                continue
            variants = [child]
            if self._is_else_rule(child):
                variants = self._split_else_rule(child, rule, layer)
            for variant in variants:
                self._flatten_rule(
                    layer, layer_idx, variant, rule_type, rule_level + 1, child_idx,
                    parent_for_children, origin=child if variant is not child else None,
                    ancestors=child_ancestors,
                )

    @staticmethod
    def _rule_key(rule) -> str:
        try:
            return rule.ruleKey()
        except AttributeError:
            return ""

    @staticmethod
    def _is_else_rule(rule) -> bool:
        try:
            return bool(rule.isElse())
        except AttributeError:
            return rule.filterExpression() == "ELSE"

    @staticmethod
    def _scale_interval(rule) -> ZoomInterval:
        return fidelity_zoom.interval_from_scales(rule.minimumScale(), rule.maximumScale())

    def _split_else_rule(self, else_rule, parent_rule, layer) -> list:
        """Replace an ELSE rule by explicit, scale-aware exclusion rules.

        QGIS applies an ELSE rule to features that no *active sibling rendered
        at the current scale*. A sibling limited to a scale range therefore
        only excludes features inside that range. The ELSE rule is split at
        every sibling scale breakpoint; each piece excludes exactly the
        siblings visible in its interval. Disabled siblings never exclude.
        """
        siblings = [
            sibling for sibling in parent_rule.children()
            if sibling is not else_rule and sibling.active() and not self._is_else_rule(sibling)
        ]
        if any(sibling.children() or not self._rule_has_payload(sibling) for sibling in siblings):
            self.diagnostics.add(
                "Q2VT_ELSE_NESTED_SIBLINGS",
                f"ELSE rule of layer '{layer.name()}': sibling rules with nested children "
                "are treated as matching whenever their own filter matches.",
                layer_id=layer.id(), rule_id=self._rule_key(else_rule),
            )
        own = self._scale_interval(else_rule)
        breakpoints = {own.min_zoom}
        for sibling in siblings:
            interval = self._scale_interval(sibling)
            for value in (interval.min_zoom, interval.max_zoom):
                if value is not None and own.contains(value):
                    breakpoints.add(value)
        edges = sorted(breakpoints) + [own.max_zoom]
        variants = []
        for low, high in zip(edges[:-1], edges[1:]):
            segment = ZoomInterval(low, high)
            if segment.is_empty:
                continue
            probe = segment.min_zoom + (1e-3 if segment.max_zoom is None
                                        else (segment.max_zoom - segment.min_zoom) / 2)
            filters = [
                sibling.filterExpression() for sibling in siblings
                if self._scale_interval(sibling).contains(probe)
            ]
            variant = else_rule.clone()
            variant.setMinimumScale(fidelity_zoom.zoom_to_scale(segment.min_zoom)
                                    if segment.min_zoom > 0 else else_rule.minimumScale())
            variant.setMaximumScale(fidelity_zoom.zoom_to_scale(segment.max_zoom)
                                    if segment.max_zoom is not None else 0)
            if any(not f for f in filters):
                # A visible sibling without a filter matches every feature.
                variant.setFilterExpression("FALSE")
            elif filters:
                variant.setFilterExpression(
                    f'NOT ({" OR ".join(f"({f})" for f in filters)}) IS 1'
                )
            else:
                variant.setFilterExpression("")
            variants.append(variant)
        return variants

    @staticmethod
    def _is_feature_dependent(expression: str) -> bool:
        parsed = QgsExpression(expression)
        return bool(parsed.referencedColumns()) or parsed.needsGeometry() or \
            bool({"feature", "id", "geometry"} & set(parsed.referencedVariables()))

    def _fold_enabled_property(self, flat_rule: FlattenedRule, symbol_layer):
        """Turn a feature-dependent data-defined *enabled* into a rule filter.

        The legacy export ignored it, drawing the component on every feature.
        Scale-only expressions keep the per-zoom handling.
        """
        props = symbol_layer.dataDefinedProperties()
        prop = props.property(QgsSymbolLayer.Property.PropertyLayerEnabled)
        if not prop or not prop.isActive() or not self._is_feature_dependent(prop.asExpression()):
            return
        flat_rule.rule.setFilterExpression(and_filters(
            flat_rule.rule.filterExpression(), enabled_condition(prop.asExpression())))
        prop.setActive(False)
        props.setProperty(QgsSymbolLayer.Property.PropertyLayerEnabled, prop)

    def _fold_show_property(self, rule):
        """Turn a data-defined label *Show* into part of the rule filter."""
        settings = rule.settings()
        if settings is None:
            return
        props = settings.dataDefinedProperties()
        key = QgsPalLayerSettings.Property.Show
        prop = props.property(key)
        if not prop or not prop.isActive():
            return
        rule.setFilterExpression(and_filters(rule.filterExpression(),
                                             enabled_condition(prop.asExpression())))
        prop.setActive(False)
        props.setProperty(key, prop)
        settings.setDataDefinedProperties(props)

    @staticmethod
    def _rule_has_payload(rule) -> bool:
        if hasattr(rule, "symbol"):
            return rule.symbol() is not None
        return rule.settings() is not None

    def _process_rule(
        self,
        layer: QgsVectorLayer,
        layer_idx: int,
        rule,
        rule_type: int,
        rule_level: int,
        rule_idx: int,
        inherited_parent,
    ):
        """Inherit, validate, split, register; return the cumulative source for descendants."""
        if rule_type == 1:
            self._sync_labeling_scale_range(rule)

        inherited_rule = self._inherit_rule_properties(rule, rule_type, inherited_parent)
        if not inherited_rule:
            return None

        # Snapshot cumulative state (AND-chain filter, intersected scales, stacked
        # symbols) BEFORE applying NOT-children — descendants must not see the
        # output-only exclusion (otherwise C inherits "... AND NOT C" from B*).
        inheritance_source = inherited_rule.clone()
        if rule_type == 1:
            # After the snapshot: a label's Show condition must not restrict
            # the labels of child rules.
            self._fold_show_property(inherited_rule)
        # self._exclude_children_from_filter(inherited_rule, rule)

        flat_rule = FlattenedRule(inherited_rule, layer,
                                  provenance=getattr(self, "_current_provenance", None))
        flat_rule.rule.setDescription("")
        self._set_rule_attributes(flat_rule, layer_idx, rule_type, rule_level, rule_idx)

        if flat_rule.visibility.is_empty:
            self.diagnostics.add(
                "Q2VT_ZOOM_EMPTY_INTERVAL",
                f"Rule '{rule.description() or rule.filterExpression() or rule_idx}' of layer "
                f"'{layer.name()}' is never visible (scale range {rule.minimumScale():g} – "
                f"{rule.maximumScale():g}).",
                layer_id=layer.id(), rule_id=self._rule_key(rule),
            )
            return inheritance_source

        if not self._is_within_zoom_range(flat_rule):
            return inheritance_source

        if rule_type == 0:
            self._draw_seq += 1
        split_rules = self._split_rule(flat_rule, rule_type)
        if rule_type == 1:
            split_rules = [part for rule in split_rules for part in self._split_pinned_labels(rule)]
        self._clip_rules_to_zoom_range(split_rules)
        # Components split by zoom (e.g. dense patterns) may fall outside the export.
        split_rules = [r for r in split_rules if r.get_attr("o") <= r.get_attr("i")]
        for split_rule in split_rules:
            self.flattened_rules.extend(self._split_by_scale_expressions(split_rule))
        return inheritance_source

    def _sync_labeling_scale_range(self, rule):
        """Copy label settings scale visibility to the rule if the rule has no range set."""
        if rule.minimumScale() != 0 or rule.maximumScale() != 0:
            return
        settings = rule.settings()
        if settings and settings.scaleVisibility:
            rule.setMinimumScale(settings.minimumScale)
            rule.setMaximumScale(settings.maximumScale)
            settings.scaleVisibility = False

    def _inherit_rule_properties(self, rule, rule_type: int, inherited_parent):
        """Clone and inherit scale range, filter expression, and symbol layers from cumulative parent."""
        clone = rule.clone()
        self._inherit_min_scale(clone, rule, inherited_parent)
        self._inherit_max_scale(clone, rule, inherited_parent)
        self._inherit_filter_expression(clone, rule, inherited_parent)
        # if rule_type == 0:
        #     self._inherit_symbol_layers(clone, rule, inherited_parent)
        return clone

    def _inherit_min_scale(self, clone, rule, inherited_parent):
        """Inherit minimum scale; 0 means no restriction → use the largest available scale.

        The 0-substitution is applied symmetrically to both the rule's own scale
        and the parent's scale; otherwise an unrestricted ancestor would zero out
        every descendant when min(child, 0) is computed.
        """
        rule_scale = rule.minimumScale() if rule.minimumScale() != 0 else max(ZoomLevels.SCALES)
        parent_min = inherited_parent.minimumScale()
        parent_scale = parent_min if parent_min != 0 else max(ZoomLevels.SCALES)
        clone.setMinimumScale(min(rule_scale, parent_scale))

    def _inherit_max_scale(self, clone, rule, inherited_parent):
        """Inherit maximum scale; 0 means no restriction → use the smallest available scale."""
        rule_scale = rule.maximumScale() if rule.maximumScale() != 0 else min(ZoomLevels.SCALES)
        parent_max = inherited_parent.maximumScale()
        parent_scale = parent_max if parent_max != 0 else min(ZoomLevels.SCALES)
        clone.setMaximumScale(max(rule_scale, parent_scale))

    def _inherit_filter_expression(self, clone, rule, inherited_parent):
        """Combine cumulative ancestor filter with this rule's filter (AND-chain only).

        NOT-children exclusion is NOT applied here — it would poison the snapshot
        used for descendant inheritance. It is applied to the output clone in
        _exclude_children_from_filter after the snapshot is taken.
        """
        parent_filter = inherited_parent.filterExpression() if inherited_parent is not None else ""
        rule_filter = rule.filterExpression()

        if parent_filter and rule_filter:
            combined = f"({parent_filter}) AND ({rule_filter})"
        else:
            combined = parent_filter or rule_filter or ""

        clone.setFilterExpression(combined)

    def _exclude_children_from_filter(self, clone, rule):
        """Exclude this rule's children's filter conditions (output-only — descendants must
        not see this exclusion or grandchildren would inherit a tautologically-false filter)."""
        child_filters = [
            f"({child.filterExpression()})"
            for child in rule.children()
            if child.filterExpression() and child.filterExpression() != "ELSE"
        ]
        if not child_filters:
            return
        children_expr = " OR ".join(child_filters)
        combined = clone.filterExpression()
        final = f"({combined}) AND NOT ({children_expr})" if combined else f"NOT ({children_expr})"
        clone.setFilterExpression(final)

    def _inherit_symbol_layers(self, clone, rule, inherited_parent):
        """Append the cumulative parent's symbol layers to the cloned rule's symbol."""
        clone_symbol = clone.symbol()
        parent_symbol = inherited_parent.symbol() if inherited_parent is not None else None
        if parent_symbol and clone_symbol:
            for i in range(parent_symbol.symbolLayerCount()):
                clone_symbol.appendSymbolLayer(parent_symbol.symbolLayer(i).clone())

    def _split_rule(self, flat_rule: FlattenedRule, rule_type: int) -> List[FlattenedRule]:
        """Split rule by symbol layers (renderer) or matching renderers (labeling)."""
        if rule_type == 0:
            return self._split_by_symbol_layers(flat_rule)
        return self._split_by_matching_renderers(flat_rule)

    def _is_within_zoom_range(self, flat_rule: FlattenedRule) -> bool:
        """Return True if the rule's zoom range overlaps with the requested range."""
        return self._ranges_overlap(
            flat_rule.get_attr("o"), flat_rule.get_attr("i"),
            self.min_zoom, self.max_zoom,
        )

    def _clip_rules_to_zoom_range(self, flat_rules: List[FlattenedRule]):
        """Clamp each rule's zoom range to the global min/max zoom."""
        for flat_rule in flat_rules:
            if flat_rule.get_attr("o") < self.min_zoom:
                flat_rule.set_attr("o", self.min_zoom)
            if flat_rule.get_attr("i") > self.max_zoom:
                flat_rule.set_attr("i", self.max_zoom)

    @classmethod
    def _flatten_generators(cls, symbol):
        """Symbol whose nested geometry generators are composed into single ones.

        A generator whose sub-symbol holds further generators (e.g. an outer
        ``$geometry`` drawn as a line around an inner ``wave($geometry)``)
        becomes one generator per sub-symbol layer: the inner expression with
        its geometry replaced by the outer one. Returns None when nothing is
        nested.
        """
        from qgis.core import QgsGeometryGeneratorSymbolLayer  # pylint: disable=import-outside-toplevel
        nested = any(
            layer.layerType() == "GeometryGenerator" and layer.subSymbol() is not None and any(
                sub.layerType() == "GeometryGenerator" for sub in layer.subSymbol().symbolLayers())
            for layer in symbol.symbolLayers())
        if not nested:
            return None
        result = symbol.clone()
        for index in reversed(range(result.symbolLayerCount())):
            result.deleteSymbolLayer(index)

        def generator(expression, sub_symbol, template):
            layer = QgsGeometryGeneratorSymbolLayer.create({
                "geometryModifier": expression,
                "SymbolType": {0: "Marker", 1: "Line", 2: "Fill"}[int(getattr(
                    sub_symbol.type(), "value", sub_symbol.type()))]})
            layer.setSubSymbol(sub_symbol)
            layer.setUnits(template.units())
            layer.setEnabled(template.enabled())
            layer.setRenderingPass(template.renderingPass())
            return layer

        def expand(layer, expression):
            sub = layer.subSymbol()
            for index in range(sub.symbolLayerCount()):
                inner = sub.symbolLayer(index)
                if inner.layerType() == "GeometryGenerator" and inner.subSymbol() is not None:
                    # The inner generator receives the outer output coerced to
                    # the outer sub-symbol's type (rings for a line symbol...).
                    received = coerce_to_symbol_type(
                        expression, int(getattr(sub.type(), "value", sub.type())))
                    # Inside another symbol QGIS evaluates a generator on the
                    # painter geometry (y down), scaled back to its units
                    # (QgsGeometryGeneratorSymbolLayer::render /
                    # evaluateGeometryInPainterUnits): mirror, evaluate, mirror.
                    mirrored = f"scale({received}, 1, -1, make_point(0, 0))"
                    composed = (f"scale({substitute_geometry(inner.geometryExpression(), mirrored)}"
                                f", 1, -1, make_point(0, 0))")
                    yield from expand(inner, composed)
                else:
                    single = sub.clone()
                    for i in reversed(range(single.symbolLayerCount())):
                        if i != index:
                            single.deleteSymbolLayer(i)
                    yield generator(expression, single, layer)

        for layer in symbol.symbolLayers():
            if layer.layerType() == "GeometryGenerator" and layer.subSymbol() is not None:
                for part in expand(layer, layer.geometryExpression()):
                    result.appendSymbolLayer(part)
            else:
                result.appendSymbolLayer(layer.clone())
        return result

    def _split_by_symbol_layers(self, flat_rule: FlattenedRule) -> List[FlattenedRule]:
        """Split a renderer rule into one rule per enabled symbol layer."""
        symbol = flat_rule.rule.symbol()
        if not symbol:
            return [flat_rule]
        flattened = self._flatten_generators(symbol)
        if flattened is not None:
            del symbol  # deleted by setSymbol: no stale wrapper (see below)
            flat_rule.rule.setSymbol(flattened)
            symbol = flat_rule.rule.symbol()

        layer_count = symbol.symbolLayerCount()
        split_rules = []

        clone_symbol = clone_symbol_layer = rule_clone = None
        for layer_idx in reversed(range(layer_count)):
            # Materializing can replace the previous clone's symbol layer
            # (changeSymbolLayer deletes it): drop those wrappers before the
            # next clone allocates (see the outline note below).
            clone_symbol = clone_symbol_layer = None
            symbol_layer = symbol.symbolLayer(layer_idx)
            if not symbol_layer.enabled():
                continue
            geom_generator = None
            sub_symbol = symbol_layer.subSymbol()
            layer_type = symbol_layer.layerType() if symbol_layer else None
            if layer_type == "GeometryGenerator" and sub_symbol is not None and \
                    int(getattr(sub_symbol.type(), "value", sub_symbol.type())) == 1:
                split_rules.extend(self._generated_line_rules(flat_rule, symbol_layer, layer_idx))
                continue
            if layer_type == "VectorField" and sub_symbol is not None:
                split_rules.extend(self._vector_field_rules(flat_rule, symbol_layer, layer_idx))
                continue
            if layer_type == "GeometryGenerator":
                symbol_type = sub_symbol.type()
                geom_generator = True
            elif layer_type == "CentroidFill":
                symbol_type = 0
            else:
                symbol_type = symbol_layer.type()

            rule_clone = flat_rule.derive()
            rule_clone.set_attr("c", symbol_type)
            rule_clone.set_attr("s", layer_idx, geom_generator)

            clone_symbol = rule_clone.rule.symbol()
            for remove_idx in reversed(range(layer_count)):
                if remove_idx != layer_idx:
                    clone_symbol.deleteSymbolLayer(remove_idx)


            clone_symbol_layer = clone_symbol.symbolLayers()[0]
            self._fold_enabled_property(rule_clone, clone_symbol_layer)
            draw_pass = clone_symbol_layer.renderingPass() if self._honor_passes else 0
            # Bottom first: lower layer tree position, pass, rule, symbol layer, part.
            order = (-flat_rule.get_attr("l"), draw_pass, self._draw_seq, layer_idx)
            rule_clone.order = order + (0,)
            materialized = self.materializer.materialize(rule_clone, clone_symbol_layer)
            if materialized is not None:
                for part, component in enumerate(materialized):
                    component.order = order + (part,)
                split_rules.extend(materialized)
                continue
            if rule_clone and layer_type == "SimpleFill":
                if clone_symbol_layer.strokeStyle() != Qt.PenStyle.NoPen:
                    outline_rule = rule_clone.derive()
                    outline_rule.set_attr("c", 1)
                    # No Python reference to the fill symbol may outlive
                    # setSymbol() below, which deletes it: SIP would hand
                    # that stale QgsFillSymbol wrapper back for the next
                    # object QGIS allocates at the same address (seen on
                    # Windows: "'QgsFillSymbol' object has no attribute
                    # 'sizeUnit'" for a point pattern's marker).
                    outline_symbol = self._convert_fill_outline_to_line_symbol(
                        outline_rule.rule.symbol())
                    if outline_symbol:
                        outline_rule.order = order + (1,)  # stroke above its fill
                        outline_rule.rule.setSymbol(outline_symbol)
                        split_rules.append(outline_rule)
                        clone_symbol_layer.setStrokeStyle(Qt.PenStyle.NoPen)
                if clone_symbol_layer.brushStyle() == Qt.BrushStyle.NoBrush:
                    continue
            if rule_clone:
                split_rules.append(rule_clone)

        return split_rules

    def _vector_field_rules(self, flat_rule: FlattenedRule, field_layer, layer_idx: int):
        """A vector field marker: QGIS draws its line sub-symbol from each
        point to the point moved by the vector (x, y attributes; or length and
        angle; or a height) times the scale, in map units (project CRS) or on
        screen. Exported as a line geometry generator; screen distances get one
        rule per zoom, converted at the middle of the zoom."""
        from qgis.core import QgsGeometryGeneratorSymbolLayer, QgsVectorFieldSymbolLayer  # pylint: disable=import-outside-toplevel
        from .fidelity.units import normalize_unit  # pylint: disable=import-outside-toplevel
        from .materializer import _to_mm  # pylint: disable=import-outside-toplevel
        x_name, y_name = field_layer.xAttribute(), field_layer.yAttribute()
        x = f"to_real({QgsExpression.quotedColumnRef(x_name)})" if x_name else "0"
        y = f"to_real({QgsExpression.quotedColumnRef(y_name)})" if y_name else "0"
        kind = field_layer.vectorFieldType()
        if kind == QgsVectorFieldSymbolLayer.Polar:
            angle = y if field_layer.angleUnits() == QgsVectorFieldSymbolLayer.Radians else f"radians({y})"
            if field_layer.angleOrientation() == QgsVectorFieldSymbolLayer.ClockwiseFromNorth:
                dx, dy = f"{x} * sin({angle})", f"{x} * cos({angle})"
            else:
                dx, dy = f"{x} * cos({angle})", f"{x} * sin({angle})"
        elif kind == QgsVectorFieldSymbolLayer.Height:
            dx, dy = "0", y
        else:
            dx, dy = x, y
        if abs(field_layer.offset().x()) > 1e-9 or abs(field_layer.offset().y()) > 1e-9:
            self.diagnostics.add("Q2VT_SYMBOL_APPROXIMATE", "Vector field offset is not applied.",
                                 layer_id=flat_rule.layer.id(), rule_id=flat_rule.rule_id)
        project_crs = QgsProject.instance().crs().authid() or flat_rule.layer.crs().authid()
        layer_crs = flat_rule.layer.crs().authid()

        def generator(factor):
            point = "$geometry" if project_crs == layer_crs else \
                f"transform($geometry, '{layer_crs}', '{project_crs}')"
            line = (f"make_line({point}, translate({point}, ({dx}) * {factor!r}, "
                    f"({dy}) * {factor!r}))")
            if project_crs != layer_crs:
                line = f"transform({line}, '{project_crs}', '{layer_crs}')"
            gen = QgsGeometryGeneratorSymbolLayer.create({"geometryModifier": line, "SymbolType": "Line"})
            gen.setSubSymbol(field_layer.subSymbol().clone())
            gen.setRenderingPass(field_layer.renderingPass())
            return gen

        unit = field_layer.distanceUnit()
        scale = float(field_layer.scale())
        if normalize_unit(unit) == "map" or _to_mm(1.0, unit) is None:
            return self._generated_line_rules(flat_rule, generator(scale), layer_idx)
        rules = []
        for rule in self.materializer._per_zoom(flat_rule):  # pylint: disable=protected-access
            zoom = rule.get_attr("o")
            factor = scale * _to_mm(1.0, unit) / 1000.0 * ZoomLevels.zoom_to_scale(zoom) / math.sqrt(2)
            rules.extend(self._generated_line_rules(rule, generator(factor), layer_idx))
        return rules

    def _generated_line_rules(self, flat_rule: FlattenedRule, generator, layer_idx: int):
        """A geometry generator drawn with a line symbol, as line rules on the
        generated geometry: one per sub-symbol layer, exported like the
        layers of a line layer (exact marker positions, hash lines, offsets
        ...). ``pre_generator`` makes the exporter generate the lines first
        and evaluate geometry-dependent properties on every generated part,
        as QGIS does (``length(geometry_n($geometry, @geometry_part_num))``
        is one segment's length)."""
        sub_symbol = generator.subSymbol()
        rules = []
        clone_layer = None
        for inner_idx in reversed(range(sub_symbol.symbolLayerCount())):
            clone_layer = None  # may have been replaced (deleted) by materialize()
            inner = sub_symbol.symbolLayer(inner_idx)
            if not inner.enabled():
                continue
            rule_clone = flat_rule.derive()
            single = sub_symbol.clone()
            for remove_idx in reversed(range(single.symbolLayerCount())):
                if remove_idx != inner_idx:
                    single.deleteSymbolLayer(remove_idx)
            single.setOpacity(single.opacity() * flat_rule.rule.symbol().opacity())
            rule_clone.rule.setSymbol(single)
            rule_clone.set_attr("g", 1)
            rule_clone.set_attr("c", 1)
            # A distinct dataset per sub-symbol layer (their recipes differ).
            rule_clone.set_attr("s", layer_idx * 100 + inner_idx)
            rule_clone.pre_generator = generator.geometryExpression()
            clone_layer = single.symbolLayers()[0]
            self._fold_enabled_property(rule_clone, clone_layer)
            draw_pass = generator.renderingPass() if self._honor_passes else 0
            order = (-flat_rule.get_attr("l"), draw_pass, self._draw_seq, layer_idx, inner_idx)
            rule_clone.order = order + (0,)
            materialized = self.materializer.materialize(rule_clone, clone_layer)
            if materialized is not None:
                for part, component in enumerate(materialized):
                    component.order = order + (part,)
                rules.extend(materialized)
            else:
                rules.append(rule_clone)
        return rules

    @staticmethod
    def _convert_fill_outline_to_line_symbol(fill_symbol: QgsFillSymbol) -> QgsFillSymbol | None:
        """ Convert a QgsFillSymbol containing a single QgsSimpleFillSymbolLayer into 
        a cloned QgsFillSymbol containing a single QgsSimpleLineSymbolLayer representing
        the original outline.
        """
        if not isinstance(fill_symbol, QgsFillSymbol):
            return None

        layers = fill_symbol.symbolLayers()
        if len(layers) != 1:
            return None

        fill_layer = layers[0]
        if not isinstance(fill_layer, QgsSimpleFillSymbolLayer):
            return None

        new_symbol = fill_symbol.clone()
        line_layer = QgsSimpleLineSymbolLayer()

        line_layer.setColor(fill_layer.strokeColor())
        line_layer.setWidth(fill_layer.strokeWidth())
        line_layer.setWidthUnit(fill_layer.strokeWidthUnit())
        line_layer.setPenStyle(fill_layer.strokeStyle())
        line_layer.setPenJoinStyle(fill_layer.penJoinStyle())

        property_keys = [
            QgsSymbolLayer.Property.PropertyStrokeColor,
            QgsSymbolLayer.Property.PropertyStrokeWidth,
            QgsSymbolLayer.Property.PropertyStrokeStyle,
            QgsSymbolLayer.Property.PropertyJoinStyle
        ]

        for key in property_keys:
            prop = fill_layer.dataDefinedProperties().property(key)
            if prop is not None and prop.isActive():
                line_layer.setDataDefinedProperty(key, QgsProperty(prop))

        new_symbol.changeSymbolLayer(0, line_layer)

        return new_symbol


    # Leaders are drawn with the labels, above every layer's features.
    _CALLOUT_ORDER = (1 << 30, 0, 0, 0, 0)

    def _split_pinned_labels(self, label_rule: FlattenedRule) -> List[FlattenedRule]:
        """Separate labels with a data-defined position ("pinned" labels).

        QGIS places a label whose X and Y are both data-defined exactly at that
        point (layer CRS); other features of the rule are placed normally. The
        pinned features become their own point dataset, and a straight callout
        leader (if callouts are enabled) becomes a line dataset drawn with the
        callout's line symbol.
        """
        settings = label_rule.rule.settings()
        if settings is None:
            return [label_rule]
        props = settings.dataDefinedProperties()
        P = QgsPalLayerSettings.Property
        x_prop, y_prop = props.property(P.PositionX), props.property(P.PositionY)
        if not (x_prop and y_prop and x_prop.isActive() and y_prop.isActive()):
            return [label_rule]
        x, y = x_prop.asExpression(), y_prop.asExpression()
        condition = f"({x}) IS NOT NULL AND ({y}) IS NOT NULL"
        base_filter = label_rule.rule.filterExpression()

        free = label_rule.derive()
        free.rule.setFilterExpression(and_filters(base_filter, f"NOT ({condition})"))
        free_settings = free.rule.settings()
        free_props = free_settings.dataDefinedProperties()
        for key in (P.PositionX, P.PositionY):
            prop = free_props.property(key)
            prop.setActive(False)
            free_props.setProperty(key, prop)
        free_settings.setDataDefinedProperties(free_props)

        pinned = label_rule.derive()
        pinned.rule.setFilterExpression(and_filters(base_filter, condition))
        pinned.set_attr("p", 1)
        pinned.set_attr("c", 0)
        parts = [free, pinned]

        callout = settings.callout()
        if callout is not None and callout.enabled():
            leader = self._callout_leader(pinned, callout, x, y)
            if leader is not None:
                parts.append(leader)
        return parts

    def _callout_leader(self, pinned: FlattenedRule, callout, x: str, y: str):
        from .fidelity.materialize import Recipe  # pylint: disable=import-outside-toplevel
        symbol = callout.lineSymbol() if hasattr(callout, "lineSymbol") else None
        if symbol is None:
            self.diagnostics.add(
                "Q2VT_CALLOUT_APPROX", f"Callout type '{callout.type()}' is not exported.",
                layer_id=pinned.layer.id())
            return None
        if callout.type() != "simple":
            self.diagnostics.add(
                "Q2VT_CALLOUT_APPROX",
                f"'{callout.type()}' callouts are drawn as straight leaders.",
                layer_id=pinned.layer.id())
        self.diagnostics.add(
            "Q2VT_CALLOUT_APPROX",
            "Callout leaders end at the label's anchor point (QGIS: nearest point of "
            "the label box); pinned labels are always shown so leaders never dangle.",
            layer_id=pinned.layer.id())
        rule = QgsRuleBasedRenderer.Rule(symbol.clone())
        rule.setFilterExpression(pinned.rule.filterExpression())
        rule.setMinimumScale(pinned.rule.minimumScale())
        rule.setMaximumScale(pinned.rule.maximumScale())
        rule.setDescription(pinned.rule.description())
        try:
            anchor = int(getattr(callout.anchorPoint(), "value", callout.anchorPoint()))
        except (AttributeError, TypeError, ValueError):
            anchor = 0
        recipe = Recipe("callout", params=(("x", x), ("y", y), ("anchor", anchor),
                                           ("crs", pinned.layer.crs().authid())))
        leader = FlattenedRule(rule, pinned.layer, "", pinned.visibility, recipe,
                               self._CALLOUT_ORDER, provenance=self._leader_provenance(pinned))
        leader.set_attr("t", 0)
        leader.set_attr("c", 1)
        leader.set_attr("s", 0)
        return leader

    def _split_by_matching_renderers(self, label_rule: FlattenedRule) -> List[FlattenedRule]:
        """Split a label rule by matching renderer rules with overlapping scale ranges."""
        split_rules = []
        seen_datasets: set = set()
        renderer_idx = 0

        for renderer_rule in self.flattened_rules:
            if label_rule.layer.id() != renderer_rule.layer.id():
                continue
            if renderer_rule.get_attr("t") == 1:
                continue
            if renderer_rule.recipe is not None and renderer_rule.recipe.kind == "callout":
                continue
            filter_id = f'{renderer_rule.get_attr("r")}{renderer_rule.get_attr("d")}'
            if filter_id in seen_datasets:
                continue
            matched = self._match_label_to_renderer(label_rule, renderer_rule, renderer_idx)
            if matched:
                split_rules.append(matched)
                seen_datasets.add(filter_id)
            renderer_idx += 1

        return split_rules if split_rules else [label_rule]

    def _match_label_to_renderer(
        self,
        label_rule: FlattenedRule,
        renderer_rule: FlattenedRule,
        renderer_idx: int,
    ) -> Optional[FlattenedRule]:
        """Return a combined label/renderer rule if their zoom ranges overlap."""
        label_min, label_max = label_rule.get_attr("o"), label_rule.get_attr("i")
        renderer_min, renderer_max = renderer_rule.get_attr("o"), renderer_rule.get_attr("i")

        if not self._ranges_overlap(label_min, label_max, renderer_min, renderer_max):
            return None

        rule_clone = label_rule.derive()
        label_filter = rule_clone.rule.filterExpression()
        renderer_filter = renderer_rule.rule.filterExpression()

        if label_filter and renderer_filter:
            combined = f"({renderer_filter}) AND ({label_filter})"
        else:
            combined = renderer_filter or label_filter or ""
        rule_clone.rule.setFilterExpression(combined)

        if label_min < renderer_min:
            rule_clone.set_attr("o", renderer_min)
        if label_max > renderer_max:
            rule_clone.set_attr("i", renderer_max)
        if label_rule.visibility is not None and renderer_rule.visibility is not None:
            rule_clone.visibility = label_rule.visibility.intersect(renderer_rule.visibility)
            if rule_clone.visibility.is_empty:
                return None

        rule_clone.set_attr("f", renderer_idx)
        return rule_clone

    @staticmethod
    def _ranges_overlap(r1_start: int, r1_end: int, r2_start: int, r2_end: int) -> bool:
        a_min, a_max = sorted((r1_start, r1_end))
        b_min, b_max = sorted((r2_start, r2_end))
        return a_min <= b_max and b_min <= a_max

    def _split_by_scale_expressions(self, flat_rule: FlattenedRule) -> List[FlattenedRule]:
        """Split a rule per zoom level when it contains @map_scale-dependent expressions."""
        if not self._has_scale_dependencies(flat_rule):
            return [flat_rule]

        # One clone per tile zoom the rule occupies (the legacy loop also
        # produced a clone one zoom beyond the rule's own range).
        min_zoom = flat_rule.get_attr("o")
        max_zoom = min(self.max_zoom, flat_rule.get_attr("i"))
        split_rules = []
        for zoom in range(min_zoom, max_zoom + 1):
            clone = self._create_zoom_specific_rule(flat_rule, zoom, last=zoom == max_zoom)
            if flat_rule.get_attr("t") == 1 or self._symbol_layer_visible_at_zoom(clone):
                split_rules.append(clone)
        return split_rules

    def _symbol_layer_visible_at_zoom(self, flat_rule: FlattenedRule) -> bool:
        """Return False if the symbol layer's visibility DDP evaluates to falsy at the zoom."""
        symbol = flat_rule.rule.symbol()
        if not symbol:
            return True
        symbol_layer = symbol.symbolLayers()[0]
        vis_prop = symbol_layer.dataDefinedProperties().property(
            QgsSymbolLayer.Property.PropertyLayerEnabled
        )
        if vis_prop and vis_prop.isActive():
            min_scale = ZoomLevels.zoom_to_scale(flat_rule.get_attr("o"))
            if self._is_feature_dependent(vis_prop.expressionString()):
                return True  # feature dependent: folded into the rule filter
            expression = QgsExpression(with_map_scale(vis_prop.expressionString(), min_scale))
            evaluation = expression.evaluate()
            if not expression.hasEvalError() and evaluation is not None and not evaluation:
                return False
        return True

    def _has_scale_dependencies(self, flat_rule: FlattenedRule) -> bool:
        """Return True if the rule's serialized XML contains @map_scale."""
        doc = QDomDocument("style")
        context = QgsReadWriteContext()
        rule_clone = flat_rule.rule.clone()

        if flat_rule.get_attr("t") == 1:
            root = QgsRuleBasedLabeling.Rule(None)
            root.appendChild(rule_clone)
            elem = QgsRuleBasedLabeling(root).save(doc, context)
        else:
            root = QgsRuleBasedRenderer.Rule(None)
            root.appendChild(rule_clone)
            elem = QgsRuleBasedRenderer(root).save(doc, context)

        doc.appendChild(elem)
        return "@map_scale" in doc.toString()

    def _create_zoom_specific_rule(self, flat_rule: FlattenedRule, zoom: int,
                                   last: bool = False) -> FlattenedRule:
        """Clone a rule with @map_scale replaced by the exact scale for the given zoom."""
        rule_clone = flat_rule.derive()
        scale = ZoomLevels.zoom_to_scale(zoom)

        filter_exp = flat_rule.rule.filterExpression()
        if "@map_scale" in filter_exp:
            rule_clone.rule.setFilterExpression(with_map_scale(filter_exp, scale))

        if flat_rule.get_attr("t") == 1 and flat_rule.rule.settings():
            settings = flat_rule.rule.settings()
            label_exp = settings.getLabelExpression().expression()
            if label_exp and "@map_scale" in label_exp:
                clone_settings = rule_clone.rule.settings()
                clone_settings.fieldName = with_map_scale(label_exp, scale)
                clone_settings.isExpression = True

        rule_clone.set_attr("o", zoom)
        rule_clone.set_attr("i", zoom)
        # The last zoom keeps the rule's own upper limit: closed at zoom + 1,
        # Övezethatár disappeared beyond the export's maximum zoom + 1
        # instead of following the overzoom setting.
        upper = None if last else float(zoom + 1)
        base = flat_rule.visibility if flat_rule.visibility is not None else \
            ZoomInterval(float(zoom), None if last else float(zoom + 1))
        rule_clone.visibility = base.intersect(ZoomInterval(float(zoom), upper))
        return rule_clone