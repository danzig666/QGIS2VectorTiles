// Applies the viewer state to the map style without rebuilding it.
//
// Every change is derived from the immutable original style-layer
// definitions captured at mount time (never by re-wrapping an already
// modified expression): visibility from logical layer/group/rule/label
// state, attribute filters composed with the original filter, opacity as a
// multiplier of the original paint values (zoom curves stay top-level;
// multiplier 1 restores the original value exactly).

const OPACITY = {
  fill: ["fill-opacity"],
  line: ["line-opacity"],
  circle: ["circle-opacity", "circle-stroke-opacity"],
  symbol: ["text-opacity", "icon-opacity"],
  "fill-extrusion": ["fill-extrusion-opacity"],
  raster: ["raster-opacity"],
};
const RUNTIME_SUFFIXES = ["_q2vt_overlap"];
export const LOADER_PREFIX = "q2vt_visible_loader_";

export function scaleValue(value, factor) {
  if (factor === 1) return value;
  if (value === undefined || value === null) return factor;
  if (typeof value === "number") return value * factor;
  if (Array.isArray(value) && (value[0] === "interpolate" || value[0] === "step")) {
    const input = value[0] === "interpolate" ? 2 : 1;
    if (Array.isArray(value[input]) && value[input][0] === "zoom") {
      const out = value.slice();
      for (let i = input + 2; i < out.length; i += 2) out[i] = scaleValue(out[i], factor);
      if (value[0] === "step") out[2] = scaleValue(value[2], factor);
      return out;
    }
  }
  if (Array.isArray(value)) return ["*", value, factor];
  return value; // legacy function objects are left unchanged
}

// Legacy (pre-expression) filters cannot be nested in an expression "all".
export function isLegacyFilter(filter) {
  if (!Array.isArray(filter)) return false;
  const [op, a] = filter;
  if (["all", "any", "none"].includes(op)) return filter.slice(1).some(isLegacyFilter);
  if (["has", "!has"].includes(op)) return typeof a === "string";
  if (["==", "!=", "<", ">", "<=", ">=", "in", "!in"].includes(op)) return typeof a === "string";
  return false;
}

export function filterPredicate(layer, filters) {
  const active = filters[layer.id];
  if (!active) return null;
  const parts = [];
  for (const config of layer.filterFields || []) {
    const value = active[config.field];
    if (!value) continue;
    const get = ["get", config.field];
    if (config.kind === "values") {
      const options = [];
      if (value.values && value.values.length) options.push(["in", ["to-string", get], ["literal", value.values]]);
      if (value.nulls) options.push(["==", get, null]);
      if (options.length) parts.push(options.length === 1 ? options[0] : ["any", ...options]);
    } else if (config.kind === "range") {
      const number = ["to-number", get, NaN];
      if (value.min !== null && value.min !== undefined) parts.push([">=", number, value.min]);
      if (value.max !== null && value.max !== undefined) parts.push(["<=", number, value.max]);
    } else if (config.kind === "text" && value.text) {
      parts.push(["in", value.text.toLowerCase(), ["downcase", ["to-string", ["coalesce", get, ""]]]]);
    }
  }
  if (!parts.length) return null;
  return parts.length === 1 ? parts[0] : ["all", ...parts];
}

export class StyleControl {
  constructor(map, manifest) {
    this.map = map;
    this.manifest = manifest;
    this.layers = new Map(manifest.layers.map((l) => [l.id, l]));
    this.rules = new Map(manifest.rules.map((r) => [r.id, r]));
    this.groups = new Map(manifest.groups.map((g) => [g.id, g]));
    this.components = new Map(manifest.components.map((c) => [c.id, c]));
    this.owner = new Map(); // style layer id -> component
    this.originals = new Map(); // style layer id -> {filter, paint: {prop: value}}
    const style = map.getStyle();
    const ids = new Set(style.layers.map((l) => l.id));
    for (const component of manifest.components) {
      for (const id of component.styleLayerIds) {
        for (const styleId of [id, ...RUNTIME_SUFFIXES.map((s) => id + s)]) {
          if (!ids.has(styleId)) continue;
          this.owner.set(styleId, component);
          const def = style.layers.find((l) => l.id === styleId);
          const paint = {};
          for (const prop of OPACITY[def.type] || []) paint[prop] = def.paint ? def.paint[prop] : undefined;
          this.originals.set(styleId, { filter: def.filter, paint, minzoom: def.minzoom ?? 0, maxzoom: def.maxzoom ?? 24, type: def.type });
        }
      }
    }
    this.applied = new Map(); // style layer id -> {visible, filterKey, opacity}
  }

