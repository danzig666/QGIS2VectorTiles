// Polygon labels on the *visible part* of their polygon, as QGIS draws them
// with "Centroid: visible polygon" (QgsPalLayerSettings.centroidWhole off,
// the QGIS default).
//
// The exported style draws such labels at the centroid of the whole polygon
// (static points, for any client). Their style layers carry
//   metadata["q2vt:visible-polygons"] = <source layer with the polygons>
// and this module moves them to a GeoJSON source that is rebuilt whenever
// the map stops moving: each polygon's tile pieces are clipped to their own
// tile (tiles overlap by a buffer) and to the screen, and the label goes to
// the centroid of the visible area when that lies inside the polygon, else to
// an interior point (the GEOS / QGIS rule). A label stays where it is while
// that point is still on the visible part of its polygon (and not at the
// screen edge), so panning does not make labels jump.
//
// Labels QGIS may overlap "if required" (or "at no cost") carry
//   metadata["q2vt:overlap"] = "if-required"
// and avoid other labels in the style; enableOverlapFallback() draws the
// ones MapLibre could not place without overlap anyway, as QGIS does.

const SOURCE_PREFIX = "q2vt_visible_";
export const LOADER_PREFIX = "q2vt_visible_loader_";
export const OVERLAP_SUFFIX = "_q2vt_overlap";
const EDGE_PX = 24; // a kept label must stay this far inside the screen

// options (all optional):
//   eligible(properties, polygonSourceLayer) -> boolean: only these polygons
//     get a label (layer/rule toggles, attribute filters);
//   onUnsupported(reason): MapLibre no longer exposes the tile fields this
//     helper reads (_x/_y/_z/_vectorTileFeature) - labels stay static.
// Returns {update, sync, groups, setEligibility, pause, resume, destroy}.
export function enableVisibleLabels(map, maplibregl, sourceId = "q2vt_tiles", options = {}) {
  const groups = new Map(); // polygon source layer -> {source, layers: []}
  const style = map.getStyle();
  const tileSource = style.sources[sourceId] || {};
  style.layers.forEach((layer, index) => {
    const polygons = layer.metadata && layer.metadata["q2vt:visible-polygons"];
    if (!polygons || layer.source !== sourceId) return;
    if (!groups.has(polygons)) groups.set(polygons, { source: SOURCE_PREFIX + polygons, layers: [] });
    const before = style.layers.slice(index + 1).find((l) => !(l.metadata && l.metadata["q2vt:visible-polygons"]));
    groups.get(polygons).layers.push({ def: layer, before: before ? before.id : undefined });
  });
  const noop = () => {};
  if (!groups.size) {
    const overlap = enableOverlapFallback(map);
    return { update: noop, sync: overlap.sync, groups, setEligibility: noop, pause: noop,
             resume: noop, destroy: overlap.destroy };
  }

  const firstLayer = style.layers.length ? style.layers[0].id : undefined;
  for (const [polygons, group] of groups) {
    // MapLibre loads a source's tiles only for visible layers: an invisible
    // fill keeps the polygons loaded whatever else is shown.
    const zooms = group.layers.map(({ def }) => [def.minzoom ?? 0, def.maxzoom ?? 24]);
    map.addLayer({
      id: LOADER_PREFIX + polygons, type: "fill", source: sourceId, "source-layer": polygons,
      minzoom: Math.min(...zooms.map((z) => z[0])), maxzoom: Math.max(...zooms.map((z) => z[1])),
      paint: { "fill-opacity": 0 },
    }, firstLayer);
    map.addSource(group.source, {
      type: "geojson", data: { type: "FeatureCollection", features: [] },
      maxzoom: tileSource.maxzoom === undefined ? 18 : tileSource.maxzoom,
    });
    for (const { def } of group.layers) map.removeLayer(def.id);
  }
  // Re-add in style order, each before the first following unmoved layer.
  for (const group of groups.values()) {
    for (const { def, before } of group.layers) {
      const moved = { ...def, source: group.source };
      delete moved["source-layer"];
      map.addLayer(moved, before && map.getLayer(before) ? before : undefined);
    }
  }

  // After the moves: the fallback copies follow the moved layers.
  const overlap = enableOverlapFallback(map);

  let pending = null;
  let paused = false;
  let unsupported = false;
  let eligible = options.eligible || null;
  const states = new Map(); // polygon source layer -> label positions kept
  const signatures = new Map(); // polygon source layer -> last data written
  const update = () => {
    pending = null;
    if (paused || unsupported) return;
    const view = viewRect(map, maplibregl);
    const margin = EDGE_PX * (view[2] - view[0]) / Math.max(1, map.getContainer().clientWidth);
    for (const [polygons, group] of groups) {
      if (!states.has(polygons)) states.set(polygons, newLabelState());
      const features = map.querySourceFeatures(sourceId, { sourceLayer: polygons });
      if (features.length && !features.some((f) => f._vectorTileFeature && Number.isInteger(f._z))) {
        unsupported = true;  // private tile fields gone (MapLibre upgrade)
        if (options.onUnsupported) options.onUnsupported("tile feature fields unavailable");
        return;
      }
      const data = labelPoints(features, view, maplibregl, {
        state: states.get(polygons), margin,
        eligible: eligible ? (properties) => eligible(properties, polygons) : null,
      });
      // Unchanged labels: no setData (it would trigger another idle ->
      // update round trip forever).
      const signature = JSON.stringify(data.features.map((f) => [f.id, f.geometry.coordinates]));
      if (signatures.get(polygons) === signature) continue;
      signatures.set(polygons, signature);
      map.getSource(group.source).setData(data);
    }
  };
  const schedule = () => { if (!pending && !paused) pending = setTimeout(update, 60); };
  const onSourceData = (e) => { if (e.sourceId === sourceId && e.isSourceLoaded) schedule(); };
  map.on("moveend", schedule);
  map.on("sourcedata", onSourceData);
  map.on("idle", schedule);
  update();
  return {
    update, sync: overlap.sync, groups,
    // New eligibility (toggles/filters): positions of polygons that stay
    // eligible are kept; the others lose their label at the next update.
    setEligibility(fn) { eligible = fn || null; signatures.clear(); schedule(); },
    pause() { paused = true; if (pending) { clearTimeout(pending); pending = null; } },
    resume() { paused = false; signatures.clear(); schedule(); },
    destroy() {
      paused = true;
      if (pending) clearTimeout(pending);
      pending = null;
      map.off("moveend", schedule);
      map.off("sourcedata", onSourceData);
      map.off("idle", schedule);
      overlap.destroy();
      for (const group of groups.values()) {
        const source = map.getSource(group.source);
        if (source) source.setData({ type: "FeatureCollection", features: [] });
      }
    },
  };
}

