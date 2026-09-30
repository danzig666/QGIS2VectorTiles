# Symbol compatibility (generated)

Generated from `src/core/fidelity/capabilities.py` by `tools/generate_capabilities.py`. Do not edit by hand.

| QGIS symbol layer | Family | Strategy | Behavior | Constraints |
|---|---|---|---|---|
| `GradientFill` | fill | unsupported | Feature-relative gradients need geometry bands or raster fallback. | — |
| `ShapeburstFill` | fill | unsupported | Boundary-distance shading needs feature-aware geometry or raster fallback. | — |
| `SimpleFill` | fill | native | Solid fill color and opacity; outline exported as a separate line layer. | Qt brush styles other than solid/no-brush are drawn solid. |
| `FontMarker` | font_marker | native | Map-unit markers with a static character on point layers: the glyph outlines QGIS draws, as polygons (exact at every zoom). Otherwise browser text (glyphs generated for the font); data-defined characters, size, colour and angle are kept. Characters beyond U+FFFF or missing from the font, and markers inside marker lines, are sprites. | Browser text baselines are placed half the font's ascent below the point, as QGIS does, to within one glyph pixel (1/24 em). |
| `ArrowLine` | line | materialized | Straight arrows: body as a line of the arrow width, heads as rotated markers at the line ends. | Curved, per-segment, half or tapered arrows are approximated.<br>Heads sized in map units are omitted (reported). |
| `FilledLine` | line | materialized | Exported as a stroke of the fill width with the fill sub-symbol colour. | Pattern/gradient fills inside the line are drawn with their base colour. |
| `HashLine` | line | materialized | Hash marks are exported as rotated line markers at the QGIS positions. | Hash angle relative to a data-defined value is frozen. |
| `InterpolatedLine` | line | unsupported | Not supported. | — |
| `Lineburst` | line | unsupported | Not supported. | — |
| `RasterLine` | line | approximate | Emitted as a line pattern from the image preview. | — |
| `SimpleLine` | line | native | Stroke color, width, opacity, offset, cap, join and dash patterns. Map-unit custom dashes are exported as their dashes (Qt pattern restarted on every line and ring) from the zoom where the pattern is 6 px long. | Map-unit widths become exponential zoom curves.<br>Other dashes are MapLibre dash arrays: they restart where tiles clip a line.<br>Aligned or corner-tweaked dash patterns and trimmed lines use MapLibre dash arrays. |
| `CentroidFill` | marker | materialized | Marker points at the QGIS position (exterior-ring centroid, or GEOS point-on-surface when requested and needed). | Markers are not clipped to the polygon. |
| `EllipseMarker` | marker | sprite | Rendered by QGIS to a sprite. | — |
| `FilledMarker` | marker | sprite | Rendered by QGIS to a sprite. | — |
| `RasterMarker` | marker | sprite | Rendered by QGIS to a sprite. | — |
| `SimpleMarker` | marker | sprite | Plain circles (solid or no stroke, no offset/effect) become native circle layers with data-defined size, colours and stroke width; other shapes are rendered by QGIS to sprites. | Data-defined appearance other than size/angle/opacity needs one sprite variant per distinct value (budget: Q2VT_SPRITE_VARIANTS_BUDGET). |
| `SvgMarker` | marker | sprite | Rendered by QGIS to an oversampled sprite; data-defined SVG parameters produce sprite variants. | Data-defined appearance is bounded by the sprite variant budget. |
| `MarkerLine` | marker_line | materialized | First/last/every vertex, inner vertices, central point, segment centres and map-unit intervals (with offset along the line) are exported as point features at the QGIS positions with the line azimuth; polygon outline offsets buffer every ring like QGIS. | Screen-unit intervals are a native repeated symbol: start position differs and spacing is within about ±41 % between integer zooms.<br>Map-unit intervals denser than 8 px on screen use the native symbol at those zooms. |
| `GeometryGenerator` | other | materialized | Generated geometry is materialized in the layer CRS and coerced to the sub-symbol type; nested generators are composed as QGIS evaluates them. | Nested generators in screen units are evaluated in map units. |
| `LinePatternFill` | pattern | sprite | Screen units: periodic hatch texture with a verified repeat cell (angle and spacing within tolerance). Map units: hatch lines materialized as line features clipped to each polygon, anchored like QGIS. | Screen-unit textures are anchored to the world origin, not to each feature.<br>Only a solid simple-line sub-symbol is rendered exactly in textures. |
| `PointPatternFill` | pattern | sprite | Screen units: exact repeat cell (distance and displacement) rendered by QGIS. Map units: marker grid materialized as point features with QGIS anchoring and clip modes. | Random offsets and rotated grids are approximated (reported).<br>Markers crossing the polygon edge are drawn whole when their centre is inside. |
| `RandomMarkerFill` | pattern | approximate | Marker count as in QGIS (absolute per feature, or per map-unit density area) as seeded random points; dense zooms and screen-unit densities use a seamless texture at the QGIS density. | QGIS draws random positions in screen coordinates (they move with the view); positions differ, count and density match.<br>Markers crossing the polygon edge are not clipped. |
| `RasterFill` | pattern | sprite | Image tiled at its QGIS width (1x and 2x). | Feature- and viewport-anchored image offsets are anchored to the map origin. |
| `SVGFill` | pattern | sprite | Screen units: SVG repeat cell rendered by QGIS. Map units: grid of SVG markers materialized as point features. | Tiles crossing the polygon edge are drawn whole (QGIS clips the texture). |

## Data-defined properties with a browser emitter

- **fill**: Color, FillColor, Opacity, StrokeColor
- **font_marker**: Angle, Character, Color, FillColor, Opacity, Size
- **line**: Color, Offset, Opacity, StrokeColor, StrokeWidth
- **marker**: Angle, Opacity, Size, any other (sprite variants)
- **marker_line**: Interval, Opacity
- **pattern**: none

Any other active data-defined property produces a `Q2VT_DDP_NO_EMITTER` warning (and fails a strict export).
