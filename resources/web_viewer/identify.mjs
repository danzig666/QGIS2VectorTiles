// Identify, popups and highlighting of original features.
//
// Clicks query only manifest-declared component style layers, then
// deduplicate hits by (logical layer, q2vt_feature_key) so a fill, outline,
// hatch and label of one parcel give one record; overlapping features get a
// chooser in topmost-first order. Popup values are DOM text (textContent);
// URLs only with http(s)/mailto and noopener. Highlights are overlay layers
// filtered by the feature key (all tile-clipped copies), never a change of
// the base symbols.
import { t, formatNumber } from "./i18n.mjs";
import { matchesFilters } from "./style_control.mjs";

const KEY = "q2vt_feature_key";
const SAFE_URL = /^(https?:|mailto:)/i;
const HL = "q2vt_hl_";

export function formatValue(value, type) {
  if (value === null || value === undefined) return { text: t("popup.null"), muted: true };
  if (value === "") return { text: t("popup.empty"), muted: true };
  if (type === "boolean" || typeof value === "boolean") return { text: value ? t("popup.true") : t("popup.false") };
  if ((type === "number" || type === "integer") && typeof value === "number") return { text: formatNumber(value) };
  if (type === "url" && typeof value === "string" && SAFE_URL.test(value.trim())) return { text: value, url: value.trim() };
  return { text: String(value) };
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export class Identify {
  constructor(ctx) {
    Object.assign(this, ctx); // map, manifest, maplibregl, control, state, lookup, viewer
    this.layers = new Map(this.manifest.layers.map((l) => [l.id, l]));
    this.popup = new this.maplibregl.Popup({ maxWidth: "380px", focusAfterOpen: true, closeOnClick: false });
    this.popup.on("close", () => this.select(null));
    this.overlays = new Set();
    this.hoverKey = null;
    this.onClick = (event) => this.click(event);
    this.onMove = (event) => this.hover(event);
    this.onLeave = () => this.setHover(null);
    this.map.on("click", this.onClick);
    if (window.matchMedia && window.matchMedia("(hover: hover)").matches) {
      this.map.on("mousemove", this.onMove);
      this.map.getCanvas().addEventListener("mouseleave", this.onLeave);
    }
  }

  interactiveLayerIds() {
    const ids = [];
    for (const [styleId, component] of this.control.owner) {
      if (!component.interactive && component.role !== "label") continue;
      if (this.map.getLayer(styleId) && this.map.getLayoutProperty(styleId, "visibility") !== "none") ids.push(styleId);
    }
    return ids;
  }

  hits(point, tolerance = 4) {
    const layers = this.interactiveLayerIds();
    if (!layers.length) return [];
    const box = [[point.x - tolerance, point.y - tolerance], [point.x + tolerance, point.y + tolerance]];
    const seen = new Map();
    for (const feature of this.map.queryRenderedFeatures(box, { layers })) {
      const component = this.control.owner.get(feature.layer.id);
      if (!component) continue;
      const key = feature.properties ? feature.properties[KEY] : undefined;
      if (key === undefined || key === null) continue;
      const id = `${component.layerId}\u0000${key}`;
      if (!seen.has(id)) seen.set(id, { layerId: component.layerId, key: String(key), feature });
    }
    return [...seen.values()]; // topmost first (queryRenderedFeatures order)
  }

  async click(event) {
    if (this.viewer.measuring) return;
    const hits = this.hits(event.point);
    if (!hits.length) {
      this.popup.remove();
      return;
    }
    if (hits.length === 1) {
      await this.open(hits[0].layerId, hits[0].key, event.lngLat, hits[0].feature);
      return;
    }
    const box = element("div", "q2vt-popup");
    box.append(element("h3", "", t("popup.choose")));
    const list = element("div", "q2vt-choices");
    for (const hit of hits.slice(0, 12)) {
      const layer = this.layers.get(hit.layerId);
      const button = element("button", "q2vt-choice", `${layer ? layer.title : ""}: ${hit.key}`);
      button.type = "button";
      button.addEventListener("click", () => this.open(hit.layerId, hit.key, event.lngLat, hit.feature));
      list.append(button);
      this.lookup.get(hit.layerId, hit.key).then((record) => {
        if (record && record.t) button.textContent = `${layer ? layer.title : ""}: ${record.t}`;
      }).catch(() => {});
    }
    box.append(list);
    this.popup.setLngLat(event.lngLat).setDOMContent(box).addTo(this.map);
  }

  async open(layerId, key, lngLat, feature = null) {
    const layer = this.layers.get(layerId);
    let record = null;
    try { record = await this.lookup.get(layerId, key); } catch { record = null; }
    const box = element("div", "q2vt-popup");
    box.append(element("h3", "", (record && record.t) || (layer ? layer.title : key)));
    if (layer) box.append(element("div", "q2vt-popup-layer", layer.title));
    const fields = layer ? layer.popupFields || [] : [];
    if (record && fields.length) {
      const table = element("table");
      for (const field of fields) {
        const row = element("tr");
        row.append(element("th", "", field.title || field.field));
        const cell = element("td");
        const value = formatValue(record.a ? record.a[field.field] : undefined, field.type);
        if (value.url) {
          const link = element("a", "", value.text);
          link.href = value.url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          cell.append(link);
        } else {
          cell.textContent = value.text;
          if (value.muted) cell.className = "q2vt-muted";
        }
        row.append(cell);
        table.append(row);
      }
      box.append(table);
    } else if (!record && fields.length) {
      box.append(element("p", "q2vt-note", t("popup.none")));
    }
    const state = this.state.value;
    if (layer && !this.control.layerEnabled(layerId, state)) {
      box.append(element("p", "q2vt-note", t("popup.layerHidden")));
      const show = element("button", "q2vt-chip", t("popup.showLayer"));
      show.type = "button";
      show.addEventListener("click", () => {
        this.state.setIn("layers", layerId, true);
        if ((state.opacity[layerId] ?? 1) === 0) this.state.setIn("opacity", layerId, 1);
        if (layer.groupId) {
          let id = layer.groupId;
          while (id) { this.state.setIn("groups", id, true); id = this.control.groups.get(id)?.parentId || null; }
        }
      });
      box.append(show);
    } else if (layer && feature && !matchesFilters(layer, state.filters[layerId], feature.properties)) {
      box.append(element("p", "q2vt-note", t("popup.hiddenByFilter")));
    } else if (layer && record && !feature && !matchesFilters(layer, state.filters[layerId], record.a)) {
      box.append(element("p", "q2vt-note", t("popup.hiddenByFilter")));
    }
    if (layer && layer.identityScope === "export") box.append(element("p", "q2vt-note", t("feature.exportScoped")));
    if (layer && layer.deepLinks && this.viewer.permalink) {
      const link = element("button", "q2vt-chip q2vt-chip-ghost", t("popup.link"));
      link.type = "button";
      link.addEventListener("click", () => this.viewer.permalink.copy({ selected: { layerId, key } }));
      box.append(link);
    }
    const where = lngLat || (record && record.p) || this.map.getCenter();
    this.popup.setLngLat(where).setDOMContent(box).addTo(this.map);
    this.select({ layerId, key });
  }

  accent() {
    const value = getComputedStyle(document.documentElement).getPropertyValue("--q2vt-accent").trim();
    return /^#[0-9a-f]{6}$/i.test(value) ? value : "#2563eb";
  }

  ensureOverlay(sourceLayer, sourceId, kind) {
    const id = `${HL}${kind}_${sourceLayer}`;
    if (this.map.getLayer(id)) return id;
    const base = { id, source: sourceId, "source-layer": sourceLayer, filter: ["==", ["get", KEY], "\u0000"] };
    const hover = kind.startsWith("hover");
    if (kind.endsWith("point")) {
      this.map.addLayer({ ...base, type: "circle", paint: { "circle-radius": hover ? 7 : 9, "circle-color": "rgba(0,0,0,0)",
        "circle-stroke-color": hover ? "#f59e0b" : this.accent(), "circle-stroke-width": hover ? 2 : 3 } });
    } else {
      this.map.addLayer({ ...base, type: "line", paint: { "line-color": hover ? "#f59e0b" : this.accent(),
        "line-width": hover ? 2 : 3.5, "line-opacity": 0.95 } });
    }
    this.overlays.add(id);
    return id;
  }

  // Overlay source layers of a logical layer: its original geometry dataset
  // (geometry components; the visible-polygon helper for label-only layers),
  // never individual hatch/marker elements.
  overlayTargets(layerId) {
    const components = this.manifest.components.filter((c) => c.layerId === layerId && c.sourceLayer);
    const geometry = components.filter((c) => c.role === "geometry");
    const chosen = geometry.length ? geometry : components.filter((c) => c.role === "decoration");
    const targets = new Map();
    for (const c of chosen) targets.set(c.sourceLayer, c.sourceId);
    if (!targets.size) {
      for (const c of components) for (const helper of c.dependsOnSourceLayers || []) targets.set(helper, c.sourceId);
    }
    return [...targets.entries()];
  }

  setOverlay(prefix, selection) {
    for (const id of this.overlays) {
      if (id.startsWith(HL + prefix)) this.map.setFilter(id, ["==", ["get", KEY], "\u0000"]);
    }
    if (!selection) return;
    const layer = this.layers.get(selection.layerId);
    const point = layer && layer.geometry === "point";
    for (const [sourceLayer, sourceId] of this.overlayTargets(selection.layerId)) {
      const id = this.ensureOverlay(sourceLayer, sourceId, `${prefix}_${point ? "point" : "line"}`);
      this.map.setFilter(id, ["==", ["to-string", ["get", KEY]], String(selection.key)]);
    }
  }

  select(selection) {
    this.selected = selection;
    this.setOverlay("sel", selection);
    this.state.value.selected = selection; // not persisted; permalink reads it
    if (this.viewer.permalink) this.viewer.permalink.schedule();
  }

  setHover(selection) {
    const key = selection ? `${selection.layerId}\u0000${selection.key}` : null;
    if (key === this.hoverKey) return;
    this.hoverKey = key;
    this.setOverlay("hover", selection);
    this.map.getCanvas().style.cursor = selection ? "pointer" : "";
  }

  hover(event) {
    if (this.viewer.measuring) return;
    if (this.pendingHover) return;
    this.pendingHover = requestAnimationFrame(() => {
      this.pendingHover = null;
      const hits = this.hits(event.point, 2);
      this.setHover(hits.length ? { layerId: hits[0].layerId, key: hits[0].key } : null);
    });
  }

  destroy() {
    this.map.off("click", this.onClick);
    this.map.off("mousemove", this.onMove);
    this.map.getCanvas().removeEventListener("mouseleave", this.onLeave);
    this.popup.remove();
    for (const id of this.overlays) if (this.map.getLayer(id)) this.map.removeLayer(id);
    this.overlays.clear();
  }
}