// Label positions kept between updates, and stable feature ids (for the
// overlap fallback's feature state).
export function newLabelState() {
  return { points: new Map(), ids: new Map(), nextId: 1 };
}

// The screen as a world rectangle ([0, 1] Web Mercator, y down).
function viewRect(map, maplibregl) {
  const bounds = map.getBounds();
  const a = maplibregl.MercatorCoordinate.fromLngLat(bounds.getSouthWest());
  const b = maplibregl.MercatorCoordinate.fromLngLat(bounds.getNorthEast());
  return [Math.min(a.x, b.x), Math.min(a.y, b.y), Math.max(a.x, b.x), Math.max(a.y, b.y)];
}

// options.state (newLabelState()): keep each label where it was while that
// point is still on the visible part of its polygon and options.margin
// (world units) inside the screen; options.margin defaults to 0.
// options.eligible(properties): polygons that may have a label (toggles,
// filters); the others lose their label even where tiles are still loading.
export function labelPoints(features, view, maplibregl, options = {}) {
  const state = options.state || newLabelState();
  const margin = options.margin || 0;
  const inner = [view[0] + margin, view[1] + margin, view[2] - margin, view[3] - margin];
  // Only the deepest tiles: parent tiles shown while children load would
  // count the same area twice.
  let deepest = -1;
  for (const f of features) if (f._z > deepest) deepest = f._z;
  const loaded = new Set(); // deepest tiles with data
  const byFeature = new Map();
  const excluded = new Set();
  for (const f of features) {
    if (f._z !== deepest || !f._vectorTileFeature) continue;
    loaded.add(`${f._x}/${f._y}`);
    if (options.eligible && !options.eligible(f.properties)) {
      excluded.add(String(f.properties.q2vt_orig_id ?? f.id ?? JSON.stringify(f.properties)));
      continue;
    }
    const key = String(f.properties.q2vt_orig_id ?? f.id ?? JSON.stringify(f.properties));
    const tile = f._vectorTileFeature;
    const scale = 1 / (tile.extent * 2 ** f._z);
    const ox = f._x / 2 ** f._z, oy = f._y / 2 ** f._z;
    const clip = intersect(view, [ox, oy, ox + 1 / 2 ** f._z, oy + 1 / 2 ** f._z]);
    if (!clip) continue;
    const rings = [];
    for (const ring of tile.loadGeometry()) {
      const world = ring.map((p) => [ox + p.x * scale, oy + p.y * scale]);
      const clipped = clipRing(world, clip);
      if (clipped.length >= 3) rings.push(clipped);
    }
    if (!rings.length) continue;
    // A plain copy: tile feature properties have no prototype, which
    // MapLibre's worker serializer rejects ("_classRegistryKey").
    if (!byFeature.has(key)) byFeature.set(key, { properties: { ...f.properties }, rings: [] });
    byFeature.get(key).rings.push(...rings);
  }
  // A kept point on a tile still loading cannot be checked: keep it.
  const unchecked = (point) => !loaded.has(
    `${Math.floor(point[0] * 2 ** deepest)}/${Math.floor(point[1] * 2 ** deepest)}`);
  const points = new Map();
  for (const [key, { properties, rings }] of byFeature) {
    const old = state.points.get(key);
    const keep = old && within(inner, old.point) && (unchecked(old.point) || inside(rings, old.point));
    const point = keep ? old.point : labelPoint(rings);
    if (point) points.set(key, { point, properties });
  }
  for (const [key, old] of state.points) {
    if (!points.has(key) && !excluded.has(key) && within(inner, old.point) && unchecked(old.point)
        && (!options.eligible || options.eligible(old.properties))) points.set(key, old);
  }
  state.points = points;
  const out = [];
  for (const [key, { point, properties }] of points) {
    if (!state.ids.has(key)) state.ids.set(key, state.nextId++);
    const lngLat = new maplibregl.MercatorCoordinate(point[0], point[1], 0).toLngLat();
    out.push({ type: "Feature", id: state.ids.get(key), properties,
               geometry: { type: "Point", coordinates: [lngLat.lng, lngLat.lat] } });
  }
  return { type: "FeatureCollection", features: out };
}

