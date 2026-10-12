// Layer tree: QGIS groups (tri-state), layers, legend rules with swatches,
// per-layer opacity and the labels switch. Checked state and "not shown at
// this zoom" are separate: a rule switched on stays checked when the zoom
// hides it and comes back when zooming in again. Layers and groups the
// publisher locked show a lock instead of a switch. Helper datasets never
// appear here.
import { t } from "./i18n.mjs";
import { button, el, icon, toggle } from "./icons.mjs";

export class LayerControls {
  constructor({ map, manifest, state, control, container, releaseBase }) {
    Object.assign(this, { map, manifest, state, control, container, releaseBase });
    this.inputs = { layers: new Map(), groups: new Map(), rules: new Map(), opacity: new Map() };
    this.nodes = { layers: new Map(), rules: new Map() };
    this.render();
    this.unsubscribe = state.onChange(() => this.sync());
    this.onZoom = () => this.syncScale();
    map.on("zoomend", this.onZoom);
    this.sync();
  }

  swatch(path, layer) {
    if (path) {
      const img = document.createElement("img");
      img.className = "q2vt-swatch";
      img.alt = "";
      img.loading = "lazy";
      img.src = new URL(path, this.releaseBase).href;
      return img;
    }
    if (layer && layer.geometry === "raster") {
      const box = el("span", "q2vt-swatch q2vt-swatch-icon");
      box.append(icon("image", 18));
      return box;
    }
    return null;
  }

  // A row: [expander] [switch | lock] [swatch] [title + scale note] [actions]
  row(section, id, title, { swatch, locked, kind, expander, actions } = {}) {
    const row = el("div", `q2vt-row q2vt-row-${kind || section}`);
    if (expander) row.append(expander);
    else row.append(el("span", "q2vt-expander-space"));
    if (locked) {
      const lock = el("span", "q2vt-lock");
      lock.append(icon("lock", 16));
      lock.title = t("layers.locked");
      lock.setAttribute("aria-label", t("layers.locked"));
      row.append(lock);
    } else {
      const { wrap, input } = toggle(title, (checked) => this.state.setIn(section, id, checked));
      this.inputs[section].set(id, input);
      row.append(wrap);
    }
    if (swatch) row.append(swatch);
    const name = el("span", "q2vt-name");
    name.append(el("span", "q2vt-title", title), el("span", "q2vt-scale-note"));
    row.append(name);
    if (actions) row.append(...actions);
    return row;
  }

  expander(item, label, expanded = true) {
    const toggleButton = button("q2vt-expander", `${label}`, "chevron");
    toggleButton.setAttribute("aria-expanded", String(expanded));
    if (!expanded) item.classList.add("q2vt-collapsed");
    toggleButton.addEventListener("click", () => {
      const collapsed = item.classList.toggle("q2vt-collapsed");
      toggleButton.setAttribute("aria-expanded", String(!collapsed));
    });
    return toggleButton;
  }

