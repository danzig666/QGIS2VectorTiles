"""
rules_flattener.py

RulesFlattener — walks every visible vector layer in the current QGIS project,
converts any non-rule-based renderer / labeling system to rule-based, then
recursively flattens the rule hierarchy with full property inheritance
(scale range, filter expression, symbol layers).

Depends on: config, zoom_levels, flattened_rule
"""

from typing import List, Optional, Union

from qgis.core import (
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
from .fidelity.qgis_expr import and_filters, enabled_condition, with_map_scale
from .materializer import SymbolMaterializer



class RulesFlattener:
    """Flatten QGIS rule-based styling with full property inheritance."""

    RULE_TYPES = {0: "renderer", 1: "labeling"}

    def __init__(self, min_zoom: int, max_zoom: int, utils_dir, feedback,
                 diagnostics: Optional[DiagnosticCollector] = None):
        self.min_zoom = min_zoom
        self.max_zoom = max_zoom
        self.utils_dir = utils_dir
        self.layer_tree_root = QgsProject.instance().layerTreeRoot()
        self.flattened_rules: List[FlattenedRule] = []
        self.feedback = feedback
        self.diagnostics = diagnostics or DiagnosticCollector()
        # Converted/cloned rule systems are kept alive here instead of being
        # assigned to the project layers: exporting must never modify the
        # user's project (renderer, labeling, ELSE rules, scale visibility).
        self._rule_systems: list = []
        self.materializer = SymbolMaterializer(self.diagnostics)
        # Tree-unique counter; reset per (layer, rule_type) pass. Used only to
        # disambiguate output_dataset when sibling subtrees share (l,t,d,r,...).
        self._unique_counter = 0

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
        node = self.layer_tree_root.findLayer(layer.id())
        is_visible = node.isVisible() if node is not None else False
        return is_vector and is_visible

    def _process_layer_rules(self, layer: QgsVectorLayer, layer_idx: int):
        """Process both renderer and labeling rules for a single layer."""
        for rule_type, type_name in self.RULE_TYPES.items():
            rule_system = self._get_or_convert_rule_system(layer, rule_type)
            if not rule_system:
                continue
            self._rule_systems.append(rule_system)
            root_rule = self._prepare_root_rule(rule_system, layer)
            if root_rule:
                # Reset per (layer, rule_type) pass; values must stay < 100
                # because FlattenedRule.set_attr formats as 2 digits.
                self._unique_counter = 0
                self._flatten_rule(layer, layer_idx, root_rule, rule_type, 0, 0)

    def _get_or_convert_rule_system(self, layer: QgsVectorLayer, rule_type: int):
        """Return the layer's rule system, converting from single/graduated/categorized if needed."""
        if rule_type == 0:
            return self._convert_renderer_to_rules(layer)
        return self._convert_labeling_to_rules(layer)

    def _convert_renderer_to_rules(self, layer: QgsVectorLayer):
        """Convert any renderer to a QgsRuleBasedRenderer, preserving active items only."""
        system = layer.renderer()
        if not system:
            return None

        system = system.clone()
        if isinstance(system, QgsRuleBasedRenderer):
            return system

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
        if layer.hasScaleBasedVisibility():
            root_rule.setMinimumScale(layer.minimumScale())
            root_rule.setMaximumScale(layer.maximumScale())
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
    ):
        """Recursively flatten the rule hierarchy with property inheritance."""
        # Children of an unprocessed node (the root) inherit from the node itself;
        # children of a processed node inherit from its accumulated (flattened) state.
        parent_for_children = rule
        if rule.parent() or rule_level > 0:  # split ELSE variants are parentless clones
            inheritance_source = self._process_rule(
                layer, layer_idx, rule, rule_type, rule_level, rule_idx, inherited_parent
            )
            if inheritance_source is not None:
                parent_for_children = inheritance_source
        for child_idx, child in enumerate(rule.children()):
            if not child.active():
                continue
            variants = [child]
            if self._is_else_rule(child):
                variants = self._split_else_rule(child, rule, layer)
            for variant in variants:
                self._flatten_rule(
                    layer, layer_idx, variant, rule_type, rule_level + 1, child_idx,
                    parent_for_children,
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

        flat_rule = FlattenedRule(inherited_rule, layer)
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

        split_rules = self._split_rule(flat_rule, rule_type)
        self._clip_rules_to_zoom_range(split_rules)
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

    def _split_by_symbol_layers(self, flat_rule: FlattenedRule) -> List[FlattenedRule]:
        """Split a renderer rule into one rule per enabled symbol layer."""
        symbol = flat_rule.rule.symbol()
        if not symbol:
            return [flat_rule]

        layer_count = symbol.symbolLayerCount()
        split_rules = []

        for layer_idx in reversed(range(layer_count)):
            symbol_layer = symbol.symbolLayer(layer_idx)
            if not symbol_layer.enabled():
                continue
            geom_generator = None
            sub_symbol = symbol_layer.subSymbol()
            layer_type = symbol_layer.layerType() if symbol_layer else None
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
            materialized = self.materializer.materialize(rule_clone, clone_symbol_layer)
            if materialized is not None:
                split_rules.extend(materialized)
                continue
            if rule_clone and layer_type == "SimpleFill":
                if clone_symbol_layer.strokeStyle() != Qt.PenStyle.NoPen:
                    outline_rule = rule_clone.derive()
                    outline_rule.set_attr("c", 1)
                    fill_symbol = outline_rule.rule.symbol()
                    outline_symbol = self._convert_fill_outline_to_line_symbol(fill_symbol)
                    if outline_symbol:
                        outline_rule.rule.setSymbol(outline_symbol)
                        split_rules.append(outline_rule)
                        clone_symbol_layer.setStrokeStyle(Qt.PenStyle.NoPen)
                if clone_symbol_layer.brushStyle() == Qt.BrushStyle.NoBrush:
                    continue
            if rule_clone:
                split_rules.append(rule_clone)

        return split_rules

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
            clone = self._create_zoom_specific_rule(flat_rule, zoom)
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

    def _create_zoom_specific_rule(self, flat_rule: FlattenedRule, zoom: int) -> FlattenedRule:
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
        if flat_rule.visibility is not None:
            rule_clone.visibility = flat_rule.visibility.intersect(
                ZoomInterval(float(zoom), float(zoom + 1))
            )
        return rule_clone