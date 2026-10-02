// Polygon labels on the *visible part* of their polygon, as QGIS draws them
// with "Centroid: visible polygon" (QgsPalLayerSettings.centroidWhole off,
// the QGIS default).
//
// The exported style draws such labels at the centroid of the whole polygon
// (static points, for any client). Their style layers carry
//   metadata["q2vt:visible-polygons"] = <source layer with the polygons>
// and this module moves them to a GeoJSON source that is rebuilt as the
// polygon tiles arrive and while the map moves (throttled; see the
// *_INTERVAL_MS constants), not only when everything has loaded: each
// polygon's tile pieces are clipped to their own
// tile (tiles overlap by a buffer) and to the screen, and the label goes to
// the centroid of the visible area when that lies inside the polygon, else to
// an interior point (the GEOS / QGIS rule). A label stays where it is while
// that point is still on the visible part of its polygon (and not at the
// screen edge), so panning does not make labels jump. Labels whose QGIS
// placement is Horizontal or Free (metadata["q2vt:label-anchor"] = "pole")
// go where they have the most room instead: the pole of inaccessibility of
// the visible part (edges where tiles cut the polygon do not count), as
// QGIS ranks its candidates; a kept label must still have most of that room
// and a kept centroid label must stay near the centroid, so after a pan
// labels sit in the middle of what is visible, not at an old edge spot.
// A label is only shown where its whole box fits on the screen (QGIS
// drops candidates outside the map extent), and only when the visible part
// of its polygon is at least as large as the label's box: a polygon with a
// sliver in view gets no label at the edge. Groups are placed by QGIS label priority
// and z-index (metadata["q2vt:label-rank"]); later ones keep clear of the
// boxes already placed (a zone code and the parcel number of the same
// parcel do not compete for one point).
// While the map moves
// (drag, zoom animation) placed labels do not move at all - only polygons
// without a label get one - and the rule above is applied once the map
// stops: labels stay glued to the map like the other labels. Polygons in the
// loaded tiles just outside the screen get their label in advance (the
// centroid of what is loaded of them), so panning reveals labels that are
// already placed instead of waiting for a new round.
//
// Labels QGIS may overlap "if required" (or "at no cost") carry
//   metadata["q2vt:overlap"] = "if-required"
// and avoid other labels in the style; enableOverlapFallback() draws the
// ones MapLibre could not place without overlap anyway, as QGIS does.

const SOURCE_PREFIX = "q2vt_visible_";
export const LOADER_PREFIX = "q2vt_visible_loader_";
export const OVERLAP_SUFFIX = "_q2vt_overlap";
const EDGE_PX = 24; // a kept label must stay this far inside the screen
// Recompute at most this often while polygon tiles arrive / the map moves.
// Unchanged labels keep their fade state across the GeoJSON reloads
// (MapLibre matches them by tile and position), so this does not flicker.
const TILE_INTERVAL_MS = 80;
const MOVE_INTERVAL_MS = 150;
const REACH = 0.5;   // labels in advance up to half a screen beyond each edge
const GUARD_PX = 48; // ... but not so close that their text reaches the screen
// Label point tiles stop at this zoom (then overzoomed): bigger tiles cover
// more around the screen, so panning rarely needs new ones laid out. Points
// keep 1/8192 of a z15 tile (about 0.1 m at mid latitudes).
const POINT_MAXZOOM = 15;
const KEEP_ROOM = 0.8;  // a kept "pole" label keeps >= 80 % of the best room
const KEEP_PX = 24;     // a kept centroid label stays this close to the centroid