  groupEnabled(groupId, state) {
    let id = groupId;
    while (id) {
      if (state.groups[id] === false) return false;
      id = this.groups.get(id)?.parentId || null;
    }
    return true;
  }

  ruleEnabled(ruleId, state) {
    let id = ruleId;
    while (id) {
      if (state.rules[id] === false) return false;
      id = this.rules.get(id)?.parentId || null;
    }
    return true;
  }

  layerEnabled(layerId, state) {
    const layer = this.layers.get(layerId);
    return !!layer && state.layers[layerId] !== false && this.groupEnabled(layer.groupId, state)
      && (state.opacity[layerId] ?? 1) > 0;
  }

  componentVisible(component, state) {
    if (!this.layerEnabled(component.layerId, state)) return false;
    if ((component.role === "label" || component.role === "callout") && !state.labels) return false;
    // Shared ownership: visible while any owning rule is enabled.
    if (component.ruleIds && component.ruleIds.length) return component.ruleIds.some((r) => this.ruleEnabled(r, state));
    return true;
  }

  // Whether a layer / rule draws anything at this zoom (scale state, shown
  // separately from the checkbox).
  availableAt(componentIds, zoom) {
    for (const cid of componentIds || []) {
      const component = this.components.get(cid);
      for (const id of component ? component.styleLayerIds : []) {
        const original = this.originals.get(id);
        if (original && zoom >= original.minzoom && zoom < original.maxzoom) return true;
      }
    }
    return false;
  }

  // Eligibility predicate for the visible-polygon label helper: polygons of
  // hidden or filtered-out features get no label.
  eligibility(state) {
    const byHelper = new Map(); // helper source layer -> [{component, predicate}]
    for (const component of this.manifest.components) {
      for (const helper of component.dependsOnSourceLayers || []) {
        const layer = this.layers.get(component.layerId);
        const list = byHelper.get(helper) || [];
        list.push({ visible: this.componentVisible(component, state), filters: layer ? state.filters[layer.id] : null, layer });
        byHelper.set(helper, list);
      }
    }
    return (properties, helper) => {
      const owners = byHelper.get(helper);
      if (!owners) return true;
      return owners.some((o) => o.visible && matchesFilters(o.layer, o.filters, properties));
    };
  }

  apply(state) {
    const map = this.map;
    const loaderNeeded = new Map();
    for (const [styleId, component] of this.owner) {
      if (!map.getLayer(styleId)) continue;
      const original = this.originals.get(styleId);
      const visible = this.componentVisible(component, state);
      const layer = this.layers.get(component.layerId);
      const predicate = layer ? filterPredicate(layer, state.filters) : null;
      let filter = original.filter;
      if (predicate) filter = original.filter ? (isLegacyFilter(original.filter) ? original.filter : ["all", original.filter, predicate]) : predicate;
      const factor = state.opacity[component.layerId] ?? 1;
      const before = this.applied.get(styleId) || {};
      const filterKey = JSON.stringify(filter ?? null);
      if (before.visible !== visible) map.setLayoutProperty(styleId, "visibility", visible ? "visible" : "none");
      if (before.filterKey !== filterKey) map.setFilter(styleId, filter ?? null);
      if (before.factor !== factor) {
        for (const [prop, value] of Object.entries(original.paint)) {
          map.setPaintProperty(styleId, prop, scaleValue(value, factor));
        }
      }
      this.applied.set(styleId, { visible, filterKey, factor });
      for (const helper of component.dependsOnSourceLayers || []) {
        loaderNeeded.set(helper, (loaderNeeded.get(helper) || false) || visible);
      }
    }
    for (const [helper, needed] of loaderNeeded) {
      const id = LOADER_PREFIX + helper;
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", needed ? "visible" : "none");
    }
  }
}

export function matchesFilters(layer, filters, properties) {
  if (!layer || !filters) return true;
  for (const config of layer.filterFields || []) {
    const value = filters[config.field];
    if (!value) continue;
    const raw = properties ? properties[config.field] : undefined;
    if (config.kind === "values") {
      const isNull = raw === null || raw === undefined;
      if (isNull ? !value.nulls : !(value.values || []).includes(String(raw))) return false;
    } else if (config.kind === "range") {
      const number = Number(raw);
      if (raw === null || raw === undefined || Number.isNaN(number)) return false;
      if (value.min !== null && value.min !== undefined && number < value.min) return false;
      if (value.max !== null && value.max !== undefined && number > value.max) return false;
    } else if (config.kind === "text" && value.text) {
      if (!String(raw ?? "").toLowerCase().includes(value.text.toLowerCase())) return false;
    }
  }
  return true;
}
