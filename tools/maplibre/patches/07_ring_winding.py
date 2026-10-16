"""A polygon's rings are told apart by the winding of its largest ring
(always a shell: a hole is smaller than its shell), not of its first one.

GDAL's MVT writer can put a sliver of a few square tile units, wound as a
hole, before the shell (MakeValid after quantizing): stock MapLibre then
took the shell for that sliver's hole and filled a large wrong area.
Hole-wound rings before any shell are dropped (invisible slivers). Both the
fill bucket's and the vector tile reader's ring classification, in both
bundles.
"""

EDITS = [
    # Fill bucket (classifyRings in src/util/classify_rings.ts).
    ("both", """
    let polygon;
    let ccw;
    for (const ring of rings) {
        const area = calculateSignedArea(ring);
        if (area === 0) continue;
        ring.area = Math.abs(area);
        if (ccw === void 0) ccw = area < 0;
        if (ccw === area < 0) {
            if (polygon) polygons.push(polygon);
            polygon = [ring];
        } else polygon.push(ring);
    }""", """
    let polygon;
    let ccw;
    let largest = 0;
    for (const ring of rings) {
        ring.q2area = calculateSignedArea(ring);
        if (Math.abs(ring.q2area) > largest) {
            largest = Math.abs(ring.q2area);
            ccw = ring.q2area < 0;
        }
    }
    for (const ring of rings) {
        const area = ring.q2area;
        if (area === 0) continue;
        ring.area = Math.abs(area);
        if (ccw === area < 0) {
            if (polygon) polygons.push(polygon);
            polygon = [ring];
        } else if (polygon) polygon.push(ring);
    }""", 1),
    # Vector tile features (@mapbox/vector-tile toGeoJSON).
    ("both", """
    let polygon, ccw;
    for (let i = 0; i < len; i++) {
        const area = signedArea(rings[i]);
        if (area === 0) continue;
        if (ccw === void 0) ccw = area < 0;""", """
    let polygon, ccw;
    let largest = 0;
    for (let i = 0; i < len; i++) {
        const area = signedArea(rings[i]);
        if (Math.abs(area) > largest) {
            largest = Math.abs(area);
            ccw = area < 0;
        }
    }
    for (let i = 0; i < len; i++) {
        const area = signedArea(rings[i]);
        if (area === 0) continue;""", 1),
]
