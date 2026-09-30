# Symbol compatibility (generated)

Generated from `src/core/fidelity/capabilities.py` by `tools/generate_capabilities.py`. Do not edit by hand.

| QGIS symbol layer | Family | Strategy | Behavior | Constraints |
|---|---|---|---|---|
| `GradientFill` | fill | unsupported | Feature-relative gradients need geometry bands or raster fallback. | — |
| `ShapeburstFill` | fill | unsupported | Boundary-distance shading needs feature-aware geometry or raster fallback. | — |
| `SimpleFill` | fill | native | Solid fill color and opacity; outline exported as a separate line layer. | Qt brush styles other than solid/no-brush are drawn solid. |
| `FontMarker` | font_marker | native | Exported as browser text (glyphs generated for the font); data-defined characters, size, colour and angle are kept. Inside marker lines it is a sprite. | Vertical position uses the text box centre (QGIS: half the font ascent). |
| `ArrowLine` | line | unsupported | Planned: materialized arrow geometry (PR-14). | — |
| `FilledLine` | line | unsupported | Planned: materialized buffered polygons (PR-14). | — |
| `HashLine` | line | unsupported | Planned: materialized hash marks (PR-14). | — |
| `InterpolatedLine` | line | unsupported | Not supported. | — |
| `Lineburst` | line | unsupported | Not supported. | — |
| `RasterLine` | line | approximate | Emitted as a line pattern from the image preview. | — |
| `SimpleLine` | line | native | Stroke color, width, opacity, offset, cap, join and dash patterns. | Map-unit widths become exponential zoom curves.<br>Dash lengths are scaled by MapLibre with the line width. |
| `CentroidFill` | marker | materialized | Centroid points are materialized as point features. | — |
| `EllipseMarker` | marker | sprite | Rendered by QGIS to a sprite. | — |
| `FilledMarker` | marker | sprite | Rendered by QGIS to a sprite. | — |
| `RasterMarker` | marker | sprite | Rendered by QGIS to a sprite. | — |
| `SimpleMarker` | marker | sprite | Rendered by QGIS to a sprite; size/angle/opacity may be data-defined. | Data-defined color requires one sprite per color (not yet generated). |
| `SvgMarker` | marker | sprite | Rendered by QGIS to a sprite at 1x and 2x. | Data-defined SVG parameters are frozen at the static value. |
| `MarkerLine` | marker_line | approximate | Sub-symbol sprite repeated along the line (interval / center placement). | First/last/every-vertex placements are approximated until materialized marker positions are implemented. |
| `GeometryGenerator` | other | materialized | Generated geometry is materialized in the export CRS workflow. | — |
| `LinePatternFill` | pattern | sprite | Periodic hatch texture with a verified repeat cell (angle and spacing within tolerance). | Screen-fixed: MapLibre fill patterns do not scale with zoom.<br>Pattern is anchored to the world origin, not to each feature.<br>Only a solid simple-line sub-symbol is rendered exactly. |
| `PointPatternFill` | pattern | approximate | Rendered through a QGIS preview crop; repeat period not verified. | Planned: verified repeat cell (PR-08). |
| `RandomMarkerFill` | pattern | approximate | Random positions are not periodic; a preview texture is repeated. | — |
| `RasterFill` | pattern | approximate | Rendered through a QGIS preview crop; repeat period not verified. | — |
| `SVGFill` | pattern | approximate | Rendered through a QGIS preview crop; repeat period not verified. | — |

## Data-defined properties with a browser emitter

- **fill**: Color, FillColor, Opacity, StrokeColor
- **font_marker**: Angle, Char, Color, FillColor, Opacity, Size
- **line**: Color, Offset, Opacity, StrokeColor, StrokeWidth
- **marker**: Angle, Opacity, Size
- **marker_line**: Interval, Opacity
- **pattern**: none

Any other active data-defined property produces a `Q2VT_DDP_NO_EMITTER` warning (and fails a strict export).
