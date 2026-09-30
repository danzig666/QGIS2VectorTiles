"""
flattened_rule.py

FlattenedRule — shared data-transfer object (dataclass) that carries a
flattened QGIS rule together with its source layer and export dataset path.
Imported by RulesFlattener, RulesExporter, and TilesStyler.
"""

from dataclasses import dataclass
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

    def derive(self, rule=None) -> "FlattenedRule":
        """Copy of this flat rule (optionally with another rule object)."""
        return FlattenedRule(
            rule if rule is not None else self.rule.clone(),
            self.layer, self.output_dataset, self.visibility, self.recipe, self.order,
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
        desc = self.rule.description()
        start = desc.find(char) + 1
        if start == 0:
            return None
        return int(desc[start : start + 2])

    def set_attr(self, char: str, value: int, geom_generator=None):
        """Set rule attribute in description."""
        value = int(value)
        new_attr = f"{char}{value:02d}"
        current = self.get_attr(char)

        desc = self.rule.description()
        if current is not None:
            old_attr = f"{char}{current:02d}"
            desc = desc.replace(old_attr, new_attr)
        else:
            desc = f"{desc}{new_attr}"

        self.rule.setDescription(desc)
        self.output_dataset = desc

        i = desc.find("s")
        if i >= 0 and geom_generator:
            self.output_dataset = self.output_dataset.replace(desc[i : i + 3], "s00")

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