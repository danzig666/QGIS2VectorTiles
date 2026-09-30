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
    "marker": frozenset({"Size", "Angle", "Opacity", "any other (sprite variants)"}),
    "marker_line": frozenset({"Opacity", "Interval"}),
    "font_marker": frozenset({"Character", "Size", "Angle", "Opacity", "FillColor", "Color"}),
    "pattern": frozenset(),
}

# Families rendered to sprites by QGIS, where data-defined appearance is
# reproduced with per-value sprite variants (bounded by a budget that is
# reported separately as Q2VT_SPRITE_VARIANTS_BUDGET).
SPRITE_FAMILIES = frozenset({"marker"})

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
               "Stroke color, width, opacity, offset, cap, join and dash patterns. Map-unit "
               "custom dashes are exported as their dashes (Qt pattern restarted on every "
               "line and ring) from the zoom where the pattern is 6 px long.",
               ("Map-unit widths become exponential zoom curves.",
                "Other dashes are MapLibre dash arrays: they restart where tiles clip a line.",
                "Aligned or corner-tweaked dash patterns and trimmed lines use MapLibre "
                "dash arrays."),
               ("tests/integration/test_converter.py", "tests/integration/test_materialize.py",
                "tests/browser/test_browser_parity.py")),
    Capability("SimpleMarker", "marker", Strategy.SPRITE,
               "Plain circles (solid or no stroke, no offset/effect) become native circle "
               "layers with data-defined size, colours and stroke width; other shapes are "
               "rendered by QGIS to sprites.",
               ("Data-defined appearance other than size/angle/opacity needs one sprite "
                "variant per distinct value (budget: Q2VT_SPRITE_VARIANTS_BUDGET).",),
               ("tests/integration/test_native_circles.py",
                "tests/integration/test_sprites.py")),
    Capability("SvgMarker", "marker", Strategy.SPRITE,
               "Rendered by QGIS to an oversampled sprite; data-defined SVG parameters "
               "produce sprite variants.",
               ("Data-defined appearance is bounded by the sprite variant budget.",),
               ("tests/integration/test_sprites.py",)),
    Capability("RasterMarker", "marker", Strategy.SPRITE, "Rendered by QGIS to a sprite."),
    Capability("FontMarker", "font_marker", Strategy.NATIVE,
               "Map-unit markers with a static character on point layers: the glyph outlines "
               "QGIS draws, as polygons (exact at every zoom). Otherwise browser text (glyphs "
               "generated for the font); data-defined characters, size, colour and angle are "
               "kept. Characters beyond U+FFFF or missing from the font, and markers inside "
               "marker lines, are sprites.",
               ("Browser text baselines are placed half the font's ascent below the point, "
                "as QGIS does, to within one glyph pixel (1/24 em).",)),
    Capability("EllipseMarker", "marker", Strategy.SPRITE, "Rendered by QGIS to a sprite."),
    Capability("FilledMarker", "marker", Strategy.SPRITE, "Rendered by QGIS to a sprite."),
    Capability("MarkerLine", "marker_line", Strategy.MATERIALIZED,
               "First/last/every vertex, inner vertices, central point, segment centres and "
               "map-unit intervals (with offset along the line) are exported as point features "
               "at the QGIS positions with the line azimuth; polygon outline offsets buffer "
               "every ring like QGIS.",
               ("Screen-unit intervals are a native repeated symbol: start position differs "
                "and spacing is within about ±41 % between integer zooms.",
                "Map-unit intervals denser than 8 px on screen use the native symbol at "
                "those zooms."),
               ("tests/integration/test_materialize.py",
                "tests/browser/test_browser_parity.py")),
    Capability("HashLine", "line", Strategy.MATERIALIZED,
               "Hash marks are exported as rotated line markers at the QGIS positions.",
               ("Hash angle relative to a data-defined value is frozen.",),
               ("tests/integration/test_materialize.py",)),
    Capability("ArrowLine", "line", Strategy.MATERIALIZED,
               "Straight arrows: body as a line of the arrow width, heads as rotated "
               "markers at the line ends.",
               ("Curved, per-segment, half or tapered arrows are approximated.",
                "Heads sized in map units are omitted (reported)."),
               ("tests/integration/test_materialize.py",)),
    Capability("FilledLine", "line", Strategy.MATERIALIZED,
               "Exported as a stroke of the fill width with the fill sub-symbol colour.",
               ("Pattern/gradient fills inside the line are drawn with their base colour.",),
               ("tests/integration/test_materialize.py",)),
    Capability("LinePatternFill", "pattern", Strategy.SPRITE,
               "Screen units: periodic hatch texture with a verified repeat cell (angle and "
               "spacing within tolerance). Map units: hatch lines materialized as line "
               "features clipped to each polygon, anchored like QGIS.",
               ("Screen-unit textures are anchored to the world origin, not to each feature.",
                "Only a solid simple-line sub-symbol is rendered exactly in textures."),
               ("tests/unit/test_patterns_and_assets.py",
                "tests/integration/test_materialize.py")),
    Capability("PointPatternFill", "pattern", Strategy.SPRITE,
               "Screen units: exact repeat cell (distance and displacement) rendered by QGIS. "
               "Map units: marker grid materialized as point features with QGIS anchoring "
               "and clip modes.",
               ("Random offsets follow QGIS's range, not its sequence; rotated grids are "
                "approximated (reported).",
                "\"Shape\" clipping: line, cross and closed simple markers are exported as "
                "clipped geometry (cut at the edge like QGIS); other markers are drawn whole "
                "when their centre is inside."),
               ("tests/integration/test_sprites.py", "tests/integration/test_materialize.py")),
    Capability("SVGFill", "pattern", Strategy.SPRITE,
               "Screen units: SVG repeat cell rendered by QGIS. Map units: grid of SVG "
               "markers materialized as point features.",
               ("Tiles crossing the polygon edge are drawn whole (QGIS clips the texture).",)),
    Capability("RasterFill", "pattern", Strategy.SPRITE,
               "Image tiled at its QGIS width (1x and 2x).",
               ("Feature- and viewport-anchored image offsets are anchored to the map origin.",)),
    Capability("RandomMarkerFill", "pattern", Strategy.APPROXIMATE,
               "Marker count as in QGIS (absolute per feature, or per map-unit density area) "
               "as seeded random points; dense zooms and screen-unit densities use a "
               "seamless texture at the QGIS density.",
               ("QGIS draws random positions in screen coordinates (they move with the "
                "view); positions differ, count and density match.",
                "Markers crossing the polygon edge are not clipped.")),
    Capability("CentroidFill", "marker", Strategy.MATERIALIZED,
               "Marker points at the QGIS position (exterior-ring centroid, or GEOS "
               "point-on-surface when requested and needed).",
               ("Markers are not clipped to the polygon.",)),
    Capability("GeometryGenerator", "other", Strategy.MATERIALIZED,
               "Generated geometry is materialized in the layer CRS and coerced to the "
               "sub-symbol type; nested generators are composed as QGIS evaluates them.",
               ("Nested generators in screen units are evaluated in map units.",)),
    Capability("GradientFill", "fill", Strategy.UNSUPPORTED,
               "Feature-relative gradients need geometry bands or raster fallback."),
    Capability("ShapeburstFill", "fill", Strategy.UNSUPPORTED,
               "Boundary-distance shading needs feature-aware geometry or raster fallback."),
    Capability("InterpolatedLine", "line", Strategy.UNSUPPORTED, "Not supported."),
    Capability("RasterLine", "line", Strategy.APPROXIMATE,
               "Emitted as a line pattern from the image preview."),
    Capability("Lineburst", "line", Strategy.UNSUPPORTED, "Not supported."),
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
    if cap.family in SPRITE_FAMILIES:
        # Rendered by QGIS: every appearance property is baked into one
        # sprite variant per distinct value combination.
        return Classification(cap.strategy, (), cap.summary)
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
