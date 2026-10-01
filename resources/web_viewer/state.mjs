// Viewer state of one publication/release: logical visibility, labels,
// opacity multipliers, attribute filters, selection. Defaults come from the
// manifest; saved preferences (localStorage, per publication and schema)
// apply over them; URL state applies over both. reset() restores the
// published defaults.

const STORAGE_VERSION = 1;

export function defaults(manifest) {
  const layers = {};
  const opacity = {};
  for (const layer of manifest.layers) {
    layers[layer.id] = layer.initialVisibility !== false;
    opacity[layer.id] = typeof layer.opacity === "number" ? layer.opacity : 1;
  }
  const groups = Object.fromEntries(manifest.groups.map((g) => [g.id, true]));
  const rules = Object.fromEntries(manifest.rules.map((r) => [r.id, true]));
  return { layers, groups, rules, labels: true, opacity, filters: {}, selected: null };
}

export class ViewerState {
  constructor(manifest) {
    this.manifest = manifest;
    this.key = `q2vt:${manifest.publicationId}:v${STORAGE_VERSION}`;
    this.value = defaults(manifest);
    this.listeners = new Set();
  }

  onChange(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }

  emit(reason) {
    for (const listener of this.listeners) listener(this.value, reason);
  }

  set(patch, reason = "change") {
    this.value = { ...this.value, ...patch };
    this.save();
    this.emit(reason);
  }

  setIn(section, id, value, reason = section) {
    this.value = { ...this.value, [section]: { ...this.value[section], [id]: value } };
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
    };
  }

  load() {
    try {
      const raw = localStorage.getItem(this.key);
      if (raw) this.merge(JSON.parse(raw));
    } catch { /* storage unavailable or corrupt */ }
  }

  save() {
    try {
      const { layers, groups, rules, labels, opacity, filters } = this.value;
      // No attribute records or selections: only presentation preferences.
      localStorage.setItem(this.key, JSON.stringify({ layers, groups, rules, labels, opacity, filters }));
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
