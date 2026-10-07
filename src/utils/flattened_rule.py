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
    # Draw order key assigned by the flattener (see fidelity.render_order).
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
    # Shift on screen (x, y, QGIS unit name), y down: drawn with a viewport
    # translate (an arrow fill layer's offset, e.g. a drop shadow).
    translate: Optional[tuple] = None
    # Paint effects drawn for this component: "outer" only its outer effects
    # (glow, drop shadow; the line itself is another component), "none" no
    # effects; None as the symbol says.
    effect_role: Optional[str] = None
    # Inner effects (inner shadow / glow) as strips across the line, coloured
    # by the run's screen direction (SymbolMaterializer._inner_effect_spec).
    inner_effect: Optional[dict] = None

    def derive(self, rule=None) -> "FlattenedRule":
        """Copy of this flat rule (optionally with another rule object)."""
        return FlattenedRule(
            rule if rule is not None else self.rule.clone(),
            self.layer, self.output_dataset, self.visibility, self.recipe, self.order,
            self.pre_generator, self.provenance, self.merge, self.heatmap,
            self.point_group, self.translate, self.effect_role, self.inner_effect,
        )

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

    def set_attr(self, char: str, value: int, geom_generator=None):
        """Set rule attribute in description (two digits, more when needed)."""
        new_attr = f"{char}{int(value):02d}"
        desc = self.rule.description()
        desc, replaced = re.subn(f"{char}\\d+", new_attr, desc, count=1)
        if not replaced:
            desc = f"{desc}{new_attr}"

        self.rule.setDescription(desc)
        self.output_dataset = desc
        if geom_generator:
            self.output_dataset = re.sub(r"s\d+", "s00", desc, count=1)

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
        return  "'{" + f'{lyr_name},{rule_type},{rule_index},{geom_desc_str}' + "}'"