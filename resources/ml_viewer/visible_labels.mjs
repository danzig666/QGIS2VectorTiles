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
// drops candidates outside the map extent). A polygon wholly on the screen
// is labelled however small it is (QGIS: the label's centre inside, Free
// labels turned along the polygon sticking out of it where they cannot fit
// inside); a polygon cut by the screen edge only when the visible part is at
// least as large as the label's box: a sliver in view gets no label at the
// edge. A multi-part feature (metadata["q2vt:label-per-part"]) gets a label
// on every part, else on its biggest part only. "Outside polygons" labels
// (q2vt:label-around.outside) go beside the polygon's edge, never on it.
// Groups are placed by QGIS label priority
// and z-index (metadata["q2vt:label-rank"]); later ones keep clear of the
// boxes already placed (a zone code and the parcel number of the same
// parcel do not compete for one point).
// Line labels drawn once per line (metadata["q2vt:visible-kind"] = "line",
// exported at the middle of the whole line) go to the middle of the longest
// visible stretch of their line, rotated along it, if the label fits along
// that stretch and on the screen (sliding along the line, or to a shorter
// visible stretch, to fit and to keep clear of other labels), as QGIS places
// line labels inside the extent; clear also of the point labels MapLibre
// draws itself (building numbers, names), as "around point" and "pole"
// labels are (QGIS places all labels together). No clear spot: no label.
// The pieces of a line in neighbouring tiles are joined where their cuts
// meet within a pixel. QGIS's Horizontal placement on lines
// (metadata["q2vt:line-orient"]) is drawn level, centred on the line. A
// repeated line label (metadata["q2vt:repeat"]) gets one label per repeat
// part of the longest visible stretch, as QGIS chops the line clipped to
// the map extent.
// While the map moves
// (drag, zoom animation) placed labels do not move at all - only polygons
// without a label get one - and the rule above is applied once the map
// stops: labels stay glued to the map like the other labels. Polygons in the
// loaded tiles just outside the screen get their label in advance (the
// centroid of what is loaded of them), so panning reveals labels that are
// already placed instead of waiting for a new round.
//
// MapLibre draws these labels exactly where they are put (text-overlap
// "always", so they never blink while the map moves): the placement above
// keeps them clear of each other, and a label with no free spot is left out
// (QGIS "Never overlap", the default), drawn overlapping where QGIS allows
// it (metadata["q2vt:overlap"] "if-required"; with
// "q2vt:overlap-at-no-cost" at its best spot whatever it overlaps, and the
// others do not keep clear of it). Other
// label layers QGIS may overlap "if required" use MapLibre's cooperative
// overlap (enableOverlapFallback).

const SOURCE_PREFIX = "q2vt_visible_";
export const LOADER_PREFIX = "q2vt_visible_loader_";
// A source layer no tile has (see the loaders below).
const LOADER_SOURCE_LAYER = "q2vt_loader_none";
export const OVERLAP_SUFFIX = "_q2vt_overlap";
const FREE_ROTATION = "q2vt_free_rotation";
// "Always label" layers: a label that does not fit is drawn smaller (this
// factor of its size), at worst at the roomiest point at the smallest size.
const LABEL_SCALE = "q2vt_label_scale";
const SHRINK_STEPS = [0.8, 0.65, 0.5];
const EDGE_PX = 24; // a kept label must stay this far inside the screen
// Recompute at most this often while polygon tiles arrive / the map moves.
// Unchanged labels keep their fade state across the GeoJSON reloads
// (MapLibre matches them by tile and position), so this does not flicker.
const TILE_INTERVAL_MS = 80;
const MOVE_INTERVAL_MS = 150;
const REACH = 0.5;   // labels in advance up to half a screen beyond each edge
const GUARD_PX = 48; // ... but not so close that their text reaches the screen
// Label point tiles stop at this zoom at least (then overzoomed): bigger
// tiles cover more around the screen, so panning rarely needs new ones laid
// out. Points are rounded to 1/8192 of a tile of that zoom: 2^(z - maxzoom - 4)
// px at zoom z, so the source goes deep enough (pointMaxzoom) for half a
// pixel at the deepest browser zoom - else overzoomed labels sat off their
// line (4 px at z22 from z15 tiles).
const POINT_MAXZOOM = 15;
const KEEP_ROOM = 0.8;  // a kept "pole" label keeps >= 80 % of the best room
const KEEP_PX = 24;     // a kept centroid label stays this close to the centroid
// A kept point from coarser tile data than is loaded now, this close (tile
// units of its data, plus twice the precision of its search) to the new
// spot, is that spot seen through the coarser data: it moves there (else
// its rounding error doubled with each zoom).
const REFINE_UNITS = 4;

// The label point source's maxzoom for a map that zooms to maxZoom.
export function pointMaxzoom(maxZoom) {
  return Math.max(POINT_MAXZOOM, Math.ceil(maxZoom) - 4);
}

// The curved label source's maxzoom (MapLibre's GeoJSON limit: 24).
export function curvedMaxzoom(maxZoom) {
  return Math.min(24, Math.max(POINT_MAXZOOM, Math.ceil(maxZoom)));
}

// Screen pixels per tile pixel of the curved label source at ``zoom``.
export function curvedScale(map, zoom) {
  return 2 ** (zoom - Math.min(Math.floor(zoom), curvedMaxzoom(map.getMaxZoom())));
}

// The labelPath options of a curved line label layer at ``zoom``. MapLibre
// lays a tile's labels out with the text size of the tile's zoom + 1
// (symbol_bucket layoutTextSize): a size that grows with the zoom (map-unit
// text) is that much longer on the line than drawn (``ratio``).
export function curvedOption(map, layout, zoom, perPx) {
  const size = layout["text-size"] ?? 16;
  const tileZoom = Math.min(Math.floor(zoom), curvedMaxzoom(map.getMaxZoom()));
  return {
    perPx, scale: curvedScale(map, zoom),
    maxAngle: Number(layout["text-max-angle"]) || 45,
    ratio: (properties) => {
      const laid = Number(evaluate(size, tileZoom + 1, properties || {}));
      const drawn = Number(evaluate(size, zoom, properties || {}));
      return laid > 0 && drawn > 0 ? laid / drawn : 1;
    },
  };
}

