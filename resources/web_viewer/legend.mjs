// Zoom-aware legend of what is switched on: QGIS-rendered swatches of the
// original rules (UI images), layers in tree order. Used on screen and in
// print. Rules not drawn at the current zoom are listed as out of scale.
import { t } from "./i18n.mjs";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export class Legend {
  constructor({ map, manifest, state, control, container, releaseBase }) {
    Object.assign(this, { map, manifest, state, control, container, releaseBase });
    this.render = this.render.bind(this);
    this.unsubscribe = state.onChange(this.render);
    map.on("zoomend", this.render);
    this.render();
  }

  render() {
    const state = this.state.value;
    const zoom = this.map.getZoom();
    this.container.replaceChildren();
    let shown = 0;
    const layers = [...this.manifest.layers].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    for (const layer of layers) {
      if (layer.legend === false || !this.control.layerEnabled(layer.id, state)) continue;
      const block = el("div", "q2vt-legend-layer");
      block.append(el("h3", "", layer.title));
      const rules = this.manifest.rules.filter((r) => r.layerId === layer.id && this.control.ruleEnabled(r.id, state));
      const items = rules.length ? rules : [{ title: layer.title, swatch: layer.swatch, componentIds: layer.componentIds }];
      for (const rule of items) {
        if (!rule.swatch && rules.length) continue;
        const item = el("div", "q2vt-legend-item");
        if (rule.swatch) {
          const img = document.createElement("img");
          img.alt = "";
          img.src = new URL(rule.swatch, this.releaseBase).href;
          item.append(img);
        }
        item.append(el("span", "", rule.title));
        if (!this.control.availableAt(rule.componentIds, zoom)) {
          item.classList.add("q2vt-muted");
          item.title = t("app.outOfScale");
        }
        block.append(item);
      }
      this.container.append(block);
      shown++;
    }
    if (!shown) this.container.append(el("p", "q2vt-muted", t("legend.empty")));
  }

  destroy() {
    this.unsubscribe();
    this.map.off("zoomend", this.render);
  }
}
