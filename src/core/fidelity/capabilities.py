"""
capabilities.py

Registry of supported QGIS symbol components and the export strategy used
for each. It is the single source of truth for the compatibility document
(``tools/generate_capabilities.py`` renders it to Markdown), so documentation
and behavior cannot drift apart.

Support is not one boolean per QGIS class: the same class may be native for
some property/unit combinations and approximate for others. ``classify``
takes those combinations into account.
"""

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from .model import Strategy

# Data-defined properties with a MapLibre emitter, per symbol-layer family.
# Names are QgsSymbolLayer.Property member names without the "Property" prefix.
DDP_EMITTERS: Dict[str, frozenset] = {
    "line": frozenset({"StrokeColor", "StrokeWidth", "Opacity", "Offset", "Color"}),
    "fill": frozenset({"FillColor", "Opacity", "StrokeColor", "Color"}),
    "marker": frozenset({"Size", "Angle", "Opacity"}),
    "marker_line": frozenset({"Opacity"}),
    "pattern": frozenset(),
}

# Data-defined properties consumed during export rather than emitted to the
# style (they never produce a warning).
DDP_EXPORT_TIME = frozenset({"LayerEnabled", "GeometryGenerator"})


@dataclass(frozen=True)
class Capability:
    qgis_type: str                 # QgsSymbolLayer.layerType()
    family: str                    # line | fill | marker | marker_line | pattern | other
    strategy: Strategy
    summary: str
    constraints: Tuple[str, ...] = field(default_factory=tuple)
    tests: Tuple[str, ...] = field(default_factory=tuple)


_REGISTRY: Dict[str, Capability] = {c.qgis_type: c for c in [
    Capability("SimpleFill", "fill", Strategy.NATIVE,
               "Solid fill color and opacity; outline exported as a separate line layer.",
               ("Qt brush styles other than solid/no-brush are drawn solid.",),
               ("tests/integration/test_converter.py",)),
    Capability("SimpleLine", "line", Strategy.NATIVE,
               "Stroke color, width, opacity, offset, cap, join and dash patterns.",
               ("Map-unit widths become exponential zoom curves.",
                "Dash lengths are scaled by MapLibre with the line width."),
               ("tests/integration/test_converter.py",)),
    Capability("SimpleMarker", "marker", Strategy.SPRITE,
               "Rendered by QGIS to a sprite; size/angle/opacity may be data-defined.",
               ("Data-defined color requires one sprite per color (not yet generated).",),
               ("tests/integration/test_sprites.py",)),
    Capability("SvgMarker", "marker", Strategy.SPRITE,
               "Rendered by QGIS to a sprite at 1x and 2x.",
               ("Data-defined SVG parameters are frozen at the static value.",)),
    Capability("RasterMarker", "marker", Strategy.SPRITE, "Rendered by QGIS to a sprite."),
    Capability("FontMarker", "marker", Strategy.SPRITE, "Rendered by QGIS to a sprite."),
    Capability("EllipseMarker", "marker", Strategy.SPRITE, "Rendered by QGIS to a sprite."),
    Capability("FilledMarker", "marker", Strategy.SPRITE, "Rendered by QGIS to a sprite."),
    Capability("MarkerLine", "marker_line", Strategy.APPROXIMATE,
               "Sub-symbol sprite repeated along the line (interval / center placement).",
               ("First/last/every-vertex placements are approximated until materialized "
                "marker positions are implemented.",)),
    Capability("LinePatternFill", "pattern", Strategy.SPRITE,
               "Periodic hatch texture with a verified repeat cell (angle and spacing "
               "within tolerance).",
               ("Screen-fixed: MapLibre fill patterns do not scale with zoom.",
                "Pattern is anchored to the world origin, not to each feature.",
                "Only a solid simple-line sub-symbol is rendered exactly."),
               ("tests/unit/test_patterns.py",)),
    Capability("PointPatternFill", "pattern", Strategy.APPROXIMATE,
               "Rendered through a QGIS preview crop; repeat period not verified.",
               ("Planned: verified repeat cell (PR-08).",)),
    Capability("SVGFill", "pattern", Strategy.APPROXIMATE,
               "Rendered through a QGIS preview crop; repeat period not verified."),
    Capability("RasterFill", "pattern", Strategy.APPROXIMATE,
               "Rendered through a QGIS preview crop; repeat period not verified."),
    Capability("RandomMarkerFill", "pattern", Strategy.APPROXIMATE,
               "Random positions are not periodic; a preview texture is repeated."),
    Capability("CentroidFill", "marker", Strategy.MATERIALIZED,
               "Centroid points are materialized as point features."),
    Capability("GeometryGenerator", "other", Strategy.MATERIALIZED,
               "Generated geometry is materialized in the export CRS workflow."),
    Capability("GradientFill", "fill", Strategy.UNSUPPORTED,
               "Feature-relative gradients need geometry bands or raster fallback."),
    Capability("ShapeburstFill", "fill", Strategy.UNSUPPORTED,
               "Boundary-distance shading needs feature-aware geometry or raster fallback."),
    Capability("ArrowLine", "line", Strategy.UNSUPPORTED,
               "Planned: materialized arrow geometry (PR-14)."),
    Capability("HashLine", "line", Strategy.UNSUPPORTED,
               "Planned: materialized hash marks (PR-14)."),
    Capability("InterpolatedLine", "line", Strategy.UNSUPPORTED, "Not supported."),
    Capability("RasterLine", "line", Strategy.APPROXIMATE,
               "Emitted as a line pattern from the image preview."),
    Capability("Lineburst", "line", Strategy.UNSUPPORTED, "Not supported."),
    Capability("FilledLine", "line", Strategy.UNSUPPORTED,
               "Planned: materialized buffered polygons (PR-14)."),
]}


