// Shadows of point labels. MapLibre places point labels itself, each style
// layer on its own, so the shadow copy (exported under the label, see
// QgisMapLibreStyleExporter._label_shadows) is drawn at every feature but
// stays transparent until its label is found shown there: after each
// placement the labels on screen switch their features' shadows on
// (feature state, by the published feature key).

const KEY = "q2vt_feature_key";
const INTERVAL_MS = 120;

function synced(style) {
  return ((style && style.layers) || []).filter((l) => l.metadata && l.metadata["q2vt:shadow-state"]);
}

// Before the map is made: the shadows' source layers get feature ids from
// the feature key (feature state needs them).
export function prepareShadows(style) {
  for (const layer of synced(style)) {
    const source = style.sources[layer.source];
    if (!source || !layer["source-layer"]) continue;
    if (typeof source.promoteId !== "object" || source.promoteId === null) {
      if (typeof source.promoteId === "string") continue;  // already one for every layer
      source.promoteId = {};
    }
    source.promoteId[layer["source-layer"]] = KEY;
  }
}

export function installShadows(map, style) {
  const items = synced(style).map((l) => ({
    label: l.metadata["q2vt:shadow-of"], source: l.source, sourceLayer: l["source-layer"],
    state: l.metadata["q2vt:shadow-state"], shown: new Set(),
  }));
  if (!items.length) return null;
  const sync = () => {
    for (const item of items) {
      const now = new Set();
      if (map.getLayer(item.label)) {
        for (const feature of map.queryRenderedFeatures({ layers: [item.label] })) {
          if (feature.id !== undefined && feature.id !== null) now.add(feature.id);
        }
      }
      const target = (id) => ({ source: item.source, sourceLayer: item.sourceLayer, id });
      for (const id of item.shown) if (!now.has(id)) map.setFeatureState(target(id), { [item.state]: false });
      for (const id of now) if (!item.shown.has(id)) map.setFeatureState(target(id), { [item.state]: true });
      item.shown = now;
    }
  };
  let timer = null;
  const schedule = () => {
    if (timer) return;
    timer = setTimeout(() => { timer = null; sync(); }, INTERVAL_MS);
  };
  map.on("render", schedule);
  map.on("idle", sync);
  return { sync, items };
}