function within([x0, y0, x1, y1], [x, y]) {
  return x >= x0 && x <= x1 && y >= y0 && y <= y1;
}

function intersect(a, b) {
  const r = [Math.max(a[0], b[0]), Math.max(a[1], b[1]), Math.min(a[2], b[2]), Math.min(a[3], b[3])];
  return r[0] < r[2] && r[1] < r[3] ? r : null;
}

// Sutherland–Hodgman against an axis-aligned rectangle.
function clipRing(ring, [x0, y0, x1, y1]) {
  const edges = [
    (p) => p[0] >= x0, (p) => p[0] <= x1, (p) => p[1] >= y0, (p) => p[1] <= y1,
  ];
  const cut = [
    (a, b) => [x0, a[1] + (b[1] - a[1]) * (x0 - a[0]) / (b[0] - a[0])],
    (a, b) => [x1, a[1] + (b[1] - a[1]) * (x1 - a[0]) / (b[0] - a[0])],
    (a, b) => [a[0] + (b[0] - a[0]) * (y0 - a[1]) / (b[1] - a[1]), y0],
    (a, b) => [a[0] + (b[0] - a[0]) * (y1 - a[1]) / (b[1] - a[1]), y1],
  ];
  let points = ring;
  for (let e = 0; e < 4 && points.length; e++) {
    const inside = edges[e], next = [];
    for (let i = 0; i < points.length; i++) {
      const cur = points[i], prev = points[(i + points.length - 1) % points.length];
      if (inside(cur)) {
        if (!inside(prev)) next.push(cut[e](prev, cur));
        next.push(cur);
      } else if (inside(prev)) {
        next.push(cut[e](prev, cur));
      }
    }
    points = next;
  }
  return points;
}

// Centroid of the visible area if inside it (signed ring areas: holes
// subtract), else an interior point: the middle of the widest inside span on
// the horizontal line through the middle of the extent (GEOS InteriorPoint).
export function labelPoint(rings) {
  let area = 0, cx = 0, cy = 0;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const ring of rings) {
    for (let i = 0, n = ring.length; i < n; i++) {
      const [x1, y1] = ring[i], [x2, y2] = ring[(i + 1) % n];
      const cross = x1 * y2 - x2 * y1;
      area += cross; cx += (x1 + x2) * cross; cy += (y1 + y2) * cross;
      if (x1 < minX) minX = x1; if (x1 > maxX) maxX = x1;
      if (y1 < minY) minY = y1; if (y1 > maxY) maxY = y1;
    }
  }
  if (Math.abs(area) < 1e-22) return null;
  const centroid = [cx / (3 * area), cy / (3 * area)];
  if (inside(rings, centroid)) return centroid;
  const y = (minY + maxY) / 2, xs = [];
  for (const ring of rings) {
    for (let i = 0, n = ring.length; i < n; i++) {
      const [x1, y1] = ring[i], [x2, y2] = ring[(i + 1) % n];
      if ((y1 > y) !== (y2 > y)) xs.push(x1 + (y - y1) * (x2 - x1) / (y2 - y1));
    }
  }
  xs.sort((a, b) => a - b);
  let best = null;
  for (let i = 0; i + 1 < xs.length; i += 2) {
    if (!best || xs[i + 1] - xs[i] > best[1] - best[0]) best = [xs[i], xs[i + 1]];
  }
  return best ? [(best[0] + best[1]) / 2, y] : centroid;
}