// options (all optional):
//   eligible(properties, polygonSourceLayer) -> boolean: only these polygons
//     get a label (layer/rule toggles, attribute filters);
//   onUnsupported(reason): MapLibre no longer exposes the tile fields this
//     helper reads (_x/_y/_z/_vectorTileFeature) - labels stay static.
// Returns {update, sync, groups, snapshot, setEligibility, pause, resume,
// destroy}; snapshot() maps each polygon source layer to the label points
// (GeoJSON features) last written.
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
    if (layer.metadata["q2vt:label-anchor"] === "pole") groups.get(polygons).anchor = "pole";
    const rank = layer.metadata["q2vt:label-rank"];
    if (Array.isArray(rank)) {
      const old = groups.get(polygons).rank || [-Infinity, -Infinity];
      if (rank[0] > old[0] || (rank[0] === old[0] && rank[1] > old[1])) groups.get(polygons).rank = rank;
    }
    groups.get(polygons).order = index;
  });
  const noop = () => {};
  if (!groups.size) {
    const overlap = enableOverlapFallback(map);
    return { update: noop, sync: overlap.sync, groups, snapshot: () => new Map(), setEligibility: noop,
             pause: noop, resume: noop, destroy: overlap.destroy };
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
      maxzoom: Math.min(tileSource.maxzoom ?? POINT_MAXZOOM, POINT_MAXZOOM),
    });
    for (const { def } of group.layers) map.removeLayer(def.id);
  }
  // Re-add in style order, each before the first following unmoved layer.
  for (const group of groups.values()) {
    for (const { def, before } of group.layers) {
      const moved = { ...def, source: group.source };
      delete moved["source-layer"];
      // The position is computed here (room, screen fit, other labels):
      // MapLibre must not shift the label a box width off it (that pushed
      // labels out of their polygon and off the screen).
      moved.layout = { ...moved.layout, "text-anchor": "center" };
      delete moved.layout["text-variable-anchor"];
      delete moved.layout["text-radial-offset"];
      map.addLayer(moved, before && map.getLayer(before) ? before : undefined);
    }
  }

  // After the moves: the fallback copies follow the moved layers.
  const overlap = enableOverlapFallback(map);

  let pending = null;
  let last = -Infinity;
  let paused = false;
  let unsupported = false;
  let eligible = options.eligible || null;
  const states = new Map(); // polygon source layer -> label positions kept
  const written = new Map(); // polygon source layer -> Map(id -> [x, y, properties json])
  const latest = new Map(); // polygon source layer -> features last written
  const update = () => {
    if (pending) clearTimeout(pending);
    pending = null;
    if (paused || unsupported) return;
    last = performance.now();
    const view = viewRect(map, maplibregl);
    const perPx = (view[2] - view[0]) / Math.max(1, map.getContainer().clientWidth);
    const margin = EDGE_PX * perPx;
    const reach = grow(view, REACH * (view[2] - view[0]), REACH * (view[3] - view[1]));
    // The tile level MapLibre covers the view with (vector sources: floor).
    const tileZoom = Math.max(tileSource.minzoom ?? 0, Math.min(tileSource.maxzoom ?? 22,
      Math.floor(map.getZoom() + Math.log2(512 / (tileSource.tileSize || 512)))));
    // Best spots first: higher QGIS priority, then z-index, then on top in the style.
    const ranked = [...groups].sort(([, a], [, b]) => {
      const ra = a.rank || [5, 0], rb = b.rank || [5, 0];
      return (rb[0] - ra[0]) || (rb[1] - ra[1]) || ((b.order ?? 0) - (a.order ?? 0));
    });
    const placed = [];  // world boxes of the labels placed so far
    const zoom = map.getZoom();
    for (const [polygons, group] of ranked) {
      if (!states.has(polygons)) states.set(polygons, newLabelState());
      const features = map.querySourceFeatures(sourceId, { sourceLayer: polygons });
      if (features.length && !features.some((f) => f._vectorTileFeature && Number.isInteger(f._z))) {
        unsupported = true;  // private tile fields gone (MapLibre upgrade)
        if (options.onUnsupported) options.onUnsupported("tile feature fields unavailable");
        return;
      }
      const data = labelPoints(features, view, maplibregl, {
        state: states.get(polygons), margin, reach, tileZoom, guard: GUARD_PX * perPx,
        anchor: group.anchor || "centroid", precision: perPx, keepDistance: KEEP_PX * perPx,
        // Moving, or tiles still arriving (the visible part is not complete):
        // placed labels stay; the final position once everything is there.
        freeze: map.isMoving() || !map.isSourceLoaded(sourceId),
        labelBox: labelBoxes(map, group, zoom, perPx), avoid: placed.slice(),
        eligible: eligible ? (properties) => eligible(properties, polygons) : null,
      });
      write(map.getSource(group.source), data, polygons);
      if (data.boxes && groupShown(map, group, zoom)) placed.push(...data.boxes);
    }
  };
  // Only what changed goes to the source: an incremental update reloads just
  // the label tiles around the added, moved or removed points (setData
  // reloads all). Nothing changed: nothing written (else idle -> update
  // would loop forever).
  const write = (source, data, polygons) => {
    const before = written.get(polygons);
    const now = new Map(data.features.map((f) => [f.id, [...f.geometry.coordinates, JSON.stringify(f.properties)]]));
    written.set(polygons, now);
    latest.set(polygons, data.features);
    if (!before || typeof source.updateData !== "function") {
      if (before || data.features.length) source.setData(data);
      return;
    }
    const diff = { remove: [], add: [], update: [] };
    for (const id of before.keys()) if (!now.has(id)) diff.remove.push(id);
    for (const f of data.features) {
      const old = before.get(f.id);
      if (!old || old[2] !== now.get(f.id)[2]) {
        if (old) diff.remove.push(f.id);
        diff.add.push(f);
      } else if (old[0] !== f.geometry.coordinates[0] || old[1] !== f.geometry.coordinates[1]) {
        diff.update.push({ id: f.id, newGeometry: f.geometry });
      }
    }
    if (diff.remove.length || diff.add.length || diff.update.length) source.updateData(diff);
  };
  // Throttled, not debounced: the first change is handled at once, later
  // ones at most every `interval` ms, so labels follow tiles and moves.
  const throttled = (interval) => () => {
    if (pending || paused || unsupported) return;
    pending = setTimeout(update, Math.max(0, last + interval - performance.now()));
  };
  const schedule = throttled(0);
  const onTile = throttled(TILE_INTERVAL_MS);
  const onMove = throttled(MOVE_INTERVAL_MS);
  // Each polygon tile as it arrives (not only once the whole source loaded).
  const onSourceData = (e) => { if (e.sourceId === sourceId && (e.tile || e.isSourceLoaded)) onTile(); };
  const onMoveEnd = () => { if (pending) { clearTimeout(pending); pending = null; } schedule(); };
  map.on("move", onMove);
  map.on("moveend", onMoveEnd);
  map.on("sourcedata", onSourceData);
  map.on("idle", schedule);
  update();
  return {
    update, sync: overlap.sync, groups,
    snapshot: () => new Map(latest),
    // New eligibility (toggles/filters): positions of polygons that stay
    // eligible are kept; the others lose their label at the next update.
    setEligibility(fn) { eligible = fn || null; schedule(); },
    pause() { paused = true; if (pending) { clearTimeout(pending); pending = null; } },
    resume() { paused = false; schedule(); },
    destroy() {
      paused = true;
      if (pending) clearTimeout(pending);
      pending = null;
      map.off("move", onMove);
      map.off("moveend", onMoveEnd);
      map.off("sourcedata", onSourceData);
      map.off("idle", schedule);
      overlap.destroy();
      for (const group of groups.values()) {
        const source = map.getSource(group.source);
        if (source) source.setData({ type: "FeatureCollection", features: [] });
        written.clear();
        latest.clear();
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
// options.reach (world rectangle around view): polygons there that are not
// on the screen get the label point of what is loaded of them in advance,
// unless it lies within options.guard (world units) of the screen.
// options.freeze (the map is moving): labels already placed stay where they
// are; only polygons without one get a label.
export function labelPoints(features, view, maplibregl, options = {}) {
  const state = options.state || newLabelState();
  const margin = options.margin || 0;
  const inner = grow(view, -margin, -margin);
  const reach = options.reach || view;
  const guarded = grow(view, options.guard || 0, options.guard || 0);
  // One tile level only: parent tiles shown while children load (or
  // children kept while parents load) would count the same area twice. The
  // level the map wants (options.tileZoom) as soon as it has data, else the
  // deepest loaded: zooming out, the new tiles are used before the old,
  // deeper ones are dropped.
  let deepest = -1;
  let wanted = false;
  for (const f of features) {
    if (f._z > deepest) deepest = f._z;
    if (f._z === options.tileZoom) wanted = true;
  }
  if (wanted) deepest = options.tileZoom;
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
    const clip = intersect(reach, [ox, oy, ox + 1 / 2 ** f._z, oy + 1 / 2 ** f._z]);
    if (!clip) continue;
    const rings = [];
    for (const ring of tile.loadGeometry()) {
      const world = ring.map((p) => [ox + p.x * scale, oy + p.y * scale]);
      const clipped = clipRing(world, clip);
      clipped.cut = clip;  // edges along it are tile cuts, not polygon edges
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
  const avoid = (options.avoid || []).slice();
  const boxes = [];
  // Bigger polygons first: they get the middle, smaller neighbours keep clear.
  const area = (rings) => Math.abs(rings.reduce((sum, ring) => sum + ring.reduce((a, [x1, y1], i) => {
    const [x2, y2] = ring[(i + 1) % ring.length];
    return a + x1 * y2 - x2 * y1;
  }, 0), 0));
  const ordered = [...byFeature].map(([key, value]) => [key, value, area(value.rings)])
    .sort((a, b) => b[2] - a[2]);
  for (const [key, { properties, rings: loaded }] of ordered) {
    const old = state.points.get(key);
    if (old && options.freeze) {
      points.set(key, { point: old.point, properties });
      continue;
    }
    const rings = reach === view ? loaded : loaded.map((ring) => {
      const clipped = clipRing(ring, view);
      clipped.cut = ring.cut;
      return clipped;
    }).filter((ring) => ring.length >= 3);
    const pole = options.anchor === "pole";
    if (!rings.length) {  // off the screen: in advance, away from the screen edge
      const point = pole ? roomiestPoint(loaded, options.precision).point : labelPoint(loaded);
      if (point && !within(guarded, point)) points.set(key, { point, properties });
      continue;
    }
    // The label's box (half width/height, world units): it must fit on the
    // screen and should keep clear of the boxes of better-ranked labels.
    const half = options.labelBox ? options.labelBox(properties) : null;
    const fit = (x, y) => (half ? Math.min(x - view[0] - half[0], view[2] - half[0] - x,
                                           y - view[1] - half[1], view[3] - half[1] - y) : Infinity);
    // Only boxes that can matter: within a label's size of the visible part.
    let near = [];
    if (half && avoid.length) {
      const extent = bounds(rings);
      const reachBox = grow(extent, 2 * half[0], 2 * half[1]);
      near = avoid.filter((box) => intersect(box, reachBox)).map((box) => grow(box, half[0], half[1]));
    }
    const clear = (x, y) => {
      let min = Infinity;
      for (const box of near) min = Math.min(min, rectDistance(x, y, box));
      return min;
    };
    if (half && area(rings) < 4 * half[0] * half[1]) continue;  // only a sliver in view
    let point;
    if (pole) {
      const room = (extra) => roomiestPoint(rings, options.precision, extra);
      let score = (x, y) => Math.min(fit(x, y), clear(x, y));
      let best = room(score);
      if (!(best.room > 0)) {  // no free spot: the best spot that fits (MapLibre decides)
        score = fit;
        best = room(score);
      }
      if (!(best.room > 0)) continue;  // only a sliver in view: no label (as QGIS)
      const keep = old && within(inner, old.point) && (unchecked(old.point)
        || Math.min(roomAt(rings, old.point), score(...old.point)) >= KEEP_ROOM * best.room);
      point = keep ? old.point : best.point;
    } else {
      const target = labelPoint(rings);
      if (!target || fit(...target) < 0) continue;
      const near = (p) => Math.hypot(p[0] - target[0], p[1] - target[1]) <= (options.keepDistance ?? Infinity);
      const keep = old && within(inner, old.point) && fit(...old.point) >= 0 && (unchecked(old.point)
        || (inside(rings, old.point) && near(old.point)));
      point = keep ? old.point : target;
    }
    if (half) {
      const box = [point[0] - half[0], point[1] - half[1], point[0] + half[0], point[1] + half[1]];
      boxes.push(box);
      avoid.push(box);
    }
    points.set(key, { point, properties });
  }
  for (const [key, old] of state.points) {
    if (!points.has(key) && !excluded.has(key) && (options.freeze || within(inner, old.point)) && unchecked(old.point)
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
  return { type: "FeatureCollection", features: out, boxes };
}

// Polygon edges of rings, without the edges along their tile cut.
function realEdges(rings) {
  const on = (v, w) => Math.abs(v - w) <= 1e-12;
  const edges = [];
  for (const ring of rings) {
    const cut = ring.cut;
    for (let i = 0, n = ring.length; i < n; i++) {
      const a = ring[i], b = ring[(i + 1) % n];
      if (cut && ((on(a[0], b[0]) && (on(a[0], cut[0]) || on(a[0], cut[2])))
          || (on(a[1], b[1]) && (on(a[1], cut[1]) || on(a[1], cut[3]))))) continue;
      edges.push([a, b]);
    }
  }
  return edges;
}

function segmentDistance(x, y, [ax, ay], [bx, by]) {
  let dx = bx - ax, dy = by - ay;
  if (dx !== 0 || dy !== 0) {
    const t = Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)));
    ax += dx * t;
    ay += dy * t;
  }
  return Math.hypot(x - ax, y - ay);
}

function signedRoom(rings, edges, x, y) {
  let min = Infinity;
  for (const edge of edges) min = Math.min(min, segmentDistance(x, y, edge[0], edge[1]));
  return inside(rings, [x, y]) ? min : -min;
}

// Room (distance to the nearest real polygon edge; negative outside) at a point.
export function roomAt(rings, point) {
  const edges = realEdges(rings);
  return edges.length ? signedRoom(rings, edges, point[0], point[1]) : (inside(rings, point) ? Infinity : -Infinity);
}

// The point with the most room inside the rings (pole of inaccessibility,
// grid refinement with a priority queue), to ``precision``. Edges along tile
// cuts (ring.cut) are not polygon edges. Returns {point, room}.
export function roomiestPoint(rings, precision = 0, extra = null) {
  const edges = realEdges(rings);
  const fallback = labelPoint(rings);
  if (!fallback) return { point: null, room: -Infinity };
  if (!edges.length) {
    return { point: fallback, room: extra ? extra(fallback[0], fallback[1]) : Infinity };
  }
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const ring of rings) {
    for (const [x, y] of ring) {
      if (x < minX) minX = x; if (x > maxX) maxX = x;
      if (y < minY) minY = y; if (y > maxY) maxY = y;
    }
  }
  const size = Math.min(maxX - minX, maxY - minY);
  if (!(size > 0)) {
    const room = roomAt(rings, fallback);
    return { point: fallback, room: extra ? Math.min(room, extra(fallback[0], fallback[1])) : room };
  }
  const cell = (x, y, h) => {
    const d = extra ? Math.min(signedRoom(rings, edges, x, y), extra(x, y)) : signedRoom(rings, edges, x, y);
    return { x, y, h, d, max: d + h * Math.SQRT2 };
  };
  const heap = [];
  const push = (c) => {
    heap.push(c);
    for (let i = heap.length - 1; i > 0;) {
      const parent = (i - 1) >> 1;
      if (heap[parent].max >= heap[i].max) break;
      [heap[parent], heap[i]] = [heap[i], heap[parent]];
      i = parent;
    }
  };
  const pop = () => {
    const top = heap[0], last = heap.pop();
    if (heap.length) {
      heap[0] = last;
      for (let i = 0; ;) {
        const l = 2 * i + 1, r = l + 1;
        let m = i;
        if (l < heap.length && heap[l].max > heap[m].max) m = l;
        if (r < heap.length && heap[r].max > heap[m].max) m = r;
        if (m === i) break;
        [heap[m], heap[i]] = [heap[i], heap[m]];
        i = m;
      }
    }
    return top;
  };
  const half = size / 2;
  for (let x = minX; x < maxX; x += size) {
    for (let y = minY; y < maxY; y += size) push(cell(x + half, y + half, half));
  }
  let best = cell(fallback[0], fallback[1], 0);
  const center = cell((minX + maxX) / 2, (minY + maxY) / 2, 0);
  if (center.d > best.d) best = center;
  const tolerance = Math.max(precision, size * 1e-4);
  for (let visited = 0; heap.length && visited < 600; visited++) {
    const c = pop();
    if (c.d > best.d) best = c;
    if (c.max - best.d <= tolerance) continue;
    const h = c.h / 2;
    push(cell(c.x - h, c.y - h, h));
    push(cell(c.x + h, c.y - h, h));
    push(cell(c.x - h, c.y + h, h));
    push(cell(c.x + h, c.y + h, h));
  }
  return { point: [best.x, best.y], room: best.d };
}

function bounds(rings) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const ring of rings) {
    for (const [x, y] of ring) {
      if (x < x0) x0 = x; if (x > x1) x1 = x;
      if (y < y0) y0 = y; if (y > y1) y1 = y;
    }
  }
  return [x0, y0, x1, y1];
}

