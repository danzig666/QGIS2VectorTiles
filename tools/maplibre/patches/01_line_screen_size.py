"""Line patterns (line-pattern) and dash arrays keep their screen size at
every zoom, as QGIS draws them.

Stock MapLibre scales them with the tile's integer zoom
(``transform.tileZoom``): exact at whole zooms, growing up to 2x until the
next one (a 23 px raster line repeat drawn 27 / 32 / 37 px at x.25 / x.5 /
x.75), and cross-fades a double-size copy after every whole zoom. Here:

1. the line tile ratio is measured at the real zoom (``transform.zoom``);
2. both cross-fade sides of the line programs get the same scale;
3. patterns and dashes are sized by the line's width at the current zoom
   (``width``), not at the whole zoom (``floorwidth``, which stock MapLibre
   multiplies back up through the tile scale): with 1. that keeps
   screen-size lines constant and map-size lines growing with the map.
"""

EDITS = [
    ("main", """
function calculateTileRatio(tile, transform) {
    return 1 / pixelsToTileUnits(tile, 1, transform.tileZoom);
}""", """
function calculateTileRatio(tile, transform) {
    return 1 / pixelsToTileUnits(tile, 1, transform.zoom);
}""", 1),
    # line-pattern: u_scale [ratio, fromScale, toScale] -> both sides toScale.
    ("main", """
        "u_image": 0,
        "u_scale": [
            tileZoomRatio,
            crossfade.fromScale,
            crossfade.toScale
        ],""", """
        "u_image": 0,
        "u_scale": [
            tileZoomRatio,
            crossfade.toScale,
            crossfade.toScale
        ],""", 1),
    # Dashes (line and line-gradient dash programs).
    ("main", """
        "u_crossfade_from": crossfade.fromScale,
        "u_crossfade_to": crossfade.toScale,""", """
        "u_crossfade_from": crossfade.toScale,
        "u_crossfade_to": crossfade.toScale,""", 2),
    ("main", "v_linesofar=a_linesofar;v_width2=vec2(outset,inset);v_width=floorwidth;}",
     "v_linesofar=a_linesofar;v_width2=vec2(outset,inset);v_width=width;}", 1),
    ("main", "a_linesofar*u_patternscale_a_x/floorwidth", "a_linesofar*u_patternscale_a_x/width", 2),
    ("main", "a_linesofar*u_patternscale_b_x/floorwidth", "a_linesofar*u_patternscale_b_x/width", 2),
]
