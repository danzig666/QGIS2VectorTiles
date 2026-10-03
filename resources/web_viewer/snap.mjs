// Snapping for the measurement tools: the nearest vertex (else the nearest
// point of an edge or line) of the features drawn by the publisher's snap
// layers, within a few pixels of the pointer. Uses the geometry of the
// loaded vector tiles (no extra download): coordinates as precise as the
// tiles (centimetres at the deepest zoom, coarser when zoomed out). Tile
// cuts are not real edges: only each tile's own square counts (its buffer
// belongs to the neighbouring tile), so the corners and edges made by
// cutting a polygon at a tile edge are never snapped to.

const SNAP_TYPES = new Set(["fill", "line", "circle"]);

// Web Mercator world units (0..1) -> longitude/latitude.
export function worldToLngLat([x, y]) {
  const lng = x * 360 - 180;
  const lat = Math.atan(Math.sinh(Math.PI * (1 - 2 * y))) * 180 / Math.PI;
  return [lng, lat];
}

// The part of segment a-b inside the box [x0, y0, x1, y1] (Liang-Barsky), or null.
export function clipSegment([ax, ay], [bx, by], [x0, y0, x1, y1]) {
  let t0 = 0, t1 = 1;
  const dx = bx - ax, dy = by - ay;
  for (const [p, q] of [[-dx, ax - x0], [dx, x1 - ax], [-dy, ay - y0], [dy, y1 - ay]]) {
    if (p === 0) {
      if (q < 0) return null;
    } else {
      const r = q / p;
      if (p < 0) { if (r > t1) return null; if (r > t0) t0 = r; }
      else { if (r < t0) return null; if (r < t1) t1 = r; }
    }
  }
  return [[ax + t0 * dx, ay + t0 * dy], [ax + t1 * dx, ay + t1 * dy]];
}

// Nearest vertex within ``radius`` (pixels) of ``point``, else the nearest
// point on a segment. ``parts``: [{vertices: [[px, py, world]], segments:
// [[[px, py], [px, py], worldA, worldB]]}] in screen pixels. Returns
// {world, kind: "vertex" | "edge", distance} or null.
export function nearest(point, parts, radius) {
  const [x, y] = point;
  let best = null;
  for (const part of parts) {
    for (const [px, py, world] of part.vertices) {
      const d = Math.hypot(px - x, py - y);
      if (d <= radius && (!best || d < best.distance)) best = { world, kind: "vertex", distance: d };
    }
  }
  if (best) return best;
  for (const part of parts) {
    for (const [[ax, ay], [bx, by], wa, wb] of part.segments) {
      const dx = bx - ax, dy = by - ay;
      const len2 = dx * dx + dy * dy;
      const t = len2 ? Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / len2)) : 0;
      const d = Math.hypot(ax + t * dx - x, ay + t * dy - y);
      if (d <= radius && (!best || d < best.distance)) {
        best = { world: [wa[0] + t * (wb[0] - wa[0]), wa[1] + t * (wb[1] - wa[1])], kind: "edge", distance: d };
      }
    }
  }
  return best;
}

export class Snapper {
  // ``layerIds``: logical layers the publisher marked for snapping.
  constructor({ map, manifest }) {
    this.map = map;
    const wanted = new Set((manifest.layers || []).filter((l) => l.snap).map((l) => l.id));
    this.styleLayers = [];
    for (const component of manifest.components || []) {
      if (!wanted.has(component.layerId)) continue;
      for (const id of component.styleLayerIds || []) {
        const layer = map.getLayer(id);
        if (layer && SNAP_TYPES.has(layer.type)) this.styleLayers.push(id);
      }
    }
    this.titles = (manifest.layers || []).filter((l) => wanted.has(l.id)).map((l) => l.title);
  }

  get available() { return this.styleLayers.length > 0; }

  // The snapped [lng, lat] near the screen point, with its kind, or null.
  snap(point, radius) {
    if (!this.available) return null;
    const box = [[point[0] - radius, point[1] - radius], [point[0] + radius, point[1] + radius]];
    let features;
    try {
      features = this.map.queryRenderedFeatures(box, { layers: this.styleLayers.filter((id) => this.map.getLayer(id)) });
    } catch {
      return null;
    }
    const parts = [];
    const seen = new Set();
    for (const feature of features) {
      const tile = feature._vectorTileFeature;
      if (!tile || !Number.isInteger(feature._z)) continue;
      const mark = `${feature._z}/${feature._x}/${feature._y}/${feature.sourceLayer}/${feature.id ?? ""}/${tile.type}/${JSON.stringify(feature.properties)}`;
      if (seen.has(mark)) continue;  // the same tile feature drawn by several style layers
      seen.add(mark);
      parts.push(this.part(feature, tile));
    }
    const hit = nearest(point, parts, radius);
    return hit ? { lngLat: worldToLngLat(hit.world), kind: hit.kind } : null;
  }

  // Screen-space vertices and segments of a tile feature, inside its tile's own square.
  part(feature, tile) {
    const n = 2 ** feature._z, extent = tile.extent;
    const square = [0, 0, extent, extent];
    const toWorld = ([x, y]) => [(feature._x + x / extent) / n, (feature._y + y / extent) / n];
    const project = (world) => {
      const p = this.map.project(worldToLngLat(world));
      return [p.x, p.y];
    };
    const vertices = [], segments = [];
    const inside = ([x, y]) => x >= 0 && x <= extent && y >= 0 && y <= extent;
    for (const ring of tile.loadGeometry()) {
      const points = ring.map((p) => [p.x, p.y]);
      for (const p of points) {
        // A vertex on the tile's edge may be a cut, not a corner of the polygon.
        if (inside(p) && p[0] > 0 && p[0] < extent && p[1] > 0 && p[1] < extent) {
          const world = toWorld(p);
          vertices.push([...project(world), world]);
        }
      }
      if (tile.type === 1) continue;  // points: vertices only
      for (let i = 0; i + 1 < points.length; i++) {
        const clipped = clipSegment(points[i], points[i + 1], square);
        if (!clipped) continue;
        const [a, b] = clipped;
        // Along the tile's edge: a cut (a real edge there is in the neighbour too).
        if ((a[0] === b[0] && (a[0] === 0 || a[0] === extent)) || (a[1] === b[1] && (a[1] === 0 || a[1] === extent))) continue;
        const wa = toWorld(a), wb = toWorld(b);
        segments.push([project(wa), project(wb), wa, wb]);
      }
    }
    return { vertices, segments };
  }
}