function inside(rings, [x, y]) {  // even-odd over every ring
  let result = false;
  for (const ring of rings) {
    for (let i = 0, n = ring.length, j = n - 1; i < n; j = i++) {
      const [xi, yi] = ring[i], [xj, yj] = ring[j];
      if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) result = !result;
    }
  }
  return result;
}

// "Overlap if required": each label layer marked q2vt:overlap avoids other
// labels; a copy below it (overlap allowed, never blocking) draws the labels
// MapLibre could not place, found after each placement with
// queryRenderedFeatures (placed symbols only) and marked in feature state.
export function enableOverlapFallback(map) {
  const primaries = [];
  for (const layer of map.getStyle().layers) {
    if (layer.type !== "symbol" || !(layer.metadata && layer.metadata["q2vt:overlap"] === "if-required")) continue;
    const copy = JSON.parse(JSON.stringify(layer));
    copy.id = layer.id + OVERLAP_SUFFIX;
    copy.layout = { ...copy.layout, "text-allow-overlap": true, "icon-allow-overlap": true,
                    "text-ignore-placement": true, "icon-ignore-placement": true };
    const anchors = copy.layout["text-variable-anchor"];
    if (anchors) {  // the first (preferred) position
      copy.layout["text-anchor"] = anchors[0];
      delete copy.layout["text-variable-anchor"];
      delete copy.layout["text-radial-offset"];
    }
    copy.paint = { ...copy.paint };
    for (const name of ["text-opacity", "icon-opacity"]) {
      copy.paint[name] = unlessPlaced(copy.paint[name] === undefined ? 1 : copy.paint[name]);
    }
    map.addLayer(copy, layer.id);
    primaries.push(layer.id);
  }
  let marked = new Map();
  const sync = () => {
    const layers = primaries.filter((id) => map.getLayer(id));
    const placed = new Map();
    if (layers.length) {
      for (const f of map.queryRenderedFeatures({ layers })) {
        if (f.id === undefined || f.id === null) continue;
        const target = f.sourceLayer ? { source: f.source, sourceLayer: f.sourceLayer, id: f.id }
          : { source: f.source, id: f.id };
        placed.set(`${f.source}\u0000${f.sourceLayer || ""}\u0000${f.id}`, target);
      }
    }
    for (const [key, target] of marked) {
      if (!placed.has(key)) map.setFeatureState(target, { q2vtPlaced: false });
    }
    for (const [key, target] of placed) {
      if (!marked.has(key)) map.setFeatureState(target, { q2vtPlaced: true });
    }
    marked = placed;
  };
  let last = 0, pending = null;
  const throttled = () => {
    if (pending) return;
    const wait = Math.max(0, 150 - (Date.now() - last));
    pending = setTimeout(() => { pending = null; last = Date.now(); sync(); }, wait);
  };
  if (primaries.length) {
    map.on("render", throttled);
    map.on("idle", sync);
  }
  const destroy = () => {
    if (pending) clearTimeout(pending);
    pending = null;
    map.off("render", throttled);
    map.off("idle", sync);
  };
  return { sync, layers: primaries, destroy };
}

// opacity -> 0 where the label was placed by its primary layer; zoom curves
// keep ["zoom"] at the top (a MapLibre rule).
function unlessPlaced(value) {
  const placed = ["boolean", ["feature-state", "q2vtPlaced"], false];
  const wrap = (v) => ["case", placed, 0, v];
  if (Array.isArray(value) && (value[0] === "interpolate" || value[0] === "step")) {
    const input = value[0] === "interpolate" ? 2 : 1;
    if (Array.isArray(value[input]) && value[input][0] === "zoom") {
      const out = value.slice();
      for (let i = input + 2; i < out.length; i += 2) out[i] = wrap(out[i]);  // the outputs
      return out;
    }
  }
  if (value && typeof value === "object" && !Array.isArray(value)) return value;  // legacy function: leave
  return wrap(value);
}
