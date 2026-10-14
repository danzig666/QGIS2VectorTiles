"""No blank map between zoom levels in the published viewer."""

import math


def bridge_zoom_bands(style: dict) -> dict:
    """Widen a data layer's zoom range by one zoom on a side where the tiles
    there don't hold its data, so the map does not go blank between zooms.

    While the next zoom's tiles load, MapLibre draws the tiles it has in their
    place (512 px tiles: the tiles of zoom floor(map zoom)); a layer starting
    at the new zoom has no data in the previous zoom's tiles, and one ending
    there is hidden while the new tiles, which hold the next rule's data, are
    still loading. Each dataset is written only into the tiles of its own
    zoom range (``q2vt:tiles``, see core.fidelity.zoom.note_tile_zooms), so a widened side only
    ever draws it from those stand-in tiles: never twice, never at a zoom
    whose tiles hold its data (shared datasets, fractional scale limits, the
    last zoom's enlarged tiles keep their range). The range it draws its own
    data in stays in ``q2vt:zooms`` (the viewer's layer list)."""
    archive_max_zoom = (style.get("metadata") or {}).get("q2vt:tiles-max-zoom")
    if archive_max_zoom is None:
        return style
    for layer_def in style.get("layers", []):
        tiles = (layer_def.get("metadata") or {}).get("q2vt:tiles")
        if not tiles:
            continue
        low, high = tiles
        start, end = layer_def.get("minzoom"), layer_def.get("maxzoom")
        drawn = [start, end]
        # [start - 1, start) shows the tiles of zooms up to ceil(start) - 1.
        if start is not None and start >= 1 and math.ceil(start) <= low:
            layer_def["minzoom"] = start - 1
        # [end, end + 1) shows the tiles of zooms from floor(end) (beyond the
        # archive's last zoom: its tiles, enlarged).
        if end is not None and end + 1 <= 24 and min(math.floor(end), archive_max_zoom) > high:
            layer_def["maxzoom"] = end + 1
        if [layer_def.get("minzoom"), layer_def.get("maxzoom")] != drawn:
            layer_def["metadata"]["q2vt:zooms"] = drawn
    return style
