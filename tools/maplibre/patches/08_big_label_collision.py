"""A label whose collision box covers the whole collision grid (the view
plus 100 px around it) collides only with the boxes placed in it.

Stock MapLibre counted it as colliding with everything, so big map-unit
text (laid out at the size of the next zoom) vanished when zoomed in, even
alone, while QGIS draws it. Hit tests now go on to the per-cell query
(cells clamped to the grid).
"""

EDITS = [
    ("main", """
        if (x1 <= 0 && y1 <= 0 && this.width <= x2 && this.height <= y2) {
            if (hitTest) return [{""", """
        if (!hitTest && x1 <= 0 && y1 <= 0 && this.width <= x2 && this.height <= y2) {
            if (hitTest) return [{""", 1),
]