// Signed distance from a point to a rectangle (negative inside).
function rectDistance(x, y, [x0, y0, x1, y1]) {
  const dx = Math.max(x0 - x, 0, x - x1), dy = Math.max(y0 - y, 0, y - y1);
  if (dx || dy) return Math.hypot(dx, dy);
  return -Math.min(x - x0, x1 - x, y - y0, y1 - y);
}

// A style expression evaluated at a zoom for a feature's properties (the
// subset the converter writes for sizes and paddings: numbers, literal,
// get, to-number, coalesce, + - * /, interpolate / step on zoom). Unknown
// forms give undefined.
export function evaluate(expression, zoom, properties = {}) {
  if (!Array.isArray(expression)) return expression;
  const [op, ...args] = expression;
  const ev = (value) => evaluate(value, zoom, properties);
  switch (op) {
    case "literal": return args[0];
    case "zoom": return zoom;
    case "get": return properties[args[0]];
    case "to-number": {
      for (const value of args) {
        const number = Number(ev(value));
        if (value !== null && Number.isFinite(number)) return number;
      }
      return 0;
    }
    case "coalesce": {
      for (const value of args) {
        const result = ev(value);
        if (result !== null && result !== undefined) return result;
      }
      return undefined;
    }
    case "+": return args.reduce((sum, value) => sum + Number(ev(value)), 0);
    case "*": return args.reduce((product, value) => product * Number(ev(value)), 1);
    case "-": return args.length === 1 ? -Number(ev(args[0])) : Number(ev(args[0])) - Number(ev(args[1]));
    case "/": return Number(ev(args[0])) / Number(ev(args[1]));
    case "step": {
      const input = Number(ev(args[0]));
      let output = ev(args[1]);
      for (let i = 2; i + 1 < args.length; i += 2) if (input >= args[i]) output = ev(args[i + 1]);
      return output;
    }
    case "interpolate": {
      const [type, input, ...stops] = args;
      const x = Number(ev(input));
      const base = Array.isArray(type) && type[0] === "exponential" ? Number(type[1]) : 1;
      if (x <= stops[0]) return ev(stops[1]);
      for (let i = 0; i + 3 < stops.length; i += 2) {
        const z0 = stops[i], z1 = stops[i + 2];
        if (x <= z1) {
          const t = base === 1 ? (x - z0) / (z1 - z0)
            : (base ** (x - z0) - 1) / (base ** (z1 - z0) - 1);
          const a = ev(stops[i + 1]), b = ev(stops[i + 3]);
          return Array.isArray(a) ? a.map((v, k) => v + (b[k] - v) * t) : a + (b - a) * t;
        }
      }
      return ev(stops[stops.length - 1]);
    }
    default: return undefined;
  }
}

