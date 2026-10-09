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
               "Solid fill color and opacity; outline exported as a separate line layer. Qt "
               "brush styles (dense dots, hatching, crossing) as their 8 px pattern texture, "
               "started at the corner of the view as in QGIS.",
               ("A Qt brush style with a data-defined colour or style is drawn solid.",),
               ("tests/integration/test_converter.py", "tests/browser/test_browser_parity.py")),
    Capability("SimpleLine", "line", Strategy.NATIVE,
               "Stroke color, width, opacity, offset, cap, join and dash patterns. Map-unit "
               "custom dashes are exported as their dashes (Qt pattern restarted on every "
               "line and ring) from the zoom where the pattern is 6 px long. Offsets are "
               "QGIS's offset curves where MapLibre's line-offset would loop at corners "
               "(screen units: per eighth of a zoom up to the zoom where the corners allow "
               "it). Outer glow and drop shadow are blurred lines drawn from a copy "
               "simplified at the effect's size; inner shadow and inner glow are strips "
               "across the line coloured by QGIS's own rendering for the line's direction.",
               ("Map-unit widths become exponential zoom curves.",
                "Inner effects are computed for a straight line: where lines overlap or "
                "nearly touch, QGIS shades their union, the web map each line.",
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
               "intervals (with offset along the line; map units exactly, screen units per "
               "eighth of a zoom) are exported as point features at the QGIS positions with the "
               "line azimuth; polygon outline offsets buffer every ring like QGIS.",
               ("Data-defined intervals, and screen-unit intervals with Fast marker lines on, "
                "are a native repeated symbol: start position differs and spacing is within "
                "about ±41 % between integer zooms (reported).",
                "Map-unit intervals denser than 8 px on screen use the native symbol at "
                "those zooms."),
               ("tests/integration/test_materialize.py",
                "tests/browser/test_browser_parity.py")),
    Capability("HashLine", "line", Strategy.MATERIALIZED,
               "Hash marks are exported as rotated line markers at the QGIS positions.",
               ("Hash angle relative to a data-defined value is frozen.",),
               ("tests/integration/test_materialize.py",)),
    Capability("ArrowLine", "line", Strategy.MATERIALIZED,
               "The polygons QGIS fills (its straight and curved arrow construction, every "
               "head and arrow type, the vertex pairing of repeated and curved arrows), filled "
               "with the arrow's fill symbol; screen sizes per eighth of a zoom. Opaque "
               "multi-layer fills (a drop shadow) keep QGIS's per-arrow drawing order; the "
               "line keeps all its vertices.",
               ("Sizes are within +-4.5 % between eighths of a zoom; beyond the archive's "
                "last zoom the arrows scale with the map.",
                "QGIS rebuilds arrows from the line clipped to the view, so near the view's "
                "edge its own arrows change while panning; the web map keeps the whole line.",
                "Data-defined arrow sizes use their static values."),
               ("tests/integration/test_line_shapes.py", "tests/integration/test_materialize.py",
                "tests/integration/test_gradient_fills.py")),
    Capability("FilledLine", "line", Strategy.MATERIALIZED,
               "Exported as a stroke of the fill width with the fill sub-symbol colour.",
               ("Pattern/gradient fills inside the line are drawn with their base colour.",),
               ("tests/integration/test_materialize.py",)),
    Capability("LinePatternFill", "pattern", Strategy.SPRITE,
               "Screen units: periodic hatch texture with a verified repeat cell (angle and "
               "spacing within tolerance). Map units: hatch lines materialized as line "
               "features clipped to each polygon, anchored like QGIS. "
               "Textures start where QGIS starts them: \"Align pattern to: "
               "Feature\" at the bottom-left of each feature's bounding box, \"Viewport\" "
               "at the view's corner (patched MapLibre), rounded to whole pixels.",
               ("Only a solid simple-line sub-symbol is rendered exactly in textures.",),
               ("tests/unit/test_patterns_and_assets.py",
                "tests/integration/test_materialize.py")),
    Capability("PointPatternFill", "pattern", Strategy.SPRITE,
               "Screen units, \"Shape\" clipping: QGIS's own texture brush (two spacings "
               "truncated to whole pixels, overlapping markers stacked in its drawing order). "
               "Screen units, other clip modes: whole markers as point features per eighth "
               "of a zoom, kept by QGIS's test (centre, bounds) and stacked in its drawing "
               "order (column by column, each from the top). "
               "Map units: marker grid materialized as point features with QGIS anchoring "
               "and clip modes. "
               "Textures start where QGIS starts them: \"Align pattern to: "
               "Feature\" at the bottom-left of each feature's bounding box, \"Viewport\" "
               "at the view's corner (patched MapLibre), rounded to whole pixels.",
               ("Random offsets follow QGIS's range, not its sequence; rotated grids are "
                "approximated (reported).",
                "Screen-unit marker grids keep their spacing within ±4.5 % (per eighth of a "
                "zoom); set to \"Viewport\" they start at the map origin, so the markers "
                "along the edges differ from QGIS's, which change as the view moves.",
                "\"Shape\" clipping: line, cross and closed simple markers are exported as "
                "clipped geometry (cut at the edge like QGIS); other markers are drawn whole "
                "when their centre is inside."),
               ("tests/integration/test_sprites.py", "tests/integration/test_materialize.py")),
    Capability("SVGFill", "pattern", Strategy.SPRITE,
               "Screen units: SVG repeat cell rendered by QGIS. Map units: grid of SVG "
               "markers materialized as point features. "
               "Textures start where QGIS starts them: \"Align pattern to: "
               "Feature\" at the bottom-left of each feature's bounding box, \"Viewport\" "
               "at the view's corner (patched MapLibre), rounded to whole pixels.",
               ("Tiles crossing the polygon edge are drawn whole (QGIS clips the texture).",)),
    Capability("RasterFill", "pattern", Strategy.SPRITE,
               "Image tiled at its QGIS width (1x and 2x), starting where QGIS starts it: "
               "\"Coordinate mode: Object\" at the top-left of each part (clipped to the "
               "view grown by 10 %, as QGIS clips it), \"Viewport\" at the view's corner.",
               ("Rotated or tilted maps keep the anchor without QGIS's view clipping.",)),
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
    Capability("GradientFill", "fill", Strategy.MATERIALIZED,
               "Linear, radial and conical gradients (two colours or a colour ramp, pad / "
               "reflect / repeat) as solid colour bands per feature, from the QGIS reference "
               "points of its bounding box.",
               ("Smooth colour change becomes steps of about one level (up to 1024 bands, "
                "merged where narrower than a pixel two zooms past the archive).",
                "Viewport-relative gradients are drawn relative to each feature (reported).")),
    Capability("ShapeburstFill", "fill", Strategy.MATERIALIZED,
               "Shading by distance to the boundary as inset colour bands (whole shape or a "
               "set distance, rings ignored when set).",
               ("Smooth colour change becomes steps of about one level (up to 1024 bands); blur is "
                "not applied.",
                "A distance in screen units is set per quarter zoom (within about 9 %).")),
    Capability("InterpolatedLine", "line", Strategy.MATERIALIZED,
               "Colour and width interpolated along each line between the per-feature start "
               "and end values: exported as short pieces (about 64 over the value range) with "
               "the colour and width QGIS gives their middle.",
               ("Colour and width change in small steps instead of continuously.",),
               ("tests/integration/test_more_symbology.py",)),
    Capability("RasterLine", "line", Strategy.NATIVE,
               "The image along the line, its height the line width (MapLibre line-pattern), "
               "repeating every whole pixel as QGIS does, starting where the cap starts.",
               ("The repeat restarts where a tile cuts a line (as dash patterns do).",),
               ("tests/integration/test_more_symbology.py",)),
    Capability("Lineburst", "line", Strategy.NATIVE,
               "The gradient across the line (colour 1 on the left edge, colour 2 on the "
               "right) as a line-pattern image stretched to the line width, caps and joins "
               "included.",
               ("Blur is not applied.",),
               ("tests/integration/test_more_symbology.py",)),
    Capability("VectorField", "marker", Strategy.MATERIALIZED,
               "A line from each point by the vector (x/y, length/angle or height) times the "
               "scale, drawn with the line sub-symbol; screen-unit lengths per zoom.",
               ("Screen-unit lengths are exact in the middle of each zoom.",
                "The marker offset is not applied."),
               ("tests/integration/test_more_symbology.py",)),
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
