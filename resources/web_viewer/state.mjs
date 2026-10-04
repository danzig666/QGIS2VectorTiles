// Viewer state of one publication/release: logical visibility, labels,
// opacity multipliers, attribute filters, selection. Defaults come from the
// manifest; saved preferences (localStorage, per publication and schema)
// apply over them; URL state applies over both. reset() restores the
// published defaults.

const STORAGE_VERSION = 1;

// Layers and groups the publisher locked (cannot be switched off).
export function lockedIds(manifest) {
  return {
    layers: new Set(manifest.layers.filter((l) => l.toggleable === false).map((l) => l.id)),
    groups: new Set(manifest.groups.filter((g) => g.toggleable === false).map((g) => g.id)),
  };
}

export function presets(manifest) {
  return (manifest.themes && Array.isArray(manifest.themes.presets)) ? manifest.themes.presets : [];
}

// A theme preset over a state value (locked layers and groups stay on).
export function applyPreset(manifest, value, preset) {
  const locked = lockedIds(manifest);
  const pick = (section, ids) => {
    const out = { ...value[section] };
    for (const [id, enabled] of Object.entries(preset[section] || {})) {
      if (id in out && typeof enabled === "boolean") out[id] = enabled || (ids ? ids.has(id) : false);
    }
    return out;
  };
  return { ...value, layers: pick("layers", locked.layers), groups: pick("groups", locked.groups),
    rules: { ...Object.fromEntries(manifest.rules.map((r) => [r.id, true])), ...pick("rules") }, theme: preset.id };
}

export function presetMatches(manifest, value, preset) {
  const same = (section) => Object.entries(preset[section] || {}).every(([id, on]) => !(id in value[section]) || value[section][id] === on);
  return same("layers") && same("rules");
}

export function defaults(manifest) {
  const layers = {};
  const opacity = {};
  for (const layer of manifest.layers) {
    layers[layer.id] = layer.initialVisibility !== false || layer.toggleable === false;
    opacity[layer.id] = typeof layer.opacity === "number" ? layer.opacity : 1;
  }
  const groups = Object.fromEntries(manifest.groups.map((g) => [g.id, g.initialVisibility !== false || g.toggleable === false]));
  const rules = Object.fromEntries(manifest.rules.map((r) => [r.id, true]));
  const basemap = manifest.basemap && ((manifest.basemap.flavors || []).length || (manifest.basemap.xyz || []).length)
    ? (manifest.basemap.initial || "none") : "none";
  let value = { layers, groups, rules, labels: true, opacity, filters: {}, selected: null, basemap, theme: null };
  const initial = manifest.themes && manifest.themes.initial;
  const preset = presets(manifest).find((p) => p.id === initial);
  if (preset) value = applyPreset(manifest, value, preset);
  return value;
}

export class ViewerState {
  constructor(manifest) {
    this.manifest = manifest;
    this.key = `q2vt:${manifest.publicationId}:v${STORAGE_VERSION}`;
    this.locked = lockedIds(manifest);
    this.basemaps = new Set(["none", ...((manifest.basemap && manifest.basemap.flavors) || []).map((f) => f.id),
      ...((manifest.basemap && manifest.basemap.xyz) || []).map((x) => x.id)]);
    this.value = defaults(manifest);
    this.listeners = new Set();
  }

  // Locked layers and groups are always on, whatever a link or saved state says.
  guard(value) {
    const layers = { ...value.layers };
    const groups = { ...value.groups };
    for (const id of this.locked.layers) if (id in layers) layers[id] = true;
    for (const id of this.locked.groups) if (id in groups) groups[id] = true;
    return { ...value, layers, groups };
  }

  applyTheme(id) {
    const preset = presets(this.manifest).find((p) => p.id === id);
    if (!preset) return;
    this.value = this.guard(applyPreset(this.manifest, this.value, preset));
    this.save();
    this.emit("theme");
  }

  onChange(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }

  emit(reason) {
    for (const listener of this.listeners) listener(this.value, reason);
  }

  set(patch, reason = "change") {
    this.value = this.guard({ ...this.value, ...patch });
    this.save();
    this.emit(reason);
  }

  setIn(section, id, value, reason = section) {
    this.value = this.guard({ ...this.value, [section]: { ...this.value[section], [id]: value } });
    this.save();
    this.emit(reason);
  }

  reset() {
    this.value = defaults(this.manifest);
    try { localStorage.removeItem(this.key); } catch { /* storage unavailable */ }
    this.emit("reset");
  }

  // Only known ids and well-typed values are accepted (saved or URL state).
  merge(partial) {
    const base = this.value;
    const pick = (section, check) => {
      const out = { ...base[section] };
      for (const [id, value] of Object.entries((partial && partial[section]) || {})) {
        if (id in base[section] && check(value)) out[id] = value;
      }
      return out;
    };
    const bool = (v) => typeof v === "boolean";
    const unit = (v) => typeof v === "number" && v >= 0 && v <= 1;
    this.value = {
      ...base,
      layers: pick("layers", bool), groups: pick("groups", bool), rules: pick("rules", bool),
      opacity: pick("opacity", unit),
      labels: typeof partial?.labels === "boolean" ? partial.labels : base.labels,
      filters: partial?.filters && typeof partial.filters === "object" ? sanitizeFilters(this.manifest, partial.filters) : base.filters,
      selected: partial?.selected !== undefined ? partial.selected : base.selected,
      basemap: typeof partial?.basemap === "string" && this.basemaps.has(partial.basemap) ? partial.basemap : base.basemap,
      theme: null,
    };
    this.value = this.guard(this.value);
  }

  load() {
    try {
      const raw = localStorage.getItem(this.key);
      if (raw) this.merge(JSON.parse(raw));
    } catch { /* storage unavailable or corrupt */ }
  }

  save() {
    try {
      const { layers, groups, rules, labels, opacity, filters, basemap } = this.value;
      // No attribute records or selections: only presentation preferences.
      localStorage.setItem(this.key, JSON.stringify({ layers, groups, rules, labels, opacity, filters, basemap }));
    } catch { /* storage unavailable */ }
  }
}

// Filters: {layerId: {field: {values:[..]} | {min,max} | {text}}}, limited
// to configured fields and published values (URL state is untrusted).
export function sanitizeFilters(manifest, filters) {
  const out = {};
  for (const layer of manifest.layers) {
    const wanted = filters[layer.id];
    if (!wanted || typeof wanted !== "object") continue;
    for (const config of layer.filterFields || []) {
      const value = wanted[config.field];
      if (!value || typeof value !== "object") continue;
      let clean = null;
      if (config.kind === "values" && Array.isArray(value.values)) {
        const allowed = new Set((config.values || []).map((v) => v[0]));
        const values = value.values.filter((v) => typeof v === "string" && (allowed.has(v) || config.complete === false)).slice(0, 200);
        if (values.length || value.nulls) clean = { values, nulls: !!value.nulls };
      } else if (config.kind === "range") {
        const min = Number.isFinite(value.min) ? value.min : null;
        const max = Number.isFinite(value.max) ? value.max : null;
        if (min !== null || max !== null) clean = { min, max };
      } else if (config.kind === "text" && typeof value.text === "string" && value.text.trim()) {
        clean = { text: value.text.slice(0, 100) };
      }
      if (clean) (out[layer.id] ||= {})[config.field] = clean;
    }
  }
  return out;
}
