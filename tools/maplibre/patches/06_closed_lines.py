"""A line whose last point is its first (a polygon outline exported as a
line, a closed contour) is joined there like a polygon ring, as Qt strokes a
closed path; stock MapLibre gave it two caps there (a knob with square caps,
a darker spot on translucent lines). A ring's dashes start at its first
vertex, as in QGIS: stock MapLibre counted the closing segment before it
(the pattern started shifted).
"""

EDITS = [
    ("both", """
        const isPolygon = VectorTileFeature.types[feature.type] === "Polygon";
        let len = vertices.length;""", """
        const isPolygon = VectorTileFeature.types[feature.type] === "Polygon" || vertices.length > 3 && vertices[0].equals(vertices[vertices.length - 1]);
        let len = vertices.length;""", 1),
    ("both", """
            if (prevVertex) this.updateDistance(prevVertex, currentVertex);
            if (currentJoin === "miter") {""", """
            if (prevVertex && i > first) this.updateDistance(prevVertex, currentVertex);
            if (currentJoin === "miter") {""", 1),
]
