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
// Line labels drawn once per line (metadata["q2vt:visible-kind"] = "line",
// exported at the middle of the whole line) go to the middle of the longest
// visible stretch of their line, rotated along it, if the label fits along
// that stretch and on the screen (sliding along the line, or to a shorter
// visible stretch, to fit and to keep clear of other labels), as QGIS places
// line labels inside the extent; clear also of the point labels MapLibre
// draws itself (building numbers, names). No clear spot: no label.
// While the map moves
// (drag, zoom animation) placed labels do not move at all - only polygons
// without a label get one - and the rule above is applied once the map
// stops: labels stay glued to the map like the other labels. Polygons in the
// loaded tiles just outside the screen get their label in advance (the
// centroid of what is loaded of them), so panning reveals labels that are
// already placed instead of waiting for a new round.
//
// MapLibre draws these labels exactly where they are put (text-overlap
// "always"): the placement above keeps them clear of each other where it
// can, and a label with no free spot overlaps rather than disappearing -
// labels that never blink while the map moves matter more than a rare
// overlap. Other label layers QGIS may overlap "if required"
// (metadata["q2vt:overlap"]) use MapLibre's cooperative overlap
// (enableOverlapFallback).

const SOURCE_PREFIX = "q2vt_visible_";
export const LOADER_PREFIX = "q2vt_visible_loader_";
export const OVERLAP_SUFFIX = "_q2vt_overlap";
const FREE_ROTATION = "q2vt_free_rotation";
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
    if (layer.metadata["q2vt:visible-kind"] === "line") groups.get(polygons).kind = "line";
    if (layer.metadata["q2vt:label-orient"] === "free") groups.get(polygons).orient = "free";
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
      // Line labels are placed here clear of the other labels (by their
      // drawn size, as QGIS does). MapLibre's collision boxes take the text
      // size of the next whole zoom - up to twice the drawn size for map-unit
      // text - and hid most of them: drawn by the fallback copy instead.
      // The position and the overlaps are decided here (other labels,
      // MapLibre's own included): MapLibre draws the label where it is put.
      // Its own collision boxes (text size of the next whole zoom, up to
      // twice the drawn size) hid labels, and its re-placement while the
      // map moves made them blink.
      moved.layout["text-overlap"] = "always";
      if (moved.layout["icon-image"] !== undefined) moved.layout["icon-overlap"] = "always";
      delete moved.layout["text-allow-overlap"];
      delete moved.layout["icon-allow-overlap"];
      // Free (angled) placement: the angle is computed here per label.
      if (group.orient === "free") moved.layout["text-rotate"] = ["to-number", ["get", FREE_ROTATION], 0];
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
    // Boxes of the labels MapLibre draws itself (building numbers, names...):
    // line labels keep clear of them too. Only when a line group needs them.
    let otherBoxes = null;
    const others = () => (otherBoxes ??= renderedLabelBoxes(map, maplibregl, groups, zoom, perPx));
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
        labelBox: labelBoxes(map, group, zoom, perPx),
        avoid: group.kind === "line" ? placed.concat(others()) : placed.slice(),
        kind: group.kind || "polygon", orient: group.orient || "horizontal",
        rotationField: group.orient === "free" ? FREE_ROTATION : rotationField(group, zoom),
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
      if (options.kind === "line") {  // polylines, cut to the tile
        rings.push(...clipLine(world, clip));
        continue;
      }
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
  for (let [key, { properties, rings: loaded }] of ordered) {
    const old = state.points.get(key);
    if (old && options.freeze) {
      points.set(key, { point: old.point, properties: old.properties || properties });
      continue;
    }
    if (options.kind === "line") {
      const placed = placeOnLine(loaded, properties, old, view, guarded, avoid, options);
      if (placed) {
        points.set(key, placed);
        if (placed.box) {
          boxes.push(placed.box);
          avoid.push(placed.box);
        }
      }
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
    const label = options.labelBox ? options.labelBox(properties) : null;
    let half = label;
    const fit = (x, y) => (half ? Math.min(x - view[0] - half[0], view[2] - half[0] - x,
                                           y - view[1] - half[1], view[3] - half[1] - y) : Infinity);
    // Only boxes that can matter: within a label's size of the visible part.
    let near = [];
    const nearBoxes = () => {
      near = [];
      if (half && avoid.length) {
        const extent = bounds(rings);
        const reachBox = grow(extent, 2 * half[0], 2 * half[1]);
        near = avoid.filter((box) => intersect(box, reachBox)).map((box) => grow(box, half[0], half[1]));
      }
    };
    nearBoxes();
    const clear = (x, y) => {
      let min = Infinity;
      for (const box of near) min = Math.min(min, rectDistance(x, y, box));
      return min;
    };
    if (label && area(rings) < 4 * label[0] * label[1]) continue;  // only a sliver in view
    let point;
    if (pole) {
      const search = () => {
        const room = (extra) => roomiestPoint(rings, options.precision, extra);
        let score = (x, y) => Math.min(fit(x, y), clear(x, y));
        let best = room(score);
        if (!(best.room > 0)) {  // no free spot: the best spot that fits (overlapping)
          score = fit;
          best = room(score);
        }
        return { best, score };
      };
      let { best, score } = search();
      if (!(best.room > 0)) continue;  // only a sliver in view: no label (as QGIS)
      const free = options.orient === "free" && options.rotationField && label;
      if (free) {
        // Free (angled), as QGIS: horizontal where the label fits inside the
        // polygon, else turned along the polygon (a street name along its
        // street) - always wholly inside the polygon, else no label.
        const field = options.rotationField;
        const fine = (p) => Math.min(fit(...p), clear(...p)) >= 0;
        const flatOld = old && !Number(old.properties?.[field]) && within(inner, old.point)
          && fitsFlat(rings, old.point, roomAt(rings, old.point), label) && fine(old.point)
          && roomAt(rings, old.point) >= KEEP_ROOM * best.room;
        let placed = null;
        if (flatOld) placed = { point: old.point, angle: 0 };
        else if (fitsFlat(rings, best.point, best.room, label) && fit(...best.point) >= 0) {
          placed = { point: best.point, angle: 0 };
        } else {
          const angle = localDirection(loaded, best.point, label[0]);
          placed = placeTurned(rings, angle, label, view, avoid, best.point, old, field, options.precision);
        }
        if (!placed) continue;
        point = placed.point;
        half = envelope(label, placed.angle);
        properties = { ...properties, [field]: placed.angle * 180 / Math.PI };
      } else {
        const keep = old && within(inner, old.point) && (unchecked(old.point)
          || Math.min(roomAt(rings, old.point), score(...old.point)) >= KEEP_ROOM * best.room);
        point = keep ? old.point : best.point;
      }
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

// Half extents of the axis-aligned envelope of a box turned by ``angle`` (radians).
function envelope([hx, hy], angle) {
  const c = Math.abs(Math.cos(angle)), s = Math.abs(Math.sin(angle));
  return [c * hx + s * hy, s * hx + c * hy];
}

// Does a horizontal label box at ``point`` lie inside the polygon (corners
// inside, no polygon edge across it)? QGIS's Free placement keeps such a
// label horizontal.
function boxInside(rings, [x, y], [hx, hy]) {
  const box = [x - hx, y - hy, x + hx, y + hy];
  for (const corner of [[box[0], box[1]], [box[2], box[1]], [box[2], box[3]], [box[0], box[3]]]) {
    if (!inside(rings, corner)) return false;
  }
  return !realEdges(rings).some(([a, b]) => clipLine([a, b], box).length);
}

// A label turned by ``angle`` placed wholly inside the polygon (QGIS drops
// polygon label candidates that stick out), on the screen and clear of
// ``avoid``: candidate spots on a grid along the turned axes (anchored to
// the world, so the same spots after a pan); the best centred one wins (most
// room to the polygon's edges), then the one nearest ``target``. A kept spot
// at the same angle stays while it is still fine and nearly as centred.
// Returns {point, angle} or null.
function placeTurned(rings, angle, label, view, avoid, target, old, field, precision = 0) {
  const [hx, hy] = label;
  const c = Math.cos(angle), s = Math.sin(angle);
  const toFrame = ([x, y]) => [x * c + y * s, -x * s + y * c];
  const fromFrame = ([u, v]) => [u * c - v * s, u * s + v * c];
  const edges = realEdges(rings).map(([a, b]) => [toFrame(a), toFrame(b)]);
  const [ex, ey] = envelope(label, angle);
  // Does the turned label at p overlap an (axis-aligned) box? Separating axes.
  const overlaps = (p, [u, v], box) => {
    const [x, y] = p;
    const [ex2, ey2] = [ex, ey];
    if (x + ex2 <= box[0] || x - ex2 >= box[2] || y + ey2 <= box[1] || y - ey2 >= box[3]) return false;
    let us = Infinity, ue = -Infinity, vs = Infinity, ve = -Infinity;
    for (const corner of [[box[0], box[1]], [box[2], box[1]], [box[2], box[3]], [box[0], box[3]]]) {
      const [cu, cv] = toFrame(corner);
      us = Math.min(us, cu); ue = Math.max(ue, cu); vs = Math.min(vs, cv); ve = Math.max(ve, cv);
    }
    return !(ue <= u - hx || us >= u + hx || ve <= v - hy || vs >= v + hy);
  };
  const ok = (p, clearOf = avoid) => {
    const [x, y] = p;
    if (Math.min(x - view[0] - ex, view[2] - ex - x, y - view[1] - ey, view[3] - ey - y) < 0) return false;
    const [u, v] = toFrame(p);
    if (clearOf.some((other) => overlaps(p, [u, v], other))) return false;
    const rect = [u - hx, v - hy, u + hx, v + hy];
    for (const corner of [[rect[0], rect[1]], [rect[2], rect[1]], [rect[2], rect[3]], [rect[0], rect[3]]]) {
      if (!inside(rings, fromFrame(corner))) return false;
    }
    return !edges.some((edge) => clipLine(edge, rect).length);
  };
  let [u0, v0, u1, v1] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const ring of rings) {
    for (const point of ring) {
      const [u, v] = toFrame(point);
      if (u < u0) u0 = u; if (u > u1) u1 = u;
      if (v < v0) v0 = v; if (v > v1) v1 = v;
    }
  }
  // Longer or taller than the polygon along the label: it cannot fit.
  if (u1 - u0 < 2 * hx || v1 - v0 < 2 * hy) return null;
  let du = Math.max(hx / 3, precision), dv = Math.max(hy / 2, precision);
  const cells = ((u1 - u0) / du) * ((v1 - v0) / dv);
  if (cells > 800) {  // a big polygon: coarser grid
    const k = Math.sqrt(cells / 800);
    du *= k;
    dv *= k;
  }
  const candidates = [];
  for (let u = Math.floor(u0 / du) * du; u <= u1; u += du) {
    for (let v = Math.floor(v0 / dv) * dv; v <= v1; v += dv) {
      const p = fromFrame([u, v]);
      const room = roomAt(rings, p);
      if (room >= hy) candidates.push({ p, room });  // at least the label's height across
    }
  }
  const bin = Math.max(dv, precision) / 2;  // rooms within half a step count as equal
  candidates.sort((a, b) => (Math.round(b.room / bin) - Math.round(a.room / bin))
    || (Math.hypot(a.p[0] - target[0], a.p[1] - target[1]) - Math.hypot(b.p[0] - target[0], b.p[1] - target[1])));
  let found = null;
  for (let i = 0; i < candidates.length && i < 400 && !found; i++) {
    if (ok(candidates[i].p)) found = candidates[i];
  }
  // Nothing clear of the other labels: the best spot inside anyway (MapLibre
  // and the overlap setting decide, as for horizontal labels) - never outside.
  for (let i = 0; i < candidates.length && i < 400 && !found; i++) {
    if (ok(candidates[i].p, [])) found = candidates[i];
  }
  if (!found) return null;
  if (old && Math.abs(Number(old.properties?.[field]) - angle * 180 / Math.PI) < 3 && ok(old.point)
      && roomAt(rings, old.point) >= KEEP_ROOM * found.room) {
    return { point: old.point, angle };
  }
  return { point: found.p, angle };
}

// boxInside, decided by the free room around the point where that settles it.
function fitsFlat(rings, point, room, [hx, hy]) {
  if (room >= Math.hypot(hx, hy)) return true;  // the whole box is within the free circle
  if (room < hy) return false;                  // not even its height fits
  return boxInside(rings, point, [hx, hy]);
}

// The direction of a polygon around ``point`` (within ``radius``; anywhere
// when no point): the length-weighted mean direction of its edges (tile cuts
// left out), as an angle in [-90°, 90°) in radians, y down - the long axis
// of a street around the label.
function localDirection(rings, point, radius) {
  const around = (radius2) => {
    let sx = 0, sy = 0;
    const box = point ? [point[0] - radius2, point[1] - radius2, point[0] + radius2, point[1] + radius2] : null;
    for (const [a, b] of realEdges(rings)) {
      for (const piece of box ? clipLine([a, b], box) : [[a, b]]) {
        const dx = piece[piece.length - 1][0] - piece[0][0], dy = piece[piece.length - 1][1] - piece[0][1];
        const length = Math.hypot(dx, dy);
        if (!length) continue;
        const theta = Math.atan2(dy, dx);
        sx += length * Math.cos(2 * theta);
        sy += length * Math.sin(2 * theta);
      }
    }
    return [sx, sy];
  };
  let [sx, sy] = around(radius);
  if (!sx && !sy) [sx, sy] = around(radius * 4);
  let angle = Math.atan2(sy, sx) / 2;
  if (angle >= Math.PI / 2) angle -= Math.PI;
  if (angle < -Math.PI / 2) angle += Math.PI;
  return angle;
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
export { boxInside, localDirection, envelope, placeTurned };

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

// Polyline cut to a rectangle (Liang-Barsky per segment): the pieces inside.
function clipLine(points, [x0, y0, x1, y1]) {
  const pieces = [];
  let current = null;
  for (let i = 0; i + 1 < points.length; i++) {
    let [ax, ay] = points[i];
    let [bx, by] = points[i + 1];
    const dx = bx - ax, dy = by - ay;
    let t0 = 0, t1 = 1;
    let inside = true;
    for (const [p, q] of [[-dx, ax - x0], [dx, x1 - ax], [-dy, ay - y0], [dy, y1 - ay]]) {
      if (p === 0) {
        if (q < 0) { inside = false; break; }
      } else {
        const r = q / p;
        if (p < 0) { if (r > t1) { inside = false; break; } if (r > t0) t0 = r; }
        else { if (r < t0) { inside = false; break; } if (r < t1) t1 = r; }
      }
    }
    if (!inside) { current = null; continue; }
    const start = [ax + t0 * dx, ay + t0 * dy], end = [ax + t1 * dx, ay + t1 * dy];
    if (!current || t0 > 0) {
      current = [start];
      pieces.push(current);
    }
    current.push(end);
    if (t1 < 1) current = null;
  }
  return pieces.filter((piece) => piece.length >= 2);
}

// Pieces of one line (cut at tile edges and the screen) joined where their
// ends meet.
function stitch(pieces) {
  const same = (a, b) => Math.abs(a[0] - b[0]) <= 1e-10 && Math.abs(a[1] - b[1]) <= 1e-10;
  const chains = pieces.map((piece) => piece.slice());
  for (let joined = true; joined;) {
    joined = false;
    for (let i = 0; i < chains.length && !joined; i++) {
      for (let j = 0; j < chains.length && !joined; j++) {
        if (i === j) continue;
        const a = chains[i], b = chains[j];
        let merged = null;
        if (same(a[a.length - 1], b[0])) merged = a.concat(b.slice(1));
        else if (same(a[a.length - 1], b[b.length - 1])) merged = a.concat(b.slice(0, -1).reverse());
        else if (same(a[0], b[0])) merged = a.slice().reverse().concat(b.slice(1));
        if (merged) {
          chains[i] = merged;
          chains.splice(j, 1);
          joined = true;
        }
      }
    }
  }
  return chains;
}

function chainLength(chain) {
  let length = 0;
  for (let i = 0; i + 1 < chain.length; i++) {
    length += Math.hypot(chain[i + 1][0] - chain[i][0], chain[i + 1][1] - chain[i][1]);
  }
  return length;
}

// Point at distance s along a chain and the label rotation there (QGIS:
// line azimuth - 90°, kept upright in (-90°, 90°]; world y grows south).
function along(chain, s) {
  for (let i = 0; i + 1 < chain.length; i++) {
    const [ax, ay] = chain[i], [bx, by] = chain[i + 1];
    const step = Math.hypot(bx - ax, by - ay);
    if (s <= step || i + 2 === chain.length) {
      const t = step ? Math.max(0, Math.min(1, s / step)) : 0;
      let rotation = Math.atan2(bx - ax, -(by - ay)) * 180 / Math.PI - 90;
      if (rotation > 90) rotation -= 180;
      if (rotation <= -90) rotation += 180;
      return { point: [ax + (bx - ax) * t, ay + (by - ay) * t], rotation };
    }
    s -= step;
  }
  return null;
}

// Distance along a chain of the point on it nearest to ``point``, and how far.
function project(chain, [x, y]) {
  let best = { s: 0, d: Infinity }, walked = 0;
  for (let i = 0; i + 1 < chain.length; i++) {
    const [ax, ay] = chain[i], [bx, by] = chain[i + 1];
    const dx = bx - ax, dy = by - ay, step = Math.hypot(dx, dy);
    const t = step ? Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / (step * step))) : 0;
    const d = Math.hypot(x - (ax + dx * t), y - (ay + dy * t));
    if (d < best.d) best = { s: walked + t * step, d };
    walked += step;
  }
  return best;
}

// A line label on the visible part of its line: the middle of the longest
// visible stretch if the label fits there (along the line, on the screen)
// and is clear of the labels placed before, else the nearest such spot
// along that stretch or a shorter one; no clear spot: no label (as QGIS).
function placeOnLine(loaded, properties, old, view, guarded, avoid, options) {
  const withRotation = (rotation) => (options.rotationField
    ? { ...properties, [options.rotationField]: rotation } : properties);
  const chains = (pieces) => stitch(pieces).map((chain) => ({ chain, length: chainLength(chain) }))
    .sort((a, b) => b.length - a.length);
  const visible = chains(loaded.flatMap((piece) => clipLine(piece, view)));
  if (!visible.length) {  // off the screen: in advance, away from the screen edge
    const whole = chains(loaded)[0];
    const at = whole && along(whole.chain, whole.length / 2);
    if (!at || within(guarded, at.point)) return null;
    return { point: at.point, properties: withRotation(at.rotation) };
  }
  const half = options.labelBox ? options.labelBox(properties) : null;
  const candidate = (chain, s) => {
    const at = along(chain, s);
    if (!at || !half) return at && { ...at, fit: 0, clear: true, box: null };
    const angle = at.rotation * Math.PI / 180;
    const [x, y] = at.point;
    const [ex, ey] = envelope(half, angle);
    const fit = Math.min(x - view[0] - ex, view[2] - ex - x, y - view[1] - ey, view[3] - ey - y);
    const box = [x - ex, y - ey, x + ex, y + ey];
    return { ...at, fit, clear: !avoid.some((other) => intersect(other, box)), box };
  };
  const result = (c) => ({ point: c.point, properties: withRotation(c.rotation), box: c.box });
  const lo = half ? half[0] : 0;
  const longest = visible[0];
  if (old && !options.freeze && longest.length >= 2 * lo) {  // kept while near the middle and still fine
    const { s, d } = project(longest.chain, old.point);
    const precision = options.precision || 0;
    if (d <= 2 * precision + 1e-12 && Math.abs(s - longest.length / 2) <= longest.length / 4
        && s >= lo && s <= longest.length - lo) {
      const kept = candidate(longest.chain, s);
      if (kept && kept.fit >= 0 && kept.clear) return result(kept);
    }
  }
  for (const { chain, length } of visible) {
    if (length < 2 * lo) break;  // the label does not fit along it (nor along shorter ones)
    // From the middle outwards, in steps of a quarter label (at most 40).
    const step = Math.max(length / 40, half ? half[0] / 2 : length / 20);
    for (let k = 0; k <= 2 * Math.ceil(length / 2 / step); k++) {
      const s = length / 2 + (k % 2 ? 1 : -1) * Math.ceil(k / 2) * step;
      if (s < lo - 1e-12 || s > length - lo + 1e-12) continue;
      const c = candidate(chain, s);
      if (c && c.fit >= 0 && c.clear) return result(c);
    }
  }
  return null;
}

// The feature property the label's text-rotate reads, if any.
function rotationField(group, zoom) {
  const find = (expression) => {
    if (!Array.isArray(expression)) return null;
    if (expression[0] === "get" && typeof expression[1] === "string") return expression[1];
    for (const part of expression.slice(1)) {
      const found = find(part);
      if (found) return found;
    }
    return null;
  };
  return find((activeLayer(group, zoom).layout || {})["text-rotate"]);
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
  return layoutBoxes(activeLayer(group, zoom).layout || {}, zoom, perPx);
}

function layoutBoxes(layout, zoom, perPx) {
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

// World boxes of the point labels MapLibre draws from the other symbol
// layers (estimated from text and size at the anchor; rotation, offsets and
// icons ignored).
function renderedLabelBoxes(map, maplibregl, groups, zoom, perPx) {
  const ours = new Set();
  for (const group of groups.values()) {
    for (const { def } of group.layers) ours.add(def.id).add(def.id + OVERLAP_SUFFIX);
  }
  const layers = map.getStyle().layers.filter((l) => l.type === "symbol" && !ours.has(l.id)
    && (l.layout || {})["text-field"] !== undefined).map((l) => l.id);
  if (!layers.length) return [];
  const boxes = [];
  const sizes = new Map();
  for (const f of map.queryRenderedFeatures({ layers })) {
    if (f.geometry.type !== "Point") continue;  // labels along lines: their position is unknown here
    if (!sizes.has(f.layer.id)) sizes.set(f.layer.id, layoutBoxes(f.layer.layout || {}, zoom, perPx));
    const half = sizes.get(f.layer.id)(f.properties);
    if (!half) continue;
    const p = maplibregl.MercatorCoordinate.fromLngLat(f.geometry.coordinates);
    boxes.push([p.x - half[0], p.y - half[1], p.x + half[0], p.y + half[1]]);
  }
  return boxes;
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

// "Overlap if required" (QGIS) for the label layers MapLibre places itself
// (metadata q2vt:overlap): MapLibre's cooperative overlap - other positions
// first, overlapping only if none is free. (A copy layer switched by the
// placement result lagged behind it: labels blinked while the map moved.)
// Labels placed by enableVisibleLabels are drawn where they are put.
export function enableOverlapFallback(map) {
  const layers = [];
  for (const layer of map.getStyle().layers) {
    if (layer.type !== "symbol" || !(layer.metadata && layer.metadata["q2vt:overlap"] === "if-required")) continue;
    const layout = layer.layout || {};
    if (layout["text-overlap"] === "always") continue;  // placed by the viewer
    if (layout["text-field"] !== undefined) map.setLayoutProperty(layer.id, "text-overlap", "cooperative");
    if (layout["icon-image"] !== undefined) map.setLayoutProperty(layer.id, "icon-overlap", "cooperative");
    layers.push(layer.id);
  }
  const noop = () => {};
  return { sync: noop, layers, destroy: noop };
}

