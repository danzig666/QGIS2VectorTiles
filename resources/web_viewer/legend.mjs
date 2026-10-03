// Zoom-aware legend of what is switched on: QGIS-rendered swatches of the
// original rules (UI images), layers in tree order. Used on screen and in
// print. Rules not drawn at the current zoom are listed as out of scale.
// Optionally (publisher default + a switch for the visitor) only the
// entries actually drawn in the current view are listed.
import { t } from "./i18n.mjs";
import { el, icon, toggle } from "./icons.mjs";

const QUERY_ROLES = new Set(["geometry", "decoration"]);

export class Legend {
  constructor({ map, manifest, state, control, container, releaseBase }) {
    Object.assign(this, { map, manifest, state, control, container, releaseBase });
    this.visibleOnly = manifest.interaction?.legendVisibleOnly === true;
    this.render = this.render.bind(this);
    // In "visible only" mode the map must draw the change first: re-list on idle.
    this.unsubscribe = state.onChange(() => {
      if (this.visibleOnly) { this.dirty = true; this.map.triggerRepaint(); } else this.render();
    });
    this.onZoom = () => { if (!this.visibleOnly) this.render(); };
    this.onIdle = () => { if (this.visibleOnly && this.dirty) { this.dirty = false; this.render(); } };
    this.onMove = () => { this.dirty = true; };
    map.on("zoomend", this.onZoom);
    map.on("moveend", this.onMove);
    map.on("idle", this.onIdle);
    this.head = el("div", "q2vt-pane-head");
    const row = el("div", "q2vt-row q2vt-row-labels");
    const { wrap, input } = toggle(t("legend.visibleOnly"), (checked) => { this.visibleOnly = checked; this.render(); });
    input.checked = this.visibleOnly;
    const name = el("span", "q2vt-name");
    name.append(icon("eye", 18), el("span", "q2vt-title", t("legend.visibleOnly")));
    row.append(wrap, name);
    this.head.append(row);
    this.body = el("div");
    container.append(this.head, this.body);
    this.render();
  }

  // {layers: Set, rules: Set} drawn in the current view (rendered features).
  drawn(state) {
    const layers = new Set();
    const rules = new Set();
    const styleIds = [];
    for (const [styleId, component] of this.control.owner) {
      if (!QUERY_ROLES.has(component.role) || !this.map.getLayer(styleId)) continue;
      if (this.map.getLayoutProperty(styleId, "visibility") === "none") continue;
      styleIds.push(styleId);
    }
    if (styleIds.length) {
      for (const feature of this.map.queryRenderedFeatures({ layers: styleIds, validate: false })) {
        const component = this.control.owner.get(feature.layer.id);
        if (!component) continue;
        layers.add(component.layerId);
        for (const rule of component.ruleIds || []) rules.add(rule);
      }
    }
    // Raster layers: drawn when switched on and inside their area and zooms.
    const bounds = this.map.getBounds();
    const zoom = this.map.getZoom();
    for (const source of this.manifest.sources || []) {
      if (source.role !== "raster" || !source.layerId || !this.control.layerEnabled(source.layerId, state)) continue;
      const [w, s, e, n] = source.bounds || [-180, -90, 180, 90];
      const overlaps = !(e < bounds.getWest() || w > bounds.getEast() || n < bounds.getSouth() || s > bounds.getNorth());
      const layer = this.control.layers.get(source.layerId);
      if (overlaps && zoom >= (layer?.minZoom ?? 0) && zoom < (layer?.maxZoom ?? 99)) layers.add(source.layerId);
    }
    return { layers, rules };
  }

  render() {
    const state = this.state.value;
    const zoom = this.map.getZoom();
    const drawn = this.visibleOnly ? this.drawn(state) : null;
    this.body.replaceChildren();
    let shown = 0;
    const layers = [...this.manifest.layers].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    for (const layer of layers) {
      if (layer.legend === false || !this.control.layerEnabled(layer.id, state)) continue;
      if (drawn && !drawn.layers.has(layer.id)) continue;
      let rules = this.manifest.rules.filter((r) => r.layerId === layer.id && this.control.ruleEnabled(r.id, state));
      if (drawn && rules.length) rules = rules.filter((r) => drawn.rules.has(r.id));
      const items = (rules.length ? rules : [{ title: layer.title, swatch: layer.swatch, componentIds: layer.componentIds }])
        .filter((rule) => rule.swatch || !rules.length);
      // One symbol: one row with the layer's name (no heading repeating it).
      const single = items.length === 1;
      const block = el("section", single ? "q2vt-legend-layer q2vt-legend-single" : "q2vt-legend-layer");
      if (!single) block.append(el("h3", "", layer.title));
      const list = el("ul", "q2vt-legend-items");
      for (const rule of items) {
        const item = el("li", "q2vt-legend-item");
        if (rule.swatch) {
          const img = document.createElement("img");
          img.alt = "";
          img.src = new URL(rule.swatch, this.releaseBase).href;
          item.append(img);
        } else if (layer.geometry === "raster") {
          item.append(icon("image", 22));
        } else {
          item.append(el("span", "q2vt-legend-blank"));  // labels only: aligned with the others
        }
        item.append(el("span", "", single ? layer.title : rule.title));
        if (!this.control.availableAt(rule.componentIds, zoom)) {
          item.classList.add("q2vt-muted");
          item.title = t("app.outOfScale");
        }
        list.append(item);
      }
      block.append(list);
      this.body.append(block);
      shown++;
    }
    if (!shown) this.body.append(el("p", "q2vt-empty", t(this.visibleOnly ? "legend.noneVisible" : "legend.empty")));
  }

  destroy() {
    this.unsubscribe();
    this.map.off("zoomend", this.onZoom);
    this.map.off("moveend", this.onMove);
    this.map.off("idle", this.onIdle);
  }
}