// The style layer of a group drawn at this zoom.
function activeLayer(group, zoom) {
  return (group.layers.find(({ def }) => (def.minzoom ?? 0) <= zoom && zoom < (def.maxzoom ?? 25))
    || group.layers[0]).def;
}

function groupShown(map, group, zoom) {
  const def = activeLayer(group, zoom);
  return (def.minzoom ?? 0) <= zoom && zoom < (def.maxzoom ?? 25)
    && map.getLayer(def.id) && map.getLayoutProperty(def.id, "visibility") !== "none";
}

// properties -> [half width, half height] of a label's box in world units,
// estimated from its text, text size and background padding.
function labelBoxes(map, group, zoom, perPx) {
  const layout = activeLayer(group, zoom).layout || {};
  return (properties) => {
    const text = String(evaluate(layout["text-field"], zoom, properties) ?? "");
    if (!text) return null;
    let size = Number(evaluate(layout["text-size"] ?? 16, zoom, properties));
    if (!Number.isFinite(size) || size <= 0) size = 16;
    let pad = evaluate(layout["icon-text-fit-padding"], zoom, properties);
    pad = Array.isArray(pad) && pad.length === 4 && layout["icon-text-fit"] ? pad.map(Number) : [0, 0, 0, 0];
    const lines = text.split("\n");
    const width = Math.max(...lines.map((line) => line.length)) * 0.6 * size + pad[1] + pad[3];
    const height = lines.length * 1.2 * size + pad[0] + pad[2];
    return [(width / 2 + 2) * perPx, (height / 2 + 2) * perPx];
  };
}

function grow([x0, y0, x1, y1], dx, dy) {
  return [x0 - dx, y0 - dy, x1 + dx, y1 + dy];
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