  render() {
    const top = el("div", "q2vt-pane-head");
    // Interaction -> Viewer -> Labels switch: without it, labels stay on.
    this.labelsAllowed = this.manifest.interaction?.labelsToggle !== false;
    if (this.labelsAllowed) {
      const labels = el("div", "q2vt-row q2vt-row-labels");
      const { wrap, input } = toggle(t("app.labels"), (checked) => this.state.set({ labels: checked }));
      this.labelsInput = input;
      const labelName = el("span", "q2vt-name");
      labelName.append(icon("tag", 18), el("span", "q2vt-title", t("app.labels")));
      labels.append(wrap, labelName);
      top.append(labels);
    }
    const reset = button("q2vt-chip q2vt-chip-ghost", t("app.reset"), "reset", { text: t("app.resetShort") });
    reset.addEventListener("click", () => this.state.reset());
    top.append(reset);
    const root = el("ul", "q2vt-tree");
    root.setAttribute("role", "tree");
    root.setAttribute("aria-label", t("app.layers"));
    this.container.append(top, root);

    const groupNodes = new Map();
    const groups = [...this.manifest.groups].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    for (const group of groups) {
      const item = el("li", "q2vt-node q2vt-group");
      item.setAttribute("role", "treeitem");
      const expander = this.expander(item, `${t("layers.group")}: ${group.title}`, group.expanded !== false);
      const folder = el("span", "q2vt-swatch q2vt-swatch-icon");
      folder.append(icon("folder", 18));
      const row = this.row("groups", group.id, group.title,
        { swatch: folder, locked: group.toggleable === false, kind: "group", expander });
      const children = el("ul");
      children.setAttribute("role", "group");
      item.append(row, children);
      (group.parentId ? (groupNodes.get(group.parentId) || root) : root).append(item);
      groupNodes.set(group.id, children);
    }
    const rulesByLayer = new Map();
    for (const rule of this.manifest.rules) {
      if (!rulesByLayer.has(rule.layerId)) rulesByLayer.set(rule.layerId, []);
      rulesByLayer.get(rule.layerId).push(rule);
    }
    const layers = [...this.manifest.layers].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    const opacityAllowed = this.manifest.interaction?.opacityControls !== false;
    for (const layer of layers) {
      const item = el("li", "q2vt-node q2vt-layer");
      item.setAttribute("role", "treeitem");
      const rules = rulesByLayer.get(layer.id) || [];
      const locked = layer.toggleable === false;
      const swatch = this.swatch(rules.length === 1 ? rules[0].swatch : rules.length === 0 ? layer.swatch : null, layer);
      const actions = [];
      let drawer = null;
      if (opacityAllowed) {
        drawer = el("div", "q2vt-drawer");
        drawer.hidden = true;
        const slider = document.createElement("input");
        slider.type = "range";
        slider.min = locked ? "10" : "0";
        slider.max = "100";
        slider.className = "q2vt-opacity";
        slider.setAttribute("aria-label", `${t("app.opacity")}: ${layer.title}`);
        const value = el("output", "q2vt-opacity-value");
        slider.addEventListener("input", () => {
          value.textContent = `${slider.value}%`;
          this.state.setIn("opacity", layer.id, Number(slider.value) / 100);
        });
        this.inputs.opacity.set(layer.id, slider);
        this.inputs.opacityValue = this.inputs.opacityValue || new Map();
        this.inputs.opacityValue.set(layer.id, value);
        drawer.append(el("span", "q2vt-drawer-label", t("app.opacity")), slider, value);
        const more = button("q2vt-icon-btn q2vt-more", `${t("app.opacity")}: ${layer.title}`, "sliders");
        more.setAttribute("aria-expanded", "false");
        more.addEventListener("click", () => {
          drawer.hidden = !drawer.hidden;
          more.setAttribute("aria-expanded", String(!drawer.hidden));
        });
        actions.push(more);
      }
      const expander = rules.length > 1 ? this.expander(item, layer.title, false) : null;
      const row = this.row("layers", layer.id, layer.title, { swatch, locked, kind: "layer", expander, actions });
      item.append(row);
      if (drawer) item.append(drawer);
      this.nodes.layers.set(layer.id, item);
      if (rules.length > 1) {
        const list = el("ul", "q2vt-rules");
        const ruleNodes = new Map();
        for (const rule of rules) {
          const ruleItem = el("li", "q2vt-node q2vt-rule");
          ruleItem.append(this.row("rules", rule.id, rule.title,
            { swatch: this.swatch(rule.swatch), locked: false, kind: "rule" }));
          const children = el("ul");
          ruleItem.append(children);
          ruleNodes.set(rule.id, children);
          (rule.parentId && ruleNodes.get(rule.parentId) ? ruleNodes.get(rule.parentId) : list).append(ruleItem);
          this.nodes.rules.set(rule.id, ruleItem);
        }
        item.append(list);
      }
      (layer.groupId && groupNodes.get(layer.groupId) ? groupNodes.get(layer.groupId) : root).append(item);
    }
  }

  sync() {
    const state = this.state.value;
    if (!this.labelsAllowed && state.labels === false) {
      this.state.set({ labels: true }); // a remembered or linked "off" has no switch to undo it
      return;
    }
    if (this.labelsInput) this.labelsInput.checked = state.labels;
    for (const [id, input] of this.inputs.layers) input.checked = state.layers[id] !== false;
    for (const [id, input] of this.inputs.rules) input.checked = state.rules[id] !== false;
    for (const [id, input] of this.inputs.opacity) {
      const percent = Math.round((state.opacity[id] ?? 1) * 100);
      input.value = String(percent);
      input.style.setProperty("--q2vt-fill", `${percent}%`);
      const out = this.inputs.opacityValue && this.inputs.opacityValue.get(id);
      if (out) out.textContent = `${percent}%`;
    }
    // Groups: tri-state from their layers.
    for (const [id, input] of this.inputs.groups) {
      input.checked = state.groups[id] !== false;
      const members = this.manifest.layers.filter((l) => this.inGroup(l.groupId, id));
      const on = members.filter((l) => state.layers[l.id] !== false).length;
      input.indeterminate = input.checked && on > 0 && on < members.length;
    }
    for (const [id, item] of this.nodes.layers) {
      item.classList.toggle("q2vt-off", !this.control.layerEnabled(id, state));
    }
    this.syncScale();
  }

  inGroup(groupId, target) {
    let id = groupId;
    while (id) {
      if (id === target) return true;
      id = this.control.groups.get(id)?.parentId || null;
    }
    return false;
  }

  syncScale() {
    const zoom = this.map.getZoom();
    const mark = (item, available) => {
      item.classList.toggle("q2vt-out-of-scale", !available);
      const note = item.querySelector(":scope > .q2vt-row .q2vt-scale-note");
      if (note) note.textContent = available ? "" : t("app.outOfScale");
    };
    for (const [id, item] of this.nodes.layers) {
      const layer = this.control.layers.get(id);
      mark(item, this.control.availableAt(layer.componentIds, zoom));
    }
    for (const [id, item] of this.nodes.rules) {
      const rule = this.control.rules.get(id);
      mark(item, this.control.availableAt(rule.componentIds, zoom));
    }
  }

  destroy() {
    this.unsubscribe();
    this.map.off("zoomend", this.onZoom);
    this.container.replaceChildren();
  }
}
