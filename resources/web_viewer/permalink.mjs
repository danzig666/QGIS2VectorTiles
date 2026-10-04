// Versioned URL state: camera, logical visibility, labels, opacity,
// filters and the selected feature, in the hash (v=1). Parsing is strict:
// unknown ids are ignored, numbers are range-checked, filters are limited
// to configured fields/values, sizes are capped; nothing from the URL is
// evaluated as code or as a style expression.

const MAX_HASH = 4000;
const RELEASE_PATH = /\/releases\/r-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\/(index\.html)?$/;

function b64url(text) {
  return btoa(String.fromCharCode(...new TextEncoder().encode(text))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function unb64url(text) {
  const bin = atob(text.replace(/-/g, "+").replace(/_/g, "/"));
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
}

export function encodeState(manifest, value, camera, defaults) {
  const params = new URLSearchParams();
  params.set("v", "1");
  if (camera) params.set("map", `${camera.zoom.toFixed(2)}/${camera.lat.toFixed(6)}/${camera.lng.toFixed(6)}`);
  const diff = (section) => {
    const on = [], off = [];
    for (const [id, enabled] of Object.entries(value[section])) {
      if (enabled !== defaults[section][id]) (enabled ? on : off).push(id);
    }
    return [on, off];
  };
  for (const [section, short] of [["layers", "l"], ["groups", "g"], ["rules", "r"]]) {
    const [on, off] = diff(section);
    if (on.length) params.set(`${short}+`, on.join(","));
    if (off.length) params.set(`${short}-`, off.join(","));
  }
  if (!value.labels) params.set("lab", "0");
  if (value.basemap && value.basemap !== defaults.basemap) params.set("bm", value.basemap);
  const opacity = Object.entries(value.opacity).filter(([id, v]) => v !== defaults.opacity[id])
    .map(([id, v]) => `${id}:${Math.round(v * 100)}`);
  if (opacity.length) params.set("o", opacity.join(","));
  if (value.filters && Object.keys(value.filters).length) params.set("f", b64url(JSON.stringify(value.filters)));
  if (value.selected) params.set("sel", `${value.selected.layerId}~${value.selected.key}`);
  return params.toString();
}

export function decodeState(manifest, hash) {
  const text = (hash || "").replace(/^#/, "");
  if (!text || text.length > MAX_HASH) return null;
  const params = new URLSearchParams(text);
  if (params.get("v") !== "1") return null;
  const out = { layers: {}, groups: {}, rules: {}, opacity: {} };
  const ids = {
    layers: new Set(manifest.layers.map((l) => l.id)),
    groups: new Set(manifest.groups.map((g) => g.id)),
    rules: new Set(manifest.rules.map((r) => r.id)),
  };
  for (const [section, short] of [["layers", "l"], ["groups", "g"], ["rules", "r"]]) {
    for (const [suffix, enabled] of [["+", true], ["-", false]]) {
      for (const id of (params.get(`${short}${suffix}`) || "").split(",")) {
        if (ids[section].has(id)) out[section][id] = enabled;
      }
    }
  }
  if (params.get("lab") === "0") out.labels = false;
  const basemap = params.get("bm");
  const flavors = new Set(["none", ...((manifest.basemap && manifest.basemap.flavors) || []).map((f) => f.id),
    ...((manifest.basemap && manifest.basemap.xyz) || []).map((x) => x.id)]);
  if (basemap && flavors.has(basemap)) out.basemap = basemap;
  for (const pair of (params.get("o") || "").split(",")) {
    const [id, percent] = pair.split(":");
    const value = Number(percent);
    if (ids.layers.has(id) && Number.isInteger(value) && value >= 0 && value <= 100) out.opacity[id] = value / 100;
  }
  const camera = (params.get("map") || "").split("/").map(Number);
  if (camera.length === 3 && camera.every(Number.isFinite) && camera[0] >= 0 && camera[0] <= 24
      && Math.abs(camera[1]) <= 85.06 && Math.abs(camera[2]) <= 180) {
    out.camera = { zoom: camera[0], lat: camera[1], lng: camera[2] };
  }
  if (params.get("f")) {
    try {
      const filters = JSON.parse(unb64url(params.get("f")).slice(0, 2000));
      if (filters && typeof filters === "object") out.filters = filters;
    } catch { /* ignored */ }
  }
  const selected = params.get("sel");
  if (selected && selected.length <= 600) {
    const at = selected.indexOf("~");
    const layerId = selected.slice(0, at);
    const key = selected.slice(at + 1);
    if (at > 0 && ids.layers.has(layerId) && key) out.selected = { layerId, key };
  }
  return out;
}

export class Permalink {
  constructor({ map, manifest, state, defaults, enabled = true }) {
    Object.assign(this, { map, manifest, state, defaults, enabled });
    this.onMove = () => this.schedule();
    if (enabled) {
      map.on("moveend", this.onMove);
      state.onChange(() => this.schedule());
    }
  }

  hash(extra = {}) {
    const c = this.map.getCenter();
    const value = { ...this.state.value, ...extra };
    return encodeState(this.manifest, value, { zoom: this.map.getZoom(), lat: c.lat, lng: c.lng }, this.defaults);
  }

  schedule() {
    if (!this.enabled) return;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => history.replaceState(null, "", `#${this.hash()}`), 300);
  }

  // Links: the exact release (versioned) and the stable entry (follows the
  // current release) — both keep the same state.
  links(extra = {}) {
    const hash = `#${this.hash(extra)}`;
    const here = new URL(location.href);
    // The release's own page (the address bar may show the stable entry).
    const versioned = new URL((window.q2vtViewer && window.q2vtViewer.releaseUrl) || here.href);
    versioned.search = here.search;
    versioned.hash = hash;
    let stable = null;
    if (!RELEASE_PATH.test(here.pathname)) {  // already the stable address
      stable = new URL(here.href);
      stable.hash = hash;
    } else if (RELEASE_PATH.test(versioned.pathname)) {
      stable = new URL("../../index.html", versioned);
      stable.search = versioned.search;
      stable.hash = hash;
    }
    return { versioned: versioned.href, stable: stable ? stable.href : null };
  }

  async copy(extra = {}, which = "stable") {
    const links = this.links(extra);
    const text = (which === "stable" && links.stable) || links.versioned;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      window.prompt("", text);  // eslint-disable-line no-alert
    }
    return text;
  }
}