// options (all optional):
//   eligible(properties, polygonSourceLayer) -> boolean: only these polygons
//     get a label (layer/rule toggles, attribute filters);
//   minTextPx: "always" labels sized in map units shrink to fit no smaller
//     than this text size (CSS px; 0: down to half their size);
//   onUnsupported(reason): MapLibre no longer exposes the tile fields this
//     helper reads (_x/_y/_z/_vectorTileFeature) - labels stay static.
// Returns {update, sync, groups, snapshot, setEligibility, pause, resume,
// destroy}; snapshot() maps each polygon source layer to the label points
// (GeoJSON features) last written.
export function enableVisibleLabels(map, maplibregl, sourceId = "q2vt_tiles", options = {}) {
  const groups = new Map(); // polygon source layer -> {source, layers: []}
  // The one copy of the whole style (its label layers are moved below).
  const style = map.getStyle();
  const fonts = fontMetrics(style);
  const tileSource = style.sources[sourceId] || {};
  style.layers.forEach((layer, index) => {
    const polygons = layer.metadata && layer.metadata["q2vt:visible-polygons"];
    if (!polygons || layer.source !== sourceId) return;
    if (!groups.has(polygons)) groups.set(polygons, { source: SOURCE_PREFIX + polygons, layers: [] });
    const before = style.layers.slice(index + 1).find((l) => !(l.metadata && l.metadata["q2vt:visible-polygons"]));
    groups.get(polygons).layers.push({ def: layer, before: before ? before.id : undefined });
    if (layer.metadata["q2vt:label-anchor"] === "pole") groups.get(polygons).anchor = "pole";
    if (layer.metadata["q2vt:visible-kind"] === "line") groups.get(polygons).kind = "line";
    if (layer.metadata["q2vt:line-degraded"]) groups.get(polygons).degraded = true;
    // QGIS overlap handling: "Never overlap" (none), "Allow overlap if
    // required" (a free spot first), "Allow overlap at no cost" (anywhere).
    if (layer.metadata["q2vt:overlap"] === "if-required") {
      groups.get(polygons).overlap = layer.metadata["q2vt:overlap-at-no-cost"] ? "always" : "if-required";
    }
    // "Label every part of multi-part features": one label per part, else
    // only on the biggest part (QGIS).
    if (layer.metadata["q2vt:label-per-part"]) groups.get(polygons).perPart = true;
    // QGIS Horizontal placement on lines: level, centred on the line.
    if (layer.metadata["q2vt:line-orient"] === "horizontal") groups.get(polygons).level = true;
    // QGIS Curved placement: bent along the line, glyph by glyph.
    if (layer.metadata["q2vt:line-curved"]) groups.get(polygons).curved = true;
    // A repeated line label: one label per repeat part of the visible line.
    if (layer.metadata["q2vt:repeat"]) groups.get(polygons).repeat = layer.metadata["q2vt:repeat"];
    // "Merge connected lines": lines of one text meeting end to end are one line.
    if (layer.metadata["q2vt:merge-lines"]) groups.get(polygons).merge = true;
    if (layer.metadata["q2vt:label-orient"] === "free") groups.get(polygons).orient = "free";
    // "Around point" (QGIS): beside the point at the label distance, not on it.
    if (layer.metadata["q2vt:label-around"]) groups.get(polygons).around = layer.metadata["q2vt:label-around"];
    if (options.always && options.always.has(layer.id)) groups.get(polygons).always = true;
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
    // fill keeps the polygons loaded whatever else is shown. It reads no
    // source layer that is in the tiles: querySourceFeatures reads any of a
    // tile's layers, and a fill of the polygons themselves would build
    // their triangles in every tile (memory) only to draw nothing.
    const zooms = group.layers.map(({ def }) => [def.minzoom ?? 0, def.maxzoom ?? 24]);
    map.addLayer({
      id: LOADER_PREFIX + polygons, type: "fill", source: sourceId, "source-layer": LOADER_SOURCE_LAYER,
      minzoom: Math.min(...zooms.map((z) => z[0])), maxzoom: Math.max(...zooms.map((z) => z[1])),
      paint: { "fill-opacity": 0 },
    }, firstLayer);
    map.addSource(group.source, {
      type: "geojson", data: { type: "FeatureCollection", features: [] },
      // A curved label's piece of line whole in every tile it crosses: each
      // tile then finds the same middle, kept by the one tile it falls in
      // (cut at the tile edge, each piece got a label of its own). Not
      // overzoomed: MapLibre fits the label on its line in tile pixels, so
      // the piece is longer by the tile's scale (curvedScale).
      ...(group.curved ? { buffer: 512, maxzoom: curvedMaxzoom(map.getMaxZoom()) }
        : { maxzoom: pointMaxzoom(map.getMaxZoom()) }),
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
      // A line label above or below its line keeps its anchor: the box
      // edge on the line (the point is on the line).
      const anchor = moved.layout["text-anchor"];
      moved.layout = { ...moved.layout, "text-anchor": group.kind === "line"
        && (anchor === "top" || anchor === "bottom") ? anchor : "center" };
      if (group.curved && !group.level) {
        // The label is laid out along its piece of line (written below):
        // MapLibre bends it with the line and keeps it upright.
        moved.layout["symbol-placement"] = "line-center";
        moved.layout["text-rotation-alignment"] = "map";
        moved.layout["text-keep-upright"] = true;
        delete moved.layout["text-rotate"];
        delete moved.layout["symbol-spacing"];
        // The bends were judged here as QGIS does (curvedBendsOk): MapLibre's
        // own check adds up the turns of a few pixels of line and dropped
        // labels QGIS bends round a corner.
        moved.layout["text-max-angle"] = 179;
      }
      if (group.always) moved.layout["text-size"] = scaledTextSize(moved.layout["text-size"]);
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
      if (group.orient === "free") {
        moved.layout["text-rotate"] = ["to-number", ["get", FREE_ROTATION], 0];
        // A frame turns with its label (QGIS "Sync with label" / "Offset of label").
        const follows = (moved.metadata || {})["q2vt:icon-follows-text"];
        if (moved.layout["icon-image"] !== undefined && typeof follows === "number") {
          moved.layout["icon-rotate"] = follows ? ["+", moved.layout["text-rotate"], follows] : moved.layout["text-rotate"];
        }
      }
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
    // Final once the map stands still with its tiles loaded.
    if (!map.isMoving() && map.isSourceLoaded(sourceId)) dirty = false;
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
    // line, "around point" and "pole" labels keep clear of them too. Only
    // when such a group needs them.
    let otherBoxes = null;
    const others = () => (otherBoxes ??= renderedLabelBoxes(map, maplibregl, groups, zoom, perPx, fonts));
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
        labelBox: labelBoxes(map, group, zoom, perPx, fonts),
        minScale: options.minTextPx > 0 ? minTextScale(activeLayer(group, zoom).layout || {}, zoom, options.minTextPx)
          : null,
        // Line, "around point" and "pole" labels keep clear of the point
        // labels MapLibre draws itself (a town name): drawn with text-overlap
        // "always", they would otherwise sit on top of them.
        avoid: avoidFor(group, placed, others),
        kind: group.kind || "polygon", orient: group.orient || "horizontal",
        degraded: Boolean(group.degraded), overlap: group.overlap || null, perPart: Boolean(group.perPart),
        level: Boolean(group.level),
        curved: group.curved && !group.level ? curvedOption(map, activeLayer(group, zoom).layout || {}, zoom, perPx)
          : null,
        repeat: repeatOption(group.repeat, zoom, perPx),
        mergeText: group.merge ? textKey((activeLayer(group, zoom).layout || {})["text-field"]) : null,
        textAnchor: (activeLayer(group, zoom).layout || {})["text-anchor"],
        rotationField: group.orient === "free" ? FREE_ROTATION : rotationField(group, zoom),
        eligible: eligible ? (properties) => eligible(properties, polygons) : null,
        always: !!group.always,
        around: aroundOption(group.around, zoom, perPx),
      });
      write(map.getSource(group.source), data, polygons);
      // "Allow overlap at no cost" works both ways (QGIS: no conflict when
      // either label has it): the others need not keep clear of these.
      if (data.boxes && group.overlap !== "always" && groupShown(map, group, zoom)) placed.push(...data.boxes);
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
  // "idle" also follows paint-only changes (an animated marker's frames, ten
  // a second): labels are placed again only when something they depend on
  // changed since the last final placement (moves, tiles, toggles).
  let dirty = true;
  const throttled = (interval) => () => {
    dirty = true;
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
  const onIdle = () => { if (dirty) schedule(); };
  map.on("idle", onIdle);
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
      map.off("idle", onIdle);
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

// The labels of a repeated line label are kept as "<feature key><sep><k>".
const PART_SEPARATOR = "\u0000";
const baseKey = (key) => (key.includes(PART_SEPARATOR) ? key.slice(0, key.indexOf(PART_SEPARATOR)) : key);

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
  // A tile unit of the data (overzoomed tiles are cut from the max zoom's):
  // neighbouring tiles round a line's cut on their shared edge to it.
  let tileUnit = 0;
  let dataUnit = 0;  // the tile unit of the deepest data (the label points' precision)
  const loaded = new Set(); // deepest tiles with data
  let byFeature = new Map();
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
    dataUnit = Math.max(dataUnit, scale);
    tileUnit = Math.max(tileUnit, 1 / (tile.extent * 2 ** Math.min(f._z, options.tileZoom ?? f._z)));
    const ox = f._x / 2 ** f._z, oy = f._y / 2 ** f._z;
    const clip = intersect(reach, [ox, oy, ox + 1 / 2 ** f._z, oy + 1 / 2 ** f._z]);
    if (!clip) continue;
    const rings = [];
    const tileRect = [ox, oy, ox + 1 / 2 ** f._z, oy + 1 / 2 ** f._z];
    let extent = null;  // of the piece in its own tile (not cut to reach)
    for (const ring of tile.loadGeometry()) {
      const world = ring.map((p) => [ox + p.x * scale, oy + p.y * scale]);
      extent = union(extent, intersect(bounds([world]), tileRect));
      if (options.kind === "line") {  // polylines, cut to the tile
        const pieces = clipLine(world, clip);
        for (const piece of pieces) piece.clip = clip;  // ends on it are cuts, not line ends
        rings.push(...pieces);
        continue;
      }
      const clipped = clipRing(world, clip);
      clipped.cut = clip;  // edges along it are tile cuts, not polygon edges
      if (clipped.length >= 3) rings.push(clipped);
    }
    if (!rings.length) continue;
    // The parts of a multi-part feature (q2vt_part) stay apart; the pieces
    // of one part in several tiles are joined. A plain copy of the
    // properties: tile feature properties have no prototype, which
    // MapLibre's worker serializer rejects ("_classRegistryKey").
    if (!byFeature.has(key)) byFeature.set(key, new Map());
    const parts = byFeature.get(key);
    const part = String(f.properties.q2vt_part ?? "");
    if (!parts.has(part)) parts.set(part, { properties: { ...f.properties }, rings: [], extent: null });
    const entry = parts.get(part);
    entry.rings.push(...rings);
    entry.extent = union(entry.extent, extent);
  }
  if (options.kind === "line" && options.mergeText) {
    // "Merge connected lines" joins feature parts (pal's FeatureParts): the
    // parts of the features are merged, the others keep their feature.
    const flat = new Map();
    for (const [key, parts] of byFeature) {
      for (const [part, entry] of parts) flat.set(key + PART_OF + part, entry);
    }
    byFeature = new Map();
    for (const [key, entry] of mergeConnected(flat, options.mergeText, joinTolerance({ ...options, tileUnit }))) {
      const [feature, part] = key.startsWith(MERGED_PREFIX) ? [key, ""] : key.split(PART_OF);
      if (!byFeature.has(feature)) byFeature.set(feature, new Map());
      byFeature.get(feature).set(part, entry);
    }
  }
  // A kept point on a tile still loading cannot be checked: keep it.
  const unchecked = (point) => !loaded.has(
    `${Math.floor(point[0] * 2 ** deepest)}/${Math.floor(point[1] * 2 ** deepest)}`);
  // QGIS overlap handling: "never" (the default: a label with no free spot
  // is not drawn), "if-required" (a free spot first) or "always" (at no cost).
  const overlap = options.overlap === true ? "if-required" : (options.overlap || "never");
  const tolerance = (options.precision || 0) + 1e-12;
  const points = new Map();
  const avoid = (options.avoid || []).slice();
  // The same boxes in a grid: each polygon looks up only those near it (a
  // scan of all of them per polygon grew with the square of the labels).
  const avoidIndex = boxIndex(avoid.map((bounds) => ({ bounds })), view);
  const keepClear = (box) => {
    avoid.push(box);
    avoidIndex.add({ bounds: box });
  };
  const boxes = [];
  // Bigger polygons first: they get the middle, smaller neighbours keep clear.
  const area = (rings) => Math.abs(rings.reduce((sum, ring) => sum + ring.reduce((a, [x1, y1], i) => {
    const [x2, y2] = ring[(i + 1) % ring.length];
    return a + x1 * y2 - x2 * y1;
  }, 0), 0));
  // QGIS "Label every part": a label per part; else only the biggest part
  // (area, or length for lines, of what is in the map extent) gets one.
  const length = (pieces) => pieces.reduce((sum, piece) => sum + chainLength(piece), 0);
  const visibleSize = (rings) => (options.kind === "line"
    ? length(rings.flatMap((piece) => clipLine(piece, view)))
    : area(rings.map((ring) => clipRing(ring, view)).filter((ring) => ring.length >= 3)));
  const entries = [];
  for (const [key, parts] of byFeature) {
    if (parts.size === 1) {
      entries.push([key, parts.values().next().value, key]);
    } else if (!options.perPart) {
      let best = null, most = -Infinity;
      for (const entry of parts.values()) {
        // Nothing on the screen: the biggest loaded part (labelled in advance).
        const size = visibleSize(entry.rings) || 1e-9 * (options.kind === "line" ? length(entry.rings) : area(entry.rings));
        if (size > most) [best, most] = [entry, size];
      }
      entries.push([key, best, key]);
    } else {
      for (const [part, entry] of parts) entries.push([`${key}#${part}`, entry, key]);
    }
  }
  // While the map moves (freeze) the labels already shown go first: they
  // stay, and newcomers keep clear of them.
  const frozen = (key) => (options.freeze && state.points.has(key) ? 1 : 0);
  const ordered = entries.map(([key, value, orig]) => [key, value, area(value.rings), orig])
    .sort((a, b) => frozen(b[0]) - frozen(a[0]) || b[2] - a[2]);
  // "Outside polygons": the layer's other polygons are obstacles (QGIS
  // default): a label beside one polygon keeps off its neighbours if it can.
  const obstacles = options.around && options.around.outside
    ? boxIndex(ordered.map(([key, { rings }]) => ({ key, rings, bounds: bounds(rings) })), view) : null;
  // The labels kept of each repeated line label.
  const partsOf = new Map();
  for (const part of options.repeat > 0 ? state.points.keys() : []) {
    if (part.includes(PART_SEPARATOR)) partsOf.set(baseKey(part), [...(partsOf.get(baseKey(part)) || []), part]);
  }
  const lineOptions = { ...options, tileUnit };
  for (let [key, { properties, rings: loaded, extent }, , orig] of ordered) {
    const old = state.points.get(key);
    // A kept point, or the new spot ``p`` when the kept one is only that
    // spot computed from coarser tiles (a small polygon labelled zoomed out).
    const refined = (p) => (old && old.unit > 1.5 * dataUnit && p && Math.hypot(old.point[0] - p[0],
      old.point[1] - p[1]) <= REFINE_UNITS * old.unit + 2 * (old.prec || 0) ? p : old.point);
    if (old && options.freeze) {
      const kept = old.properties || properties;
      points.set(key, { point: old.point, properties: kept, orig, unit: old.unit, prec: old.prec,
                        line: old.line });
      // Its box at this zoom (shrunk and turned as drawn), for the others.
      const label = options.labelBox ? options.labelBox(kept) : null;
      if (label) {
        const scale = Number(kept[LABEL_SCALE]) || 1;
        const angle = (Number(options.rotationField && kept[options.rotationField]) || 0) * Math.PI / 180;
        const [hx, hy] = envelope([label[0] * scale, label[1] * scale], angle);
        const box = [old.point[0] - hx, old.point[1] - hy, old.point[0] + hx, old.point[1] + hy];
        boxes.push(box);
        keepClear(box);
      }
      continue;
    }
    if (options.kind === "line" && options.repeat > 0) {  // one label per repeat part
      const parts = partsOf.get(key) || [];
      if (options.freeze && parts.length) {
        for (const part of parts) points.set(part, state.points.get(part));
        continue;
      }
      placeRepeatedOnLine(loaded, properties, view, avoid, lineOptions).forEach((placed, k) => {
        points.set(key + PART_SEPARATOR + k, { ...placed, orig });
        if (placed.box) {
          boxes.push(placed.box);
          keepClear(placed.box);
        }
      });
      continue;
    }
    if (options.kind === "line") {
      const placed = placeOnLine(loaded, properties, old, view, guarded, avoid, lineOptions);
      if (placed) {
        points.set(key, { ...placed, orig });
        if (placed.box) {
          boxes.push(placed.box);
          keepClear(placed.box);
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
    const outside = Boolean(options.around && options.around.outside);
    if (!rings.length) {  // off the screen: in advance, away from the screen edge
      if (outside) continue;  // beside the polygon: placed once it is in view
      const point = pole ? roomiestPoint(loaded, options.precision).point : labelPoint(loaded);
      if (point && !within(guarded, point)) {
        points.set(key, { point, properties, orig, unit: dataUnit, prec: pole ? options.precision : 0 });
      }
      continue;
    }
    // The label's box (half width/height, world units): it must fit on the
    // screen and keep clear of the boxes of better-ranked labels.
    const label = options.labelBox ? options.labelBox(properties) : null;
    let half = label;
    const fit = (x, y) => (half ? Math.min(x - view[0] - half[0], view[2] - half[0] - x,
                                           y - view[1] - half[1], view[3] - half[1] - y) : Infinity);
    // Only boxes that can matter: within a label's size of the visible part
    // ("outside polygons": of the polygon's edges, the part on the screen).
    let near = [];
    const extra = options.around ? options.around.distance : 0;
    const reachBox = intersect(grow(bounds(outside ? loaded : rings), 2 * (half ? half[0] : 0) + extra,
                                    2 * (half ? half[1] : 0) + extra), view);
    if (half && avoid.length && overlap !== "always" && reachBox) {
      near = avoidIndex.query(reachBox).map(({ bounds }) => grow(bounds, half[0], half[1]));
    }
    const clear = (x, y) => {
      let min = Infinity;
      for (const box of near) min = Math.min(min, rectDistance(x, y, box));
      return min;
    };
    // The boxes keep LABEL_MARGIN_PX round the text, for a better spot
    // where there is room; QGIS's overlap test is of the bare text: two
    // labels whose margins alone meet do not overlap.
    const slack = 2 * LABEL_MARGIN_PX * (options.precision || 0);
    const bareClear = (x, y) => clear(x, y) >= -slack;
    // "Always label" with the polygon's roomiest point precomputed at export:
    // a label that cannot fit even there goes there at once, smaller and
    // along the polygon - no search (narrow strips, tiny parcels).
    let point;
    let slot;
    // "Always label" shrinks no label below the smallest readable size
    // (the export's "Hide labels smaller than"): there it may stick out.
    const smallest = options.always && options.minScale ? options.minScale(properties) : 0;
    const pre = options.always && label ? precomputedSpot(properties, label, smallest) : null;
    if (pre) {
      if (!within(reach, pre.point)) continue;
      const angle = options.orient === "free" && options.rotationField ? pre.angle : 0;
      point = pre.point;
      half = envelope([label[0] * pre.scale, label[1] * pre.scale], angle);
      properties = { ...properties, [LABEL_SCALE]: pre.scale };
      if (options.orient === "free" && options.rotationField) properties[options.rotationField] = angle * 180 / Math.PI;
      const box = [point[0] - half[0], point[1] - half[1], point[0] + half[0], point[1] + half[1]];
      boxes.push(box);
      keepClear(box);
      points.set(key, { point, properties, orig });
      continue;
    }
    // The polygon wholly on the screen (not cut by its edge). QGIS labels it
    // however small: Over point at its centroid, Horizontal / Free with the
    // label's centre inside it (no "fit in polygon" by default).
    const whole = extent ? extent[0] >= view[0] - tolerance && extent[1] >= view[1] - tolerance
      && extent[2] <= view[2] + tolerance && extent[3] <= view[3] + tolerance
      : area(rings) >= 0.98 * area(loaded);
    // "Always label": such a polygon keeps its label whatever overlaps it.
    const forced = options.always && whole;
    // A label may overlap others: QGIS's overlap setting, or "Always label".
    const mayOverlap = overlap !== "never" || forced;
    // Only a sliver of a polygon cut by the screen edge in view: no label.
    // "Around point" labels go beside the polygon (a restaurant's building).
    if (label && !options.around && !whole && area(rings) < 4 * label[0] * label[1]) continue;
    let scale = 1;
    if (pole) {
      const search = () => {
        const room = (extra) => roomiestPoint(rings, options.precision, extra);
        let score = (x, y) => Math.min(fit(x, y), clear(x, y));
        let best = room(score);
        if (!(best.room > 0) && slack && near.length) {  // free but for the margins
          score = (x, y) => Math.min(fit(x, y), clear(x, y) + slack);
          best = room(score);
        }
        if (!(best.room > 0) && mayOverlap) {  // no free spot: the best spot that fits (overlapping)
          score = fit;
          best = room(score);
        }
        if (!(best.room > 0) && forced) {  // too small for the label: the roomiest point
          score = () => Infinity;
          best = room(null);
        }
        return { best, score };
      };
      let { best, score } = search();
      // No free spot of the polygon on the screen: no label (QGIS "Never overlap").
      if (!(best.room > 0)) continue;
      const free = options.orient === "free" && options.rotationField && label;
      if (free) {
        // Free (angled), as QGIS: horizontal where the label fits inside the
        // polygon, else turned along the polygon (a street name along its
        // street): wholly inside where it fits, else with its centre inside
        // (QGIS only requires that without "fit in polygon").
        const field = options.rotationField;
        const fine = (p) => Math.min(fit(...p), clear(...p)) >= 0;
        const flatOld = old && !Number(old.properties?.[field]) && within(inner, old.point)
          && fitsFlat(rings, old.point, roomAt(rings, old.point), label) && fine(old.point)
          && roomAt(rings, old.point) >= KEEP_ROOM * best.room;
        const oldScale = Number(old?.properties?.[LABEL_SCALE]) || 1;
        // QGIS decides the angle from the label's own size (no margin).
        const bare = (box) => box.map((h) => Math.max(h - LABEL_MARGIN_PX * (options.precision || 0), h / 2));
        const place = (box, keepOld, loose = false) => {
          const angle = freeAngle(rings, bare(box));
          if (!angle && !loose) {
            if (keepOld && flatOld) return { point: refined(best.point), angle: 0 };
            if (fitsFlat(rings, best.point, best.room, box) && fit(...best.point) >= 0) {
              return { point: best.point, angle: 0 };
            }
          }
          // Equally roomy spots: the one nearest the centroid wins (QGIS's
          // polygon candidate costs), not the arbitrary roomiest point along
          // a long, even strip.
          return placeTurned(rings, angle, box, view, avoid, labelPoint(rings) || best.point,
                             oldScale === scale ? old : null, field, options.precision,
                             { overlap: mayOverlap, loose, refined });
        };
        let placed = place(label, true);
        if (!placed && options.always) {
          // Smaller until it fits; at worst the smallest size at the roomiest
          // point, along the polygon (a narrow strip) - never no label.
          for (const step of SHRINK_STEPS) {
            if (step < smallest) break;
            scale = step;
            // Not even its height fits at the roomiest point: no spot will.
            if (label[1] * step > best.room) continue;
            placed = place([label[0] * step, label[1] * step], false);
            if (placed) break;
          }
          if (!placed) {
            const small = [label[0] * scale, label[1] * scale];
            const angle = freeAngle(rings, bare(small));
            placed = { point: best.point, angle };
          }
        }
        // Nowhere wholly inside: centred inside, sticking out (QGIS).
        if (!placed) placed = place(label, false, true);
        if (!placed) continue;
        point = placed.point;
        half = envelope([label[0] * scale, label[1] * scale], placed.angle);
        properties = { ...properties, [field]: placed.angle * 180 / Math.PI };
        if (scale < 1) properties[LABEL_SCALE] = scale;
        else delete properties[LABEL_SCALE];
      } else {
        const keep = old && within(inner, old.point) && (unchecked(old.point)
          || Math.min(roomAt(rings, old.point), score(...old.point)) >= KEEP_ROOM * best.room);
        point = keep ? refined(best.point) : best.point;
      }
    } else if (options.around && half) {
      // "Around point" (QGIS): beside the point at the label distance, the
      // first candidate position (QGIS order) that fits on the screen and
      // keeps clear of better-ranked labels; else the first that fits, if
      // the label may overlap. "Outside polygons": beside the polygon's
      // edge, the candidate nearest its centroid first.
      let candidates;
      const fits = (p) => fit(...p) >= 0;
      const free = (p) => fits(p) && clear(...p) >= 0;
      const bare = (p) => fits(p) && bareClear(...p);
      // A kept "outside" label that is still fine beside the polygon, its
      // text no farther from the polygon's edge than the label distance plus
      // the keep distance (as a candidate it was kept near is): kept without
      // building the candidates (64 samples tested against the polygon and
      // its neighbours, only to find one near it).
      const text = label.text || half;
      const keptOutside = outside && old && Number.isInteger(old.slot) && within(inner, old.point)
        && bare(old.point) && besidePolygon(loaded, old.point, text, options.around.distance, options.precision)
        && nearEdge(loaded, old.point, text, options.around.distance + (options.keepDistance ?? Infinity));
      if (keptOutside) {
        candidates = [];
      } else if (outside) {
        // Only the neighbours a label beside this polygon can reach.
        const others = reachBox ? obstacles.query(reachBox).filter((other) => other.key !== key) : [];
        candidates = outsideCandidates(loaded, rings, label.text || half, half, options.around.distance,
                                       options.precision, others, view);
      } else {
        const target = labelPoint(rings);
        if (!target) continue;
        candidates = aroundCandidates(target, label.text || half, options.around);
      }
      // A kept label stays near its candidate (at the label distance from
      // the polygon at this zoom; "outside": near any of them).
      const nearby = (p) => Math.hypot(old.point[0] - p[0], old.point[1] - p[1]) <= (options.keepDistance ?? Infinity);
      const kept = keptOutside || old && Number.isInteger(old.slot) && within(inner, old.point) && (outside
        ? candidates.some(nearby) && bare(old.point)
          && besidePolygon(loaded, old.point, label.text || half, options.around.distance, options.precision)
        : candidates[old.slot] && nearby(candidates[old.slot])
          && (unchecked(old.point) || bare(old.point)));
      slot = kept ? old.slot : candidates.findIndex(free);
      if (slot < 0) slot = candidates.findIndex(bare);
      if (slot < 0 && mayOverlap) slot = candidates.findIndex(fits);
      if (slot < 0) continue;
      point = kept ? old.point : candidates[slot];
    } else {
      const target = labelPoint(rings);
      if (!target || fit(...target) < 0) continue;
      const near = (p) => Math.hypot(p[0] - target[0], p[1] - target[1]) <= (options.keepDistance ?? Infinity);
      const keep = old && within(inner, old.point) && fit(...old.point) >= 0 && (unchecked(old.point)
        || (inside(rings, old.point) && near(old.point) && (mayOverlap || bareClear(...old.point))));
      // Over point (QGIS): its one candidate, dropped where it would overlap
      // a label placed before it ("Never overlap").
      if (!keep && !mayOverlap && !bareClear(...target)) continue;
      point = keep ? refined(target) : target;
    }
    if (half) {
      const box = [point[0] - half[0], point[1] - half[1], point[0] + half[0], point[1] + half[1]];
      boxes.push(box);
      keepClear(box);
    }
    // The precision of the point: its data's, and the roomiest point search's.
    const placed = { point, properties, orig, unit: dataUnit, prec: pole ? options.precision : 0 };
    points.set(key, slot === undefined ? placed : { ...placed, slot });
  }
  for (const [key, old] of state.points) {
    if (!points.has(key) && !excluded.has(old.orig ?? baseKey(key)) && (options.freeze || within(inner, old.point))
        && unchecked(old.point) && (!options.eligible || options.eligible(old.properties))) points.set(key, old);
  }
  state.points = points;
  const out = [];
  const lngLatOf = ([x, y]) => {
    const lngLat = new maplibregl.MercatorCoordinate(x, y, 0).toLngLat();
    return [lngLat.lng, lngLat.lat];
  };
  for (const [key, { point, properties, line }] of points) {
    if (!state.ids.has(key)) state.ids.set(key, state.nextId++);
    // A curved label: its piece of line (MapLibre lays the glyphs along it),
    // for the label size and tile scale of this zoom.
    const path = line && options.curved ? labelPath(line.chain, line.s,
      options.labelBox ? options.labelBox(properties) : null, options.curved, properties) : null;
    const geometry = path ? { type: "LineString", coordinates: path.map(lngLatOf) }
      : { type: "Point", coordinates: lngLatOf(point) };
    out.push({ type: "Feature", id: state.ids.get(key), properties, geometry });
  }
  return { type: "FeatureCollection", features: out, boxes };
}

// The repeat distance of a repeated line label (metadata q2vt:repeat: px,
// at "zoom" for map units) in world units at this zoom; 0 without one.
export function repeatOption(repeat, zoom, perPx) {
  if (!repeat) return 0;
  let px = Number(repeat.px) || 0;
  if (Number.isFinite(Number(repeat.zoom))) px *= 2 ** (zoom - Number(repeat.zoom));  // map units
  return px > 0 ? px * perPx : 0;
}

// "Around point" settings for labelPoints from the style metadata
// (q2vt:label-around): the label distance in world units at this zoom.
export function aroundOption(around, zoom, perPx) {
  if (!around) return null;
  // A zoom curve: map units within a map unit scale's limits.
  let px = Math.max(0, Number(Array.isArray(around.px) ? evaluate(around.px, zoom) : around.px) || 0);
  if (Number.isFinite(Number(around.zoom))) px *= 2 ** (zoom - Number(around.zoom));  // map units
  // QGIS's floor (2 px: "Outside polygons", or outside the polygon allowed).
  px = Math.max(px, Number(around.min_px) || 0);
  // QGIS's order (pal createCandidatesAroundPoint): top-right first.
  let anchors = Array.isArray(around.anchors) && around.anchors.length ? around.anchors
    : ["bottom-left", "left", "bottom", "top-left", "bottom-right", "top", "right", "top-right"];
  // No distance: QGIS tries the label centred on the point first.
  if (!px && !around.outside && !anchors.includes("center")) anchors = ["center", ...anchors];
  // "Outside polygons": beside the polygon's edge instead of its centroid.
  return around.outside ? { anchors, distance: px * perPx, outside: true } : { anchors, distance: px * perPx };
}

// Label centres around a point (world units, y down), one per anchor: the
// anchor names the side of the label that touches the point (MapLibre
// text-variable-anchor), "bottom" = the label above the point. The label
// keeps the QGIS distance from the point (diagonals: along the diagonal).
const ANCHOR_SIDES = {
  center: [0, 0], top: [0, 1], bottom: [0, -1], left: [1, 0], right: [-1, 0],
  "top-left": [1, 1], "top-right": [-1, 1], "bottom-left": [1, -1], "bottom-right": [-1, -1],
};
export function aroundCandidates(point, [hx, hy], { anchors, distance }) {
  return anchors.filter((a) => ANCHOR_SIDES[a]).map((a) => {
    const [sx, sy] = ANCHOR_SIDES[a];
    const d = sx && sy ? distance * Math.SQRT1_2 : distance;
    return [point[0] + sx * (d + hx), point[1] + sy * (d + hy)];
  });
}

// QGIS "Outside polygons" (pal createCandidatesOutsidePolygon): label centres
// at points spaced evenly along the polygon's edges (``loaded``: tile cuts
// are not edges), on the outer side of each edge at the label distance from
// it, horizontal; a box (``half``) that reaches the polygon (or nearer to
// it than half the distance) is no candidate. Cheapest first: the nearest
// to the centroid of the visible part (``rings``). ``text``: the bare text's
// half size, from which the distance is measured (and tested: the margin
// of ``half`` is for keeping clear of other labels, not of the polygon).
// A label over one of the ``obstacles`` ({rings, bounds}: other polygons)
// comes after all those that are not (QGIS obstacle cost). With ``view``:
// only the candidates whose box fits in it.
const OUTSIDE_CANDIDATES = 64;
const SIDE_TOLERANCE = Math.sin(5 * Math.PI / 180);  // QGIS: within 5° of an axis
export function outsideCandidates(loaded, rings, [tx, ty], half, distance, precision = 0, obstacles = [],
                                  view = null) {
  const byRing = loaded.map((ring) => [ring, realEdges([ring])]).filter(([, edges]) => edges.length);
  const total = byRing.flatMap(([, edges]) => edges).reduce((sum, [a, b]) => sum + Math.hypot(b[0] - a[0], b[1] - a[1]), 0);
  if (!(total > 0)) return [];
  const step = Math.max(total / OUTSIDE_CANDIDATES, precision);
  const eps = Math.max(precision, total * 1e-6);
  const centre = centroidOf(rings) || labelPoint(rings);
  const out = [];
  // A ring keeps the polygon on one side all along, found once per ring
  // (not by an even-odd test beside each edge, nor beside one edge: a part
  // of the polygon narrower than the test's offset, such as a slit, put
  // that test on the wrong side). The winding gives the side of the ring's
  // own inside; it is the polygon's unless the ring lies in an odd number
  // of the others (a hole: even-odd), a majority of a few of its edges'
  // midpoints deciding (a ring may touch another).
  const edges = [];
  for (const [ring, ringEdges] of byRing) {
    let twice = 0;
    for (let i = 0, n = ring.length; i < n; i++) {
      const a = ring[i], b = ring[(i + 1) % n];
      twice += a[0] * b[1] - b[0] * a[1];
    }
    const others = loaded.filter((other) => other !== ring);
    let hole = false;
    if (others.length) {
      const samples = Math.min(3, ringEdges.length);
      let votes = 0;
      for (let k = 0; k < samples; k++) {
        const [a, b] = ringEdges[Math.floor((k + 0.5) * ringEdges.length / samples)];
        if (inside(others, [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2])) votes++;
      }
      hole = 2 * votes > samples;
    }
    // (b1 - a1, a0 - b0) points out of a ring of positive (shoelace) area.
    const flip = (twice < 0) !== hole;
    for (const [a, b] of ringEdges) edges.push([a, b, flip]);
  }
  let offset = step / 2;  // distance along the edges to the next sample
  for (const [a, b, flip] of edges) {
    const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (!length) continue;
    // The edge's normal pointing out of the polygon.
    let [nx, ny] = [(b[1] - a[1]) / length, -(b[0] - a[0]) / length];
    if (flip) [nx, ny] = [-nx, -ny];
    const sx = Math.abs(nx) < SIDE_TOLERANCE ? 0 : Math.sign(nx);
    const sy = Math.abs(ny) < SIDE_TOLERANCE ? 0 : Math.sign(ny);
    const d = sx && sy ? distance * Math.SQRT1_2 : distance;
    for (; offset <= length; offset += step) {
      const t = offset / length;
      const [px, py] = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
      const p = [px + sx * (d + tx), py + sy * (d + ty)];
      const box = [p[0] - half[0], p[1] - half[1], p[0] + half[0], p[1] + half[1]];
      if (view && !(box[0] >= view[0] && box[1] >= view[1] && box[2] <= view[2] && box[3] <= view[3])) continue;
      if (!besidePolygon(loaded, p, [tx, ty], distance, eps)) continue;
      const text = [p[0] - tx, p[1] - ty, p[0] + tx, p[1] + ty];
      const over = obstacles.some((other) => intersect(other.bounds, text) && boxHitsPolygon(other.rings, text));
      out.push({ p, over, cost: centre ? Math.max(0, rectDistance(centre[0], centre[1], box)) : 0 });
    }
    offset -= length;
  }
  return out.sort((x, y) => x.over - y.over || x.cost - y.cost).map(({ p }) => p);
}

// An "outside polygons" label centred at ``point`` (bare text half size
// ``[tx, ty]``) keeps at least half the label distance from the polygon
// (``slack``: rounding, so a label touching the polygon at distance 0 counts).
function besidePolygon(rings, point, [tx, ty], distance, slack = 0) {
  const reach = distance / 2 - Math.max(slack, 1e-12);
  return !boxHitsPolygon(rings, [point[0] - tx - reach, point[1] - ty - reach,
                                 point[0] + tx + reach, point[1] + ty + reach]);
}

// Is a polygon edge (tile cuts are not edges) within ``reach`` of the text
// box centred at ``point`` (half size ``[tx, ty]``)? Apart, a segment and a
// box are nearest at an end of the one or a corner of the other.
function nearEdge(rings, [x, y], [tx, ty], reach) {
  const box = [x - tx, y - ty, x + tx, y + ty];
  const corners = [[box[0], box[1]], [box[2], box[1]], [box[2], box[3]], [box[0], box[3]]];
  const fromBox = ([px, py]) => Math.hypot(Math.max(box[0] - px, 0, px - box[2]), Math.max(box[1] - py, 0, py - box[3]));
  return realEdges(rings).some(([a, b]) => segmentMeetsBox(a, b, grow(box, reach, reach))
    && (segmentMeetsBox(a, b, box) || fromBox(a) <= reach || fromBox(b) <= reach
        || corners.some(([cx, cy]) => segmentDistance(cx, cy, a, b) <= reach)));
}

// Items ({bounds}) in a grid over ``view`` (those off it are dropped):
// query(box) gives the items whose bounds meet the box, without testing
// every item for every query; add(item) adds one.
const INDEX_CELLS = 32;
function boxIndex(items, view) {
  const cells = new Map();
  const [vx, vy] = [view[0], view[1]];
  const [sx, sy] = [(view[2] - view[0]) / INDEX_CELLS || 1, (view[3] - view[1]) / INDEX_CELLS || 1];
  const span = (box) => {
    const part = intersect(box, view);
    if (!part) return null;
    const cell = (v, origin, size) => Math.max(0, Math.min(INDEX_CELLS - 1, Math.floor((v - origin) / size)));
    return [cell(part[0], vx, sx), cell(part[1], vy, sy), cell(part[2], vx, sx), cell(part[3], vy, sy)];
  };
  const add = (item) => {
    const range = span(item.bounds);
    if (!range) return;
    for (let i = range[0]; i <= range[2]; i++) {
      for (let j = range[1]; j <= range[3]; j++) {
        const id = j * INDEX_CELLS + i;
        if (!cells.has(id)) cells.set(id, []);
        cells.get(id).push(item);
      }
    }
  };
  for (const item of items) add(item);
  return {
    add,
    query(box) {
      const range = span(box);
      const found = new Set();
      if (!range) return [];
      for (let i = range[0]; i <= range[2]; i++) {
        for (let j = range[1]; j <= range[3]; j++) {
          for (const item of cells.get(j * INDEX_CELLS + i) || []) if (intersect(item.bounds, box)) found.add(item);
        }
      }
      return [...found];
    },
  };
}

// Does a box touch the polygon (an edge across it, or the box inside it)?
// No edge meets the box: it is wholly inside or wholly outside, so one
// corner tells.
function boxHitsPolygon(rings, box) {
  const [x0, y0, x1, y1] = box;
  if (!(x0 < x1 && y0 < y1)) return false;
  for (const ring of rings) {
    for (let i = 0, n = ring.length; i < n; i++) {
      if (segmentMeetsBox(ring[i], ring[(i + 1) % n], box)) return true;
    }
  }
  return inside(rings, [x0, y0]);
}

// Does a segment touch a box (clipLine's test, without building the pieces)?
function segmentMeetsBox([ax, ay], [bx, by], [x0, y0, x1, y1]) {
  if (Math.max(ax, bx) < x0 || Math.min(ax, bx) > x1 || Math.max(ay, by) < y0 || Math.min(ay, by) > y1) return false;
  const dx = bx - ax, dy = by - ay;
  let t0 = 0, t1 = 1;
  for (const [p, q] of [[-dx, ax - x0], [dx, x1 - ax], [-dy, ay - y0], [dy, y1 - ay]]) {
    if (p === 0) {
      if (q < 0) return false;
    } else {
      const r = q / p;
      if (p < 0) { if (r > t1) return false; if (r > t0) t0 = r; }
      else { if (r < t0) return false; if (r < t1) t1 = r; }
    }
  }
  return true;
}

// Centroid of the rings' area (holes subtract), inside or not.
function centroidOf(rings) {
  let area = 0, cx = 0, cy = 0;
  for (const ring of rings) {
    for (let i = 0, n = ring.length; i < n; i++) {
      const [x1, y1] = ring[i], [x2, y2] = ring[(i + 1) % n];
      const cross = x1 * y2 - x2 * y1;
      area += cross; cx += (x1 + x2) * cross; cy += (y1 + y2) * cross;
    }
  }
  return Math.abs(area) < 1e-22 ? null : [cx / (3 * area), cy / (3 * area)];
}

// The export's pole of inaccessibility (EPSG:3857 metres, ``q2vt_pole_*``)
// when the label cannot fit even there: {point (world units), angle (radians,
// y down, along the polygon), scale}; else null (placed live).
const MERCATOR_SIZE = 2 * 20037508.342789244;
function precomputedSpot(properties, [hx, hy], smallest = 0) {
  const x = Number(properties.q2vt_pole_x), y = Number(properties.q2vt_pole_y);
  const room = Number(properties.q2vt_pole_r) / MERCATOR_SIZE;
  if (!Number.isFinite(x) || !Number.isFinite(y) || !(room > 0) || hy <= room) return null;
  const point = [x / MERCATOR_SIZE + 0.5, 0.5 - y / MERCATOR_SIZE];
  // main_angle: azimuth of the long side (degrees clockwise from north).
  const azimuth = (Number(properties.q2vt_pole_a) || 0) * Math.PI / 180;
  let angle = Math.atan2(-Math.cos(azimuth), Math.sin(azimuth));
  if (angle >= Math.PI / 2) angle -= Math.PI;
  if (angle < -Math.PI / 2) angle += Math.PI;
  const least = Math.min(1, Math.max(SHRINK_STEPS[SHRINK_STEPS.length - 1], smallest));
  return { point, angle, scale: Math.max(least, Math.min(1, room / hy)) };
}

// ``text-size`` times the feature's LABEL_SCALE (1 when absent); a zoom
// curve stays a top-level zoom curve (MapLibre requires it), each output scaled.
export function scaledTextSize(size) {
  const factor = ["to-number", ["coalesce", ["get", LABEL_SCALE], 1], 1];
  if (size === undefined || size === null) size = 16;
  if (typeof size === "number") return ["*", size, factor];
  if (!Array.isArray(size)) return size;  // a legacy function object: unchanged
  const zoomCurve = (size[0] === "interpolate" || size[0] === "interpolate-hcl" || size[0] === "interpolate-lab")
    && Array.isArray(size[2]) && size[2][0] === "zoom";
  if (zoomCurve) {
    // A per-feature size on a zoom curve is evaluated by MapLibre at the
    // curve's stops and packed into a narrow range: stops far apart (0 and
    // 24) ruin it. Numeric curves get a stop at every whole zoom first.
    const stops = [];
    for (let i = 3; i + 1 < size.length; i += 2) stops.push([size[i], size[i + 1]]);
    if (stops.length >= 2 && stops.every(([z, v]) => typeof z === "number" && typeof v === "number")) {
      const base = size[1][0] === "exponential" ? size[1][1] : 1;
      const at = (z) => {
        let k = 0;
        while (k < stops.length - 2 && z > stops[k + 1][0]) k++;
        const [[z0, v0], [z1, v1]] = [stops[k], stops[k + 1]];
        const d = z1 - z0, x = z - z0;
        const t = d === 0 ? 0 : (base === 1 ? x / d : (base ** x - 1) / (base ** d - 1));
        return v0 + (v1 - v0) * t;
      };
      const dense = [];
      for (let z = Math.ceil(stops[0][0]); z <= Math.floor(stops[stops.length - 1][0]); z++) {
        dense.push(z, ["*", at(z), factor]);
      }
      return [size[0], size[1], size[2], ...dense];
    }
    return size.map((v, i) => (i >= 4 && i % 2 === 0 ? ["*", v, factor] : v));
  }
  if (size[0] === "step" && Array.isArray(size[1]) && size[1][0] === "zoom") {
    return size.map((v, i) => (i >= 2 && i % 2 === 0 ? ["*", v, factor] : v));
  }
  return ["*", size, factor];
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

// A label turned by ``angle`` placed wholly inside the polygon, on the
// screen and clear of ``avoid``: candidate spots on a grid along the turned
// axes (anchored to the world, so the same spots after a pan); the best
// centred one wins (most room to the polygon's edges), then the one nearest
// ``target``. A kept spot at the same angle stays while it is still fine and
// nearly as centred. ``loose``: only the label's centre must be inside (QGIS
// without "fit in polygon": a label wider than a narrow strip sticks out of
// it). ``overlap``: with no spot clear of ``avoid``, the best one anyway.
// ``refined(p)``: the kept spot, or ``p`` when that is the same spot from
// finer tiles. Returns {point, angle} or null.
function placeTurned(rings, angle, label, view, avoid, target, old, field, precision = 0,
                     { overlap = true, loose = false, refined = null } = {}) {
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
    if (loose) return inside(rings, p);
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
  if (!loose && (u1 - u0 < 2 * hx || v1 - v0 < 2 * hy)) return null;
  let du = Math.max(hx / 3, precision), dv = Math.max(hy / 2, precision);
  if (loose) {  // a polygon smaller than the label: a few spots across it
    du = Math.max(Math.min(du, (u1 - u0) / 4), precision);
    dv = Math.max(Math.min(dv, (v1 - v0) / 4), precision);
  }
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
      if (loose ? room > 0 : room >= hy) candidates.push({ p, room });  // at least the label's height across
    }
  }
  if (loose && roomAt(rings, target) > 0) candidates.push({ p: target, room: roomAt(rings, target) });
  const bin = Math.max(dv, precision) / 2;  // rooms within half a step count as equal
  candidates.sort((a, b) => (Math.round(b.room / bin) - Math.round(a.room / bin))
    || (Math.hypot(a.p[0] - target[0], a.p[1] - target[1]) - Math.hypot(b.p[0] - target[0], b.p[1] - target[1])));
  let found = null;
  for (let i = 0; i < candidates.length && i < 400 && !found; i++) {
    if (ok(candidates[i].p)) found = candidates[i];
  }
  // Nothing clear of the other labels: the best spot inside anyway, if the
  // label may overlap (QGIS overlap setting) - never outside.
  for (let i = 0; i < candidates.length && i < 400 && !found && overlap; i++) {
    if (ok(candidates[i].p, [])) found = candidates[i];
  }
  if (!found) return null;
  if (old && Math.abs(Number(old.properties?.[field]) - angle * 180 / Math.PI) < 3 && ok(old.point)
      && roomAt(rings, old.point) >= KEEP_ROOM * found.room) {
    return { point: refined ? refined(found.p) : old.point, angle };
  }
  return { point: found.p, angle };
}

// The angle QGIS gives a Free (angled) polygon label (PAL
// Feature::createCandidatesForPolygon), from the oriented bounding box of the
// polygon's convex hull: horizontal when a label twice the size, centred on
// that box, has its corners inside the polygon; else along the box axis
// nearest horizontal when both sides are over 1.5 label widths; else along
// the long side. Radians, y down, in [-90°, 90°).
export function freeAngle(rings, [hx, hy]) {
  const box = orientedBox(rings);
  if (!box) return 0;
  const [cx, cy] = box.center;
  const corners = [[cx - 2 * hx, cy - 2 * hy], [cx + 2 * hx, cy - 2 * hy],
                   [cx + 2 * hx, cy + 2 * hy], [cx - 2 * hx, cy + 2 * hy]];
  if (corners.every((corner) => inside(rings, corner))) return 0;
  const width = 2 * hx;
  const normal = (angle) => {
    while (angle >= Math.PI / 2) angle -= Math.PI;
    while (angle < -Math.PI / 2) angle += Math.PI;
    return angle;
  };
  const a = normal(box.angle), b = normal(box.angle + Math.PI / 2);  // extents box.along, box.across
  if (box.along > 1.5 * width && box.across > 1.5 * width) return Math.abs(a) <= Math.abs(b) ? a : b;
  return box.along >= box.across ? a : b;
}

// Minimum-area rectangle around the rings' convex hull (rotating calipers):
// {angle (of the side measured by ``along``), along, across, center}.
function orientedBox(rings) {
  const points = rings.flat().slice().sort((p, q) => p[0] - q[0] || p[1] - q[1]);
  if (points.length < 3) return null;
  const cross = (o, p, q) => (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0]);
  const half = (list) => {
    const out = [];
    for (const point of list) {
      while (out.length >= 2 && cross(out[out.length - 2], out[out.length - 1], point) <= 0) out.pop();
      out.push(point);
    }
    out.pop();
    return out;
  };
  const hull = half(points).concat(half(points.slice().reverse()));
  if (hull.length < 3) return null;
  let best = null;
  for (let i = 0; i < hull.length; i++) {
    const [ax, ay] = hull[i], [bx, by] = hull[(i + 1) % hull.length];
    const angle = Math.atan2(by - ay, bx - ax);
    const c = Math.cos(angle), s = Math.sin(angle);
    let u0 = Infinity, u1 = -Infinity, v0 = Infinity, v1 = -Infinity;
    for (const [x, y] of hull) {
      const u = x * c + y * s, v = -x * s + y * c;
      if (u < u0) u0 = u; if (u > u1) u1 = u;
      if (v < v0) v0 = v; if (v > v1) v1 = v;
    }
    const area = (u1 - u0) * (v1 - v0);
    if (!best || area < best.area) {
      const u = (u0 + u1) / 2, v = (v0 + v1) / 2;
      best = { area, angle, along: u1 - u0, across: v1 - v0, center: [u * c - v * s, u * s + v * c] };
    }
  }
  return best;
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

// How far apart (world units) two line ends may be and still meet: a tile
// cut's mismatch, a few units of the data's tiles (it grows on the screen
// with every overzoom level), at least one CSS pixel; never a visible gap
// in the line.
function joinTolerance(options) {
  return Math.max(1e-10, options.precision || 0, 4 * (options.tileUnit || 0));
}

// properties -> the label's text as a key: the values of the fields the
// style's text-field reads (one constant text: one key).
export function textKey(expression) {
  const fields = [];
  const find = (value) => {
    if (!Array.isArray(value)) return;
    if (value[0] === "get" && typeof value[1] === "string") fields.push(value[1]);
    else value.forEach(find);
  };
  find(expression);
  return (properties) => JSON.stringify(fields.map((field) => properties[field] ?? null));
}

// "Merge connected lines" (QGIS): features of one label text whose ends
// meet are labelled as one line (pal Layer::joinConnectedFeatures), so a
// river split into short features gets one label along its course - not
// one per feature, nor none where each is shorter than the label. Ends on
// a tile or reach cut (piece.clip) are not line ends. As pal, only simple
// lines are built: the parts of one text longest first, each grown by the
// longest other part with one end on one of its two ends and the other
// end off it (FeaturePart::isConnected; GEOSLineMerge must give one line),
// so side branches and the other arms of a junction stay apart, each with
// its own label - not one label for a whole network. pal tests that other
// end against the whole line, here against the ends of its parts (the
// nodes of a network). byFeature -> the same with each merged line as one
// entry (keyed by its first feature's key).
const MERGED_PREFIX = "\u0001";
// Feature key / part separator of the parts merged by mergeConnected.
const PART_OF = "\u0002";

function mergeConnected(byFeature, textOf, tolerance) {
  const onCut = ([x, y], [x0, y0, x1, y1]) => Math.min(Math.abs(x - x0), Math.abs(x - x1),
                                                       Math.abs(y - y0), Math.abs(y - y1)) <= 1e-12;
  const byText = new Map();
  for (const [key, value] of byFeature) {
    const text = textOf(value.properties);
    if (!byText.has(text)) byText.set(text, []);
    const ends = value.rings.flatMap((piece) => [piece[0], piece[piece.length - 1]]
      .filter((end) => !piece.clip || !onCut(end, piece.clip)));
    const length = value.rings.reduce((sum, piece) => sum + chainLength(piece), 0);
    byText.get(text).push({ key, value, ends, length });
  }
  const meet = (a, b) => Math.abs(a[0] - b[0]) <= tolerance && Math.abs(a[1] - b[1]) <= tolerance;
  const byKey = (a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0);
  // Line ends in a grid of tolerance-sized cells: the parts meeting an end
  // are found by lookup, not by testing every pair (thousands of parts of
  // one text: seconds per update).
  const cell = ([x, y]) => [Math.floor(x / tolerance), Math.floor(y / tolerance)];
  // A part with more than two free ends is no simple line: never merged.
  const simple = (member) => member.ends.length >= 1 && member.ends.length <= 2;
  // A part this short has both ends within the tolerance of the point it
  // joins at: its other end is not checked against the line.
  const tiny = 3 * tolerance;
  const out = new Map();
  for (const members of byText.values()) {
    members.sort((a, b) => b.length - a.length || byKey(a, b));  // ties: the same every frame
    const grid = new Map();
    members.forEach((member, i) => {
      for (const end of member.ends) {
        const k = cell(end).join();
        if (!grid.has(k)) grid.set(k, []);
        grid.get(k).push([i, end]);
      }
    });
    const near = (point) => {  // the parts with an end meeting point
      const [cx, cy] = cell(point);
      const found = [];
      for (let dx = -1; dx <= 1; dx++) {
        for (let dy = -1; dy <= 1; dy++) {
          for (const [i, end] of grid.get(`${cx + dx},${cy + dy}`) || []) if (meet(point, end)) found.push(i);
        }
      }
      return found;
    };
    const lineOf = new Array(members.length).fill(-1);
    for (let seed = 0; seed < members.length; seed++) {
      if (lineOf[seed] >= 0) continue;
      lineOf[seed] = seed;
      const before = [], after = [];  // the parts added at the line's start, at its end
      // The line's two ends (null: a cut, nothing joins there).
      const ends = simple(members[seed]) ? [members[seed].ends[0], members[seed].ends[1] || null] : [null, null];
      // Parts that cannot join this line however it grows.
      const tried = new Set();
      // A closed line (its ends on one point: the same in every tile, under
      // half a tile unit apart) does not grow; a line crumpled within the
      // tolerance (a pixel or so at low zooms) is not closed.
      const closed = () => ends[0] && ends[1]
        && Math.hypot(ends[0][0] - ends[1][0], ends[0][1] - ends[1][1]) <= tolerance / 16;
      while (!closed()) {
        // Each part's end nearest a line end joins there. The parts ending
        // right on it first (pal: exactly), then those only within the
        // tolerance; the longest first.
        const candidates = [...new Set(ends.flatMap((end) => (end ? near(end) : [])))]
          .filter((i) => lineOf[i] < 0 && !tried.has(i)).map((i) => {
            let side = -1, at = -1, gap = Infinity;
            members[i].ends.forEach((end, k) => ends.forEach((own, s) => {
              const d = own && meet(end, own) ? Math.hypot(end[0] - own[0], end[1] - own[1]) : Infinity;
              if (d < gap) [side, at, gap] = [s, k, d];
            }));
            return { i, side, at, gap };
          }).sort((a, b) => a.gap - b.gap || a.i - b.i);
        let joined = false;
        for (const { i, side, at } of candidates) {
          const member = members[i];
          // The other end must be off the line: no loop, no part ending on
          // the line's nodes.
          const other = member.ends.length === 2 ? member.ends[1 - at] : null;
          if (!simple(member) || side < 0
              || (other && member.length > tiny && near(other).some((j) => lineOf[j] === seed))) {
            tried.add(i);
            continue;
          }
          lineOf[i] = seed;
          (side ? after : before).push(member);
          ends[side] = other;
          joined = true;
          break;
        }
        if (!joined) break;
      }
      if (!before.length && !after.length) {
        out.set(members[seed].key, members[seed].value);
        continue;
      }
      // In order along the line: its pieces stitch up without searching.
      const set = [...before.reverse(), members[seed], ...after];
      const first = set.slice().sort(byKey)[0];
      out.set(MERGED_PREFIX + first.key, { properties: first.value.properties,
                                           rings: set.flatMap(({ value }) => value.rings) });
    }
  }
  return out;
}

// Pieces of one line (cut at tile edges and the screen) joined where their
// ends meet. Neighbouring tiles cut their own rounded, buffered copies of a
// line, so the two cuts on a shared tile edge miss each other by up to a
// few tile units: ends within ``tolerance`` (world units) meet.
function stitch(pieces, tolerance = 1e-10) {
  const same = (a, b) => Math.abs(a[0] - b[0]) <= tolerance && Math.abs(a[1] - b[1]) <= tolerance;
  const chains = pieces.map((piece) => piece.slice());
  // Each chain grown at both ends until nothing more meets it (merged lines
  // can bring hundreds of pieces: no restart from the first chain).
  for (let i = 0; i < chains.length; i++) {
    for (let j = i + 1; j < chains.length;) {
      const a = chains[i], b = chains[j];
      let merged = null;
      if (same(a[a.length - 1], b[0])) merged = a.concat(b.slice(1));
      else if (same(a[a.length - 1], b[b.length - 1])) merged = a.concat(b.slice(0, -1).reverse());
      else if (same(a[0], b[b.length - 1])) merged = b.concat(a.slice(1));
      else if (same(a[0], b[0])) merged = a.slice().reverse().concat(b.slice(1));
      if (merged) {
        chains[i] = merged;
        chains.splice(j, 1);
        j = i + 1;
      } else {
        j++;
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

// QGIS's curved label limit (maxCurvedCharAngleIn/Out, here text-max-angle):
// its characters sit on chords of the line, one per character, and two
// next to each other may turn by ``maxAngle`` degrees at most. A corner
// inside a character's chord is shared by the turns on both sides of it.
// The characters here are as wide as half the label's height (about
// 0.55 em), along the label's stretch centred on ``s``; QGIS tries spots
// every few millimetres, so the characters also half a width on.
export function curvedBendsOk(chain, s, half, maxAngle) {
  const advance = half[1];
  if (!(advance > 0) || !(maxAngle > 0)) return true;
  const limit = maxAngle * Math.PI / 180;
  const fits = (shift) => {
    let heading = null, last = null;
    for (let d = s - half[0] + shift; d <= s + half[0] + shift + 1e-12; d += advance) {
      const at = along(chain, Math.max(0, d));
      if (!at) continue;
      if (last) {
        const dx = at.point[0] - last[0], dy = at.point[1] - last[1];
        if (!dx && !dy) continue;
        const next = Math.atan2(dy, dx);
        if (heading !== null) {
          let turn = next - heading;
          while (turn > Math.PI) turn -= 2 * Math.PI;
          while (turn < -Math.PI) turn += 2 * Math.PI;
          if (Math.abs(turn) > limit) return false;
        }
        heading = next;
      }
      last = at.point;
    }
    return true;
  };
  return fits(0) || fits(advance / 2);
}

// The piece of line a curved label is laid out on (MapLibre's
// "line-center"), centred on ``s`` along ``chain``. MapLibre drops a label
// longer than its line, measured in tile pixels: the piece reaches half the
// label times the tile's scale (``curved.scale``) and its layout text size
// (``curved.ratio``) and some room on either side, run on straight past the chain's ends (the label itself fits the
// chain: it is placed so).
const CURVED_ROOM = 1.1;
const CURVED_PAD_PX = 4;
const CURVED_MAX_PX = 256;  // tile pixels
export function labelPath(chain, s, half, curved, properties) {
  if (!chain || chain.length < 2) return null;
  const { perPx = 0, scale = 1 } = curved || {};
  const ratio = curved && curved.ratio ? curved.ratio(properties) : 1;
  const total = chainLength(chain);
  // More of the line where there is (the label's size estimate is not
  // MapLibre's shaping), at most half a tile on either side: the 512 tile
  // buffer then holds the whole piece in every tile it crosses.
  const need = ((half ? half[0] : 0) * ratio * CURVED_ROOM + CURVED_PAD_PX * perPx) * scale;
  const reach = Math.max(need, Math.min(s, total - s, CURVED_MAX_PX * perPx * scale));
  const a = s - reach, b = s + reach;
  const out = [];
  let walked = 0;
  for (let i = 0; i + 1 < chain.length; i++) {
    const [ax, ay] = chain[i], [bx, by] = chain[i + 1];
    const step = Math.hypot(bx - ax, by - ay);
    const from = walked, to = walked + step;
    walked = to;
    if (to < a || from > b || !step) continue;
    const t0 = Math.max(0, (a - from) / step), t1 = Math.min(1, (b - from) / step);
    if (!out.length) out.push([ax + (bx - ax) * t0, ay + (by - ay) * t0]);
    out.push([ax + (bx - ax) * t1, ay + (by - ay) * t1]);
  }
  if (out.length < 2) return null;
  // Past an end: straight on in the direction of the last bit of line.
  const extend = (end, next, by) => {
    const dx = end[0] - next[0], dy = end[1] - next[1], d = Math.hypot(dx, dy);
    return d ? [end[0] + dx / d * by, end[1] + dy / d * by] : null;
  };
  if (a < 0) {
    const p = extend(out[0], out.find((q) => q[0] !== out[0][0] || q[1] !== out[0][1]) || out[1], -a);
    if (p) out.unshift(p);
  }
  if (b > total) {
    const last = out[out.length - 1];
    const p = extend(last, [...out].reverse().find((q) => q[0] !== last[0] || q[1] !== last[1]) || out[out.length - 2],
                     b - total);
    if (p) out.push(p);
  }
  // Written from the label's left end to its right end: MapLibre turns a
  // label running right to left upside up and then puts its text-anchor and
  // text-offset on the other side of the line (an "above" label below it).
  const first = along(chain, Math.max(0, s - (half ? half[0] : 0)));
  const last = along(chain, Math.min(total, s + (half ? half[0] : 0)));
  if (first && last && last.point[0] < first.point[0]) out.reverse();
  return out;
}

// The visible stretches of a line (longest first) and the label spots
// along them: a label along the line (rotated to it) or, for QGIS's
// Horizontal placement on lines (options.level), level and centred on the
// line point; each spot with its box, how far it fits inside the screen
// and whether it keeps clear of ``avoid``.
function lineSpots(loaded, properties, view, avoid, options) {
  const withRotation = (rotation) => (options.rotationField
    ? { ...properties, [options.rotationField]: rotation } : properties);
  const chains = (pieces) => stitch(pieces, joinTolerance(options)).map((chain) => ({ chain, length: chainLength(chain) }))
    .sort((a, b) => b.length - a.length);
  const visible = chains(loaded.flatMap((piece) => clipLine(piece, view)));
  const half = options.labelBox ? options.labelBox(properties) : null;
  const level = Boolean(options.level);
  const candidate = (chain, s) => {
    let at = along(chain, s);
    if (at && level) at = { ...at, rotation: 0 };
    if (at) at = { ...at, chain, s };
    if (!at || !half) return at && { ...at, fit: 0, clear: true, box: null };
    if (options.curved && !curvedBendsOk(chain, s, half, options.curved.maxAngle)) return null;
    const angle = at.rotation * Math.PI / 180;
    const [x, y] = at.point;
    const [ex, ey] = envelope(half, angle);
    const fit = Math.min(x - view[0] - ex, view[2] - ex - x, y - view[1] - ey, view[3] - ey - y);
    const box = [x - ex, y - ey, x + ex, y + ey];
    return { ...at, fit, clear: !avoid.some((other) => intersect(other, box)), box };
  };
  const result = (c) => ({ point: c.point, properties: withRotation(c.rotation), box: c.box,
                           line: options.curved ? { chain: c.chain, s: c.s } : null });
  // Half the label along the line: it must fit along the stretch (a level
  // label only needs its centre on the line). The bare text, as QGIS
  // measures it (the box's LABEL_MARGIN_PX are for keeping labels apart): a
  // dimension line just longer than its label has it along the line.
  const lo = half && !level ? Math.max(half[0] - LABEL_MARGIN_PX * (options.precision || 0), half[0] / 2) : 0;
  // The first clear spot from the middle of [a, b] outwards, in steps of a
  // quarter label (at most 40), and the first that only fits the screen
  // (``partial``: a box that only overlaps the screen, its centre on it).
  const search = (chain, a, b, partial = false) => {
    const minFit = partial ? -Infinity : 0;
    const length = b - a;
    let overlapping = null;
    const step = Math.max(length / 40, half ? half[0] / 2 : length / 20);
    for (let k = 0; k <= 2 * Math.ceil(length / 2 / step); k++) {
      const s = a + length / 2 + (k % 2 ? 1 : -1) * Math.ceil(k / 2) * step;
      if (s < a + lo - 1e-12 || s > b - lo + 1e-12) continue;
      const c = candidate(chain, s);
      if (c && c.fit >= minFit && c.clear) return { clear: c, overlapping };
      if (c && c.fit >= minFit && !overlapping) overlapping = c;
    }
    return { clear: null, overlapping };
  };
  return { chains, visible, half, lo, candidate, result, search, withRotation };
}

// A line label on the visible part of its line: the middle of the longest
// visible stretch if the label fits there (along the line, on the screen)
// and is clear of the labels placed before, else the nearest such spot
// along that stretch or a shorter one; a wholly visible line too short for
// the label gets it level at the line (QGIS degraded placement) if allowed;
// no clear spot: overlapping if QGIS allows it ("if required"), else no label.
function placeOnLine(loaded, properties, old, view, guarded, avoid, options) {
  const { chains, visible, half, lo, candidate, result, search, withRotation } =
    lineSpots(loaded, properties, view, avoid, options);
  if (!visible.length) {  // off the screen: in advance, away from the screen edge
    const whole = chains(loaded)[0];
    const at = whole && along(whole.chain, whole.length / 2);
    if (!at || within(guarded, at.point)) return null;
    return { point: at.point, properties: withRotation(options.level ? 0 : at.rotation),
             line: options.curved ? { chain: whole.chain, s: whole.length / 2 } : null };
  }
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
  // "Allow overlap if required" (QGIS): no clear spot, the best one that
  // fits on the screen overlaps; "Never overlap": no label.
  let overlapping = null;
  for (const { chain, length } of visible) {
    if (length < 2 * lo) break;  // the label does not fit along it (nor along shorter ones)
    const found = search(chain, 0, length);
    if (found.clear) return result(found.clear);
    if (!overlapping) overlapping = found.overlapping;
  }
  // A level label with no clear spot wholly on the screen (map-unit text
  // wider than the view): QGIS keeps candidates that only overlap the map extent
  // (labeling engine "partial candidates", on by default), drawn cut by
  // the screen edge - centred on the visible line as before.
  if (options.level && half) {
    for (const { chain, length } of visible) {
      const found = search(chain, 0, length, true);
      if (found.clear) return result(found.clear);
      if (!overlapping) overlapping = found.overlapping;
    }
  }
  // A wholly visible line too short for its label (a dimension line):
  // with "Allow degraded placement" QGIS draws the label level with its
  // bottom-left corner on the line's interior point (PAL
  // createCandidatePointOnSurface); without it, no label.
  const whole = chains(loaded)[0];
  const tolerance = (options.precision || 0) + 1e-12;
  if (options.degraded && half && whole && whole.length < 2 * lo
      && Math.abs(whole.length - longest.length) <= tolerance) {
    const [px, py] = lineInteriorPoint(whole.chain);
    const [hx, hy] = half;
    const anchor = String(options.textAnchor || "center");
    const ax = px + (anchor.includes("left") ? 0 : anchor.includes("right") ? 2 * hx : hx);
    const ay = py - (anchor.includes("bottom") ? 0 : anchor.includes("top") ? 2 * hy : hy);
    const [cx, cy] = [px + hx, py - hy];
    const box = [cx - hx, cy - hy, cx + hx, cy + hy];
    const fit = Math.min(box[0] - view[0], view[2] - box[2], box[1] - view[1], view[3] - box[3]);
    const spot = { point: [ax, ay], properties: withRotation(0), box };
    if (fit >= 0 && !avoid.some((other) => intersect(other, box))) return spot;
    if (fit >= 0 && options.overlap && !overlapping) return spot;
  }
  return options.overlap && overlapping ? result(overlapping) : null;
}

// A repeated line label (QGIS repeat distance, options.repeat in world
// units) on the visible part of its line, as QGIS lays it out: the line
// clipped to the map extent, its longest part (labelled alone unless every
// part is) cut into equal parts at a whole multiple of the repeat distance
// at least as long as the label (pal Layer::chopFeaturesAtRepeatDistance),
// one label per part, from the part's middle outwards (QGIS's cheapest
// candidates). An empty list when no part has room.
function placeRepeatedOnLine(loaded, properties, view, avoid, options) {
  const taken = avoid.slice();
  // pal chops the line with the label's own box (no margin around it) and
  // keeps the labels of its parts clear of each other by that box; it
  // measures the text with the font at a whole pixel size: 10 pt (13.33 px)
  // as 13 px, 2.5 % narrower - with the margin, labels about as long as the
  // repeat lost every other part.
  const precision = options.precision || 0;
  const drawn = options.labelBox ? options.labelBox(properties) : null;
  let own = null;
  if (drawn) {
    const whole = drawn.size > 0 ? Math.max(1, Math.round(drawn.size)) / drawn.size : 1;
    own = [Math.max(drawn[0] - LABEL_MARGIN_PX * precision, drawn[0] / 2) * whole,
           Math.max(drawn[1] - LABEL_MARGIN_PX * precision, drawn[1] / 2)];
  }
  const { visible, lo, result, search } = lineSpots(loaded, properties, view, taken,
                                                    { ...options, labelBox: own ? () => own : null });
  if (!visible.length) return [];
  const width = own ? 2 * own[0] : 0;
  const out = [];
  for (const { chain, length } of options.perPart ? visible : visible.slice(0, 1)) {
    for (const [a, b] of repeatParts(length, width, options.repeat)) {
      if (b - a < 2 * lo) continue;
      let found = search(chain, a, b);
      if (!found.clear && !found.overlapping && options.level) found = search(chain, a, b, true);
      const spot = found.clear || (options.overlap ? found.overlapping : null);
      if (!spot) continue;
      out.push(result(spot));
      if (spot.box) taken.push(spot.box);
    }
  }
  return out;
}

// (start, end) along a line of the parts pal labels one by one
// (Layer::chopFeaturesAtRepeatDistance; core/label_lines.py repeat_parts).
export function repeatParts(length, labelWidth, repeat) {
  if (repeat > 0 && length > repeat) {
    const interval = repeat * Math.max(1, Math.ceil(labelWidth / repeat));
    const count = Math.floor(length / interval);
    if (count > 1) {
      const size = length / count;
      return Array.from({ length: count }, (_, k) => [k * size, (k + 1) * size]);
    }
  }
  return [[0, length]];
}

// GEOS InteriorPointLine: the interior vertex nearest the line's centroid,
// else (two points) the end nearest it, the first on a tie.
function lineInteriorPoint(chain) {
  let length = 0, cx = 0, cy = 0;
  for (let i = 0; i + 1 < chain.length; i++) {
    const [ax, ay] = chain[i], [bx, by] = chain[i + 1];
    const step = Math.hypot(bx - ax, by - ay);
    length += step;
    cx += step * (ax + bx) / 2;
    cy += step * (ay + by) / 2;
  }
  const centroid = length ? [cx / length, cy / length] : chain[0];
  const nearest = (points) => points.reduce((best, p) => (!best
    || Math.hypot(p[0] - centroid[0], p[1] - centroid[1]) < Math.hypot(best[0] - centroid[0], best[1] - centroid[1])
    ? p : best), null);
  return nearest(chain.slice(1, -1)) || nearest([chain[0], chain[chain.length - 1]]);
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
  // A plain array value (text-offset [0, -1], icon-text-fit-padding
  // [t, r, b, l]) is not an expression: expressions start with an operator.
  if (typeof expression[0] !== "string") return expression;
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
    case "format": {  // the sections' text (their options left out)
      let text = "";
      for (const value of args) {
        if (!(value && typeof value === "object" && !Array.isArray(value))) text += String(ev(value) ?? "");
      }
      return text;
    }
    default: return undefined;
  }
}

// The style layer of a group drawn at this zoom: the label itself, not its
// shadow copy drawn before it (no background frame: framed labels were
// placed and fitted as if unframed).
export function activeLayer(group, zoom) {
  const shown = ({ def }) => (def.minzoom ?? 0) <= zoom && zoom < (def.maxzoom ?? 25);
  const label = ({ def }) => !def.metadata?.["q2vt:shadow-of"];
  return (group.layers.find((l) => shown(l) && label(l)) || group.layers.find(shown)
    || group.layers.find(label) || group.layers[0]).def;
}

function groupShown(map, group, zoom) {
  const def = activeLayer(group, zoom);
  return (def.minzoom ?? 0) <= zoom && zoom < (def.maxzoom ?? 25)
    && map.getLayer(def.id) && map.getLayoutProperty(def.id, "visibility") !== "none";
}

// properties -> [half width, half height] of a label's box in world units,
// estimated from its text, text size and background padding.
// The smallest shrink factor of a label that keeps its text ``minPx`` tall;
// only text sized in map units (a size growing with the zoom), as the
// export's "Hide labels smaller than": screen sizes shrink down to half.
function minTextScale(layout, zoom, minPx) {
  if (!JSON.stringify(layout["text-size"] ?? 16).includes('"zoom"')) return () => 0;
  return (properties) => {
    const size = Number(evaluate(layout["text-size"] ?? 16, zoom, properties));
    return size > 0 ? Math.min(1, minPx / size) : 0;
  };
}

function labelBoxes(map, group, zoom, perPx, fonts) {
  const layer = activeLayer(group, zoom);
  return layoutBoxes(layer.layout || {}, zoom, perPx, Number(layer.metadata?.["q2vt:char-width"]) || 0.6,
                     fonts?.get(layer.metadata?.["q2vt:font"]) || null);
}

// MapLibre's own style layers in drawing order (id, type, source, metadata,
// minzoom, maxzoom; paint and layout by map.getPaintProperty and
// map.getLayoutProperty): map.getStyle() copies the whole style each call,
// and the boxes of MapLibre's labels are taken on every label update.
export function styleLayers(map) {
  return map.getLayersOrder().map((id) => map.getLayer(id)).filter((layer) => layer && layer.type !== "custom");
}

// The label fonts' advance per character and line height (em) measured at
// export: style metadata["q2vt:font-metrics"] -> Map(font -> {advance:
// Map(char -> em), height}); a style layer's metadata["q2vt:font"] names its font.
export function fontMetrics(style) {
  const fonts = new Map();
  for (const [name, entry] of Object.entries(style?.metadata?.["q2vt:font-metrics"] || {})) {
    if (typeof entry?.chars !== "string" || !Array.isArray(entry.advances)) continue;
    const advance = new Map();
    [...entry.chars].forEach((char, i) => {
      if (Number.isFinite(entry.advances[i])) advance.set(char, entry.advances[i] / 1000);
    });
    fonts.set(name, { advance, height: Number(entry.height) || 1.2 });
  }
  return fonts;
}

// Half width / height (world units) of a label's box, with LABEL_MARGIN_PX
// around it. ``metrics`` (fontMetrics): each character's own advance, as
// QGIS measures the label - a label of narrow letters is up to a fifth
// shorter than the mean width says, which decides whether it fits in its
// polygon; ``charWidth``: the font's mean advance per character (em,
// metadata["q2vt:char-width"]), for other characters or without metrics.
const LABEL_MARGIN_PX = 2;
export function layoutBoxes(layout, zoom, perPx, charWidth = 0.6, metrics = null) {
  return (properties) => {
    let sections = textSections(layout["text-field"], zoom, properties);
    if (!sections.some(({ text }) => text)) return null;
    let size = Number(evaluate(layout["text-size"] ?? 16, zoom, properties));
    if (!Number.isFinite(size) || size <= 0) size = 16;
    let pad = evaluate(layout["icon-text-fit-padding"], zoom, properties);
    pad = Array.isArray(pad) && pad.length === 4 && layout["icon-text-fit"] ? pad.map(Number) : [0, 0, 0, 0];
    const transform = evaluate(layout["text-transform"], zoom, properties);
    if (transform === "uppercase") sections = sections.map((p) => ({ ...p, text: p.text.toUpperCase() }));
    else if (transform === "lowercase") sections = sections.map((p) => ({ ...p, text: p.text.toLowerCase() }));
    // Lines of [text, scale] pieces (an HTML label's sections each have
    // their own font scale).
    const lines = [[]];
    for (const { text, scale } of sections) {
      text.split("\n").forEach((piece, i) => {
        if (i) lines.push([]);
        lines[lines.length - 1].push([piece, scale]);
      });
    }
    // A line is as tall as its biggest piece (an empty line: as its text).
    const lineScale = (line) => Math.max(...line.map(([piece, scale]) => (piece || line.length === 1 ? scale : 0)));
    const lineHeight = Number(evaluate(layout["text-line-height"] ?? 1.2, zoom, properties)) || 1.2;
    const scales = lines.map(lineScale);
    let width, height;
    if (metrics) {
      const spacing = Number(evaluate(layout["text-letter-spacing"] ?? 0, zoom, properties)) || 0;
      const ems = (line) => line.reduce((sum, [piece, scale]) => {
        const chars = [...piece];
        return sum + scale * (chars.reduce((total, char) => total + (metrics.advance.get(char) ?? charWidth), 0)
          + spacing * Math.max(0, chars.length - 1));
      }, 0);
      width = Math.max(...lines.map(ems)) * size;
      height = (metrics.height * scales[0] + scales.slice(1).reduce((sum, k) => sum + k * lineHeight, 0)) * size;
    } else {
      const ems = (line) => line.reduce((sum, [piece, scale]) => sum + piece.length * scale, 0);
      width = Math.max(...lines.map(ems)) * charWidth * size;
      height = scales.reduce((sum, k) => sum + k * 1.2, 0) * size;
    }
    const bare = [width / 2 * perPx, height / 2 * perPx];
    // MapLibre fits a frame to its own line box (text-line-height a line);
    // the exporter's padding brings it out to QGIS's ascent and descent.
    if (layout["icon-text-fit"]) height = scales.reduce((sum, k) => sum + k * lineHeight, 0) * size;
    width += pad[1] + pad[3];
    height += pad[0] + pad[2];
    const box = [(width / 2 + LABEL_MARGIN_PX) * perPx, (height / 2 + LABEL_MARGIN_PX) * perPx];
    box.text = bare;  // QGIS measures the "around point" distance from the bare text
    box.size = size;  // CSS px
    return box;
  };
}

// A text-field's text as [{text, scale}] sections: plain text one section;
// "format" (HTML labels) each section with its font-scale; MapLibre's own
// evaluation (a Formatted, from rendered features) likewise.
function textSections(field, zoom, properties) {
  if (Array.isArray(field) && field[0] === "format") {
    const sections = [];
    for (const item of field.slice(1)) {
      if (item && typeof item === "object" && !Array.isArray(item)) {
        if (sections.length && item["font-scale"] !== undefined) {
          const scale = Number(evaluate(item["font-scale"], zoom, properties));
          sections[sections.length - 1].scale = Number.isFinite(scale) && scale > 0 ? scale : 1;
        }
        continue;
      }
      sections.push({ text: String(evaluate(item, zoom, properties) ?? ""), scale: 1 });
    }
    return sections;
  }
  const value = evaluate(field, zoom, properties);
  if (value && typeof value === "object" && Array.isArray(value.sections)) {
    return value.sections.map((section) => ({ text: String(section.text ?? ""),
                                             scale: Number(section.scale) > 0 ? Number(section.scale) : 1 }));
  }
  return [{ text: String(value ?? ""), scale: 1 }];
}

// The boxes a group's labels keep clear of: those placed before them and,
// for line, "around point" and "pole" (Horizontal / Free) labels, the point
// labels MapLibre draws (a park's name keeps clear of a place name in it).
export function avoidFor(group, placed, others) {
  return group.kind === "line" || group.around || group.anchor === "pole"
    ? placed.concat(others()) : placed.slice();
}

// World boxes of the point labels MapLibre draws from the other symbol
// layers (estimated from text and size at the anchor, moved by text-offset;
// rotation, variable anchors and icons ignored).
export function renderedLabelBoxes(map, maplibregl, groups, zoom, perPx, fonts) {
  const ours = new Set();
  for (const group of groups.values()) {
    for (const { def } of group.layers) ours.add(def.id).add(def.id + OVERLAP_SUFFIX);
  }
  const defs = new Map(styleLayers(map).filter((l) => l.type === "symbol" && !ours.has(l.id)
    && map.getLayoutProperty(l.id, "text-field") !== undefined).map((l) => [l.id, l]));
  const layers = [...defs.keys()];
  if (!layers.length) return [];
  const boxes = [];
  for (const f of map.queryRenderedFeatures({ layers })) {
    if (f.geometry.type !== "Point") continue;  // labels along lines: their position is unknown here
    // A rendered feature's ``layer.layout`` is MapLibre's evaluation for
    // that feature (its own text, size and offset, whatever the expression:
    // case, format...): each label is measured on its own. The metadata
    // (the font's widths) comes from the style's definition.
    const layout = f.layer.layout || {};
    const metadata = (defs.get(f.layer.id) || f.layer).metadata || {};
    const half = layoutBoxes(layout, zoom, perPx, Number(metadata["q2vt:char-width"]) || 0.6,
      fonts?.get(metadata["q2vt:font"]) || null)(f.properties);
    if (!half) continue;
    const p = maplibregl.MercatorCoordinate.fromLngLat(f.geometry.coordinates);
    // text-offset (ems of the text size): the label is drawn off its point
    // (a town name 4 mm above its dot).
    const offset = layout["text-variable-anchor"] ? null : evaluate(layout["text-offset"], zoom, f.properties);
    const em = Number(evaluate(layout["text-size"] ?? 16, zoom, f.properties)) || 16;
    let [dx, dy] = Array.isArray(offset) ? offset.map((v) => (Number(v) || 0) * em * perPx) : [0, 0];
    // text-translate (px; an offset in units unlike the text's).
    const translate = evaluate((f.layer.paint || {})["text-translate"], zoom, f.properties);
    if (Array.isArray(translate)) {
      dx += (Number(translate[0]) || 0) * perPx;
      dy += (Number(translate[1]) || 0) * perPx;
    }
    boxes.push([p.x + dx - half[0], p.y + dy - half[1], p.x + dx + half[0], p.y + dy + half[1]]);
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

function union(a, b) {
  if (!a || !b) return a || b;
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])];
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
  // Relative to a vertex: in world units ([0, 1]) the cross products of a
  // small polygon cancel out (its centroid was metres off).
  const [ox, oy] = (rings.find((ring) => ring.length) || [[0, 0]])[0];
  for (const ring of rings) {
    for (let i = 0, n = ring.length; i < n; i++) {
      const [x1, y1] = ring[i], [x2, y2] = ring[(i + 1) % n];
      const cross = (x1 - ox) * (y2 - oy) - (x2 - ox) * (y1 - oy);
      area += cross; cx += (x1 + x2 - 2 * ox) * cross; cy += (y1 + y2 - 2 * oy) * cross;
      if (x1 < minX) minX = x1; if (x1 > maxX) maxX = x1;
      if (y1 < minY) minY = y1; if (y1 > maxY) maxY = y1;
    }
  }
  if (Math.abs(area) < 1e-22) return null;
  const centroid = [ox + cx / (3 * area), oy + cy / (3 * area)];
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
  for (const layer of styleLayers(map)) {
    if (layer.type !== "symbol" || !(layer.metadata && layer.metadata["q2vt:overlap"] === "if-required")) continue;
    const layout = (name) => map.getLayoutProperty(layer.id, name);
    if (layout("text-overlap") === "always") continue;  // placed by the viewer
    if (layout("text-field") !== undefined) map.setLayoutProperty(layer.id, "text-overlap", "cooperative");
    if (layout("icon-image") !== undefined) map.setLayoutProperty(layer.id, "icon-overlap", "cooperative");
    layers.push(layer.id);
  }
  const noop = () => {};
  return { sync: noop, layers, destroy: noop };
}

