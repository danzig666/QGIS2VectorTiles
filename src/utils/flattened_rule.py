"""
flattened_rule.py

FlattenedRule — shared data-transfer object (dataclass) that carries a
flattened QGIS rule together with its source layer and export dataset path.
Imported by RulesFlattener, RulesExporter, and TilesStyler.
"""

from dataclasses import dataclass
import re
from typing import Optional, Union

from qgis.core import QgsRuleBasedRenderer, QgsRuleBasedLabeling, QgsVectorLayer

from ..core.fidelity.materialize import Recipe
from ..core.fidelity.model import ZoomInterval


@dataclass
class FlattenedRule:
    """A flattened rule with inherited properties from parent hierarchy."""

    rule: Union[QgsRuleBasedLabeling.Rule, QgsRuleBasedRenderer.Rule]
    layer: QgsVectorLayer
    output_dataset: Optional[str] = ""
    # Exact browser visibility interval [min_zoom, max_zoom). The integer
    # "o"/"i" attributes only describe which tile zooms carry the data.
    visibility: Optional[ZoomInterval] = None
    # Geometry recipe for materialized components (exact marker positions,
    # map-unit hatches, ...); None exports the source geometry.
    recipe: Optional[Recipe] = None
    # Draw order key assigned by the flattener: (-layer, pass, feature-order
    # stratum, rule, symbol layer, ...); see TilesStyler.draw_order.
    order: tuple = ()
    # Geometry generator (line output) applied to the source features before
    # anything else: the rule is a sub-symbol layer drawn on generated lines.
    pre_generator: Optional[str] = None
    # Original QGIS layer / legend rule this component comes from
    # (publishing.provenance.RuleProvenance); captured before cloning and
    # conversion, copied by derive() and every other construction.
    provenance: Optional[object] = None
    # Renderer that draws the features of a symbol together: "merge" (merged
    # feature renderer: their union) or "invert" (inverted polygon renderer:
    # everything outside them); "" draws each feature.
    merge: str = ""
    # Heatmap renderer: the MapLibre heatmap spec (fidelity.heatmap.build_spec)
    # drawn instead of the rule's (placeholder) marker.
    heatmap: Optional[dict] = None
    # Point cluster / displacement renderer at one zoom: (mode, role,
    # params...) applied to the whole layer before anything else (see
    # RulesExporter._point_groups).
    point_group: Optional[tuple] = None
    # Shift on screen (x, y, QGIS unit name[, QgsMapUnitScale]), y down:
    # drawn with a viewport translate (a fill layer's offset, e.g. a drop
    # shadow; map units grow with the map).
    translate: Optional[tuple] = None
    # Paint effects drawn for this component: "outer" only its outer effects
    # (glow, drop shadow; the line itself is another component), "none" no
    # effects; None as the symbol says.
    effect_role: Optional[str] = None
    # Inner effects (inner shadow / glow) as strips across the line, coloured
    # by the run's screen direction (SymbolMaterializer._inner_effect_spec).
    inner_effect: Optional[dict] = None
    # "source": symbols drawn in data order (overlapping pattern markers
    # stacked as QGIS draws them), not sorted by screen position.
    z_order: Optional[str] = None
    # MapLibre filter of a feature-order stratum (fidelity.feature_order):
    # only the features lifted into it, or (stratum 0) all the others; None
    # draws every feature.
    feature_filter: Optional[list] = None
    # Zoom whose map scale the scale-dependent expressions of the rule
    # (@map_scale, @zoom_level) are evaluated at; None: its first tile zoom
    # ("o"). Set where they differ: a piece of a rule split at a scale
    # threshold, or a zoom beyond the archive's last one, drawn from the
    # last zoom's tiles.
    scale_zoom: Optional[float] = None
    # The rule filter before @map_scale was bound for this piece (labels
    # are matched to the features of the renderer rule, then bound at their
    # own zoom); None: the filter itself.
    unbound_filter: Optional[str] = None
    # Renderer rule filter before a symbol layer's data-defined *enabled*
    # was folded into it: the features QGIS labels (it labels every feature
    # the rule renders, whichever symbol layers are enabled). None: the
    # rule's own filter.
    label_filter: Optional[str] = None
    # (dx, dy) in project map units (y up) added to the exported geometry: a
    # simple fill's offset in map units, exact at every zoom (a translate
    # would show tile edges where it exceeds the tile buffer).
    shift: Optional[tuple] = None
    # A repeated line label placed by the web viewer on the visible part of
    # its line (RulesFlattener._split_line_label_windows), one label per
    # repeat part, as QGIS chops the line clipped to the map extent.
    visible_repeat: bool = False
    # The layer's diagrams (fidelity.diagrams.diagram_spec): a placeholder
    # marker carries their values into the tiles; the style draws them.
    diagram: Optional[dict] = None
    # A screen-unit dashed line of a symbol that also places screen-spaced
    # interval markers (letters or ticks in the dash gaps): its dashes are
    # drawn per band of a zoom like the markers (materializer
    # _screen_dash_bands), so they stay in step.
    band_dashes: bool = False
    # The outline of a simple fill (RulesFlattener._simple_fill_rules): a
    # line rule on the polygon's rings.
    fill_outline: bool = False
    # Drawn from another rule's dataset (output_dataset is the fill's: the
    # same polygons, once in the tiles); that rule's geometry is exported.
    shares_dataset: bool = False

    def derive(self, rule=None) -> "FlattenedRule":
        """Copy of this flat rule (optionally with another rule object)."""
        return FlattenedRule(
            rule if rule is not None else self.rule.clone(),
            self.layer, self.output_dataset, self.visibility, self.recipe, self.order,
            self.pre_generator, self.provenance, self.merge, self.heatmap,
            self.point_group, self.translate, self.effect_role, self.inner_effect,
            self.z_order, self.feature_filter, self.scale_zoom, self.unbound_filter,
            self.label_filter, self.shift, self.visible_repeat, self.diagram,
            self.band_dashes, self.fill_outline, self.shares_dataset,
        )

    def evaluation_zoom(self) -> float:
        """The zoom whose map scale this rule's scale-dependent values use."""
        if self.scale_zoom is not None:
            return float(self.scale_zoom)
        view = self.get_attr("v")  # a rule made for a zoom past the last tiles
        return float(view if view is not None else self.get_attr("o"))

    def renderer_filter(self) -> str:
        """The rule's filter, @map_scale unbound (see ``unbound_filter``)."""
        if self.unbound_filter is not None:
            return self.unbound_filter
        return self.rule.filterExpression()

    @property
    def rule_id(self) -> str:
        """Stable identifier: layer id + rule key (for diagnostics)."""
        try:
            return self.rule.ruleKey()
        except AttributeError:
            return ""

    def get_attr(self, char: str) -> Optional[int]:
        """Extract rule attribute from description by character prefix."""
        match = re.search(f"{char}(\\d+)", self.rule.description())
        return int(match.group(1)) if match else None

    def set_attr(self, char: str, value: int):
        """Set rule attribute in description (two digits, more when needed)."""
        new_attr = f"{char}{int(value):02d}"
        desc = self.rule.description()
        desc, replaced = re.subn(f"{char}\\d+", new_attr, desc, count=1)
        if not replaced:
            desc = f"{desc}{new_attr}"

        self.rule.setDescription(desc)
        self.output_dataset = desc

    def get_description(self):
        """Construct rule description for labeling or renderer rule."""
        geom_desc = {0:'point', 1:'line', 2:'polygon', 3:'unknown'}
        lyr_name = f'layer: {self.layer.name() or self.layer.id()}'
        rule_type = f'type: {"symbology" if self.get_attr("t") == 0 else "labeling"}'
        rule_index = f'index: level {self.get_attr("d")} number {self.get_attr("r") + 1}'
        source_geom = geom_desc.get(self.get_attr("g"))
        target_geom = geom_desc.get(self.get_attr("c"))
        if source_geom == target_geom:
            geom_desc_str = 'conversion: none'
        else:
            geom_desc_str = f'conversion: {source_geom} to {target_geom}'
        # A QGIS string literal: a quote or backslash in a layer name
        # ("Regioni d'Italia") is escaped, else the expression did not parse
        # and the rule's export failed.
        from qgis.core import QgsExpression  # pylint: disable=import-outside-toplevel
        return QgsExpression.quotedString(
            "{" + f'{lyr_name},{rule_type},{rule_index},{geom_desc_str}' + "}")