def capability(qgis_type: str) -> Optional[Capability]:
    return _REGISTRY.get(qgis_type)


def all_capabilities() -> List[Capability]:
    return sorted(_REGISTRY.values(), key=lambda c: (c.family, c.qgis_type))


@dataclass(frozen=True)
class Classification:
    strategy: Strategy
    unsupported_properties: Tuple[str, ...] = ()
    reason: str = ""


def classify(qgis_type: str, data_defined: Iterable[str] = ()) -> Classification:
    """Choose a strategy for a symbol layer given its active data-defined properties."""
    cap = capability(qgis_type)
    if cap is None:
        return Classification(Strategy.UNSUPPORTED, (),
                              f"Unknown or plugin symbol layer type '{qgis_type}'")
    emitters = DDP_EMITTERS.get(cap.family, frozenset())
    unsupported = tuple(sorted(
        name for name in data_defined
        if name not in emitters and name not in DDP_EXPORT_TIME
    ))
    return Classification(cap.strategy, unsupported, cap.summary)


def capabilities_markdown() -> str:
    """Render the registry as Markdown (generated documentation)."""
    lines = [
        "# Symbol compatibility (generated)",
        "",
        "Generated from `src/core/fidelity/capabilities.py` by "
        "`tools/generate_capabilities.py`. Do not edit by hand.",
        "",
        "| QGIS symbol layer | Family | Strategy | Behavior | Constraints |",
        "|---|---|---|---|---|",
    ]
    for cap in all_capabilities():
        lines.append(
            f"| `{cap.qgis_type}` | {cap.family} | {cap.strategy.value} | {cap.summary} | "
            f"{'<br>'.join(cap.constraints) or '—'} |")
    lines += ["", "## Data-defined properties with a browser emitter", ""]
    for family, names in sorted(DDP_EMITTERS.items()):
        lines.append(f"- **{family}**: {', '.join(sorted(names)) or 'none'}")
    lines += [
        "",
        "Any other active data-defined property produces a `Q2VT_DDP_NO_EMITTER` "
        "warning (and fails a strict export).",
        "",
    ]
    return "\n".join(lines)
