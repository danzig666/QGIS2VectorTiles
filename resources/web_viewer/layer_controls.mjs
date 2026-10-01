// Layer tree: QGIS groups (tri-state), layers, legend rules with swatches,
// per-layer opacity and the labels switch. Checked state and "not shown at
// this zoom" are separate: a rule switched on stays checked when the zoom
// hides it and comes back when zooming in again. Helper datasets never
// appear here.
import { t } from "./i18n.mjs";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export class LayerControls {
  constructor({ map, manifest, state, control, container, assetsBase, releaseBase }) {
    Object.assign(this, { map, manifest, state, control, container, assetsBase, releaseBase });
    this.inputs = { layers: new Map(), groups: new Map(), rules: new Map() };
    this.nodes = { layers: new Map(), rules: new Map() };
    this.render();
    this.unsubscribe = state.onChange(() => this.sync());
    this.onZoom = () => this.syncScale();
    map.on("zoomend", this.onZoom);
    this.sync();
  }

  checkbox(section, id, label, swatch) {
    const row = el("div", "q2vt-row");
    const labelEl = el("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.addEventListener("change", () => this.state.setIn(section, id, input.checked));
    labelEl.append(input);
    if (swatch) {
      const img = document.createElement("img");
      img.className = "q2vt-swatch";
      img.alt = "";
      img.src = new URL(swatch, this.releaseBase).href;
      labelEl.append(img);
    }
    labelEl.append(el("span", "q2vt-name", label));
    row.append(labelEl);
    this.inputs[section].set(id, input);
    return row;
  }

  render() {
    const root = el("ul", "q2vt-tree");
    root.setAttribute("role", "tree");
    const labels = el("div", "q2vt-row");
    const labelToggle = el("label");
    this.labelsInput = document.createElement("input");
    this.labelsInput.type = "checkbox";
    this.labelsInput.addEventListener("change", () => this.state.set({ labels: this.labelsInput.checked }));
    labelToggle.append(this.labelsInput, el("span", "q2vt-name", t("app.labels")));
    labels.append(labelToggle);
    const reset = el("button", "", t("app.reset"));
    reset.type = "button";
    reset.addEventListener("click", () => this.state.reset());
    this.container.append(labels, root, reset);

    const groupNodes = new Map();
    const containerFor = (groupId) => (groupId && groupNodes.get(groupId)) || root;
    const groups = [...this.manifest.groups].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    for (const group of groups) {
      const item = el("li", "q2vt-node");
      item.setAttribute("role", "treeitem");
      const row = this.checkbox("groups", group.id, group.title);
      const toggle = el("button", "q2vt-toggle", "▾");
      toggle.type = "button";
      toggle.setAttribute("aria-expanded", "true");
      toggle.setAttribute("aria-label", `${t("layers.group")}: ${group.title}`);
      toggle.addEventListener("click", () => {
        const collapsed = item.classList.toggle("q2vt-collapsed");
        toggle.textContent = collapsed ? "▸" : "▾";
        toggle.setAttribute("aria-expanded", String(!collapsed));
      });
      row.prepend(toggle);
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
    for (const layer of layers) {
      const item = el("li", "q2vt-node");
      item.setAttribute("role", "treeitem");
      const rules = rulesByLayer.get(layer.id) || [];
      const swatch = rules.length === 1 ? rules[0].swatch : layer.swatch && rules.length === 0 ? layer.swatch : null;
      const row = this.checkbox("layers", layer.id, layer.title, swatch);
      item.append(row);
      this.nodes.layers.set(layer.id, item);
      if (this.manifest.interaction?.opacityControls !== false) {
        const slider = document.createElement("input");
        slider.type = "range";
        slider.min = "0";
        slider.max = "100";
        slider.className = "q2vt-opacity";
        slider.setAttribute("aria-label", `${t("app.opacity")}: ${layer.title}`);
        slider.addEventListener("input", () => this.state.setIn("opacity", layer.id, Number(slider.value) / 100));
        this.inputs.opacity = this.inputs.opacity || new Map();
        this.inputs.opacity.set(layer.id, slider);
        item.append(slider);
      }
      if (rules.length > 1) {
        const list = el("ul");
        const ruleNodes = new Map();
        for (const rule of rules) {
          const ruleItem = el("li", "q2vt-node");
          ruleItem.append(this.checkbox("rules", rule.id, rule.title, rule.swatch));
          const children = el("ul");
          ruleItem.append(children);
          ruleNodes.set(rule.id, children);
          (rule.parentId && ruleNodes.get(rule.parentId) ? ruleNodes.get(rule.parentId) : list).append(ruleItem);
          this.nodes.rules.set(rule.id, ruleItem);
        }
        item.append(list);
      }
      containerFor(layer.groupId).append(item);
    }
  }

  sync() {
    const state = this.state.value;
    this.labelsInput.checked = state.labels;
    for (const [id, input] of this.inputs.layers) input.checked = state.layers[id] !== false;
    for (const [id, input] of this.inputs.rules) input.checked = state.rules[id] !== false;
    for (const [id, input] of this.inputs.opacity || []) input.value = String(Math.round((state.opacity[id] ?? 1) * 100));
    // Groups: tri-state from their layers.
    for (const [id, input] of this.inputs.groups) {
      input.checked = state.groups[id] !== false;
      const members = this.manifest.layers.filter((l) => this.inGroup(l.groupId, id));
      const on = members.filter((l) => state.layers[l.id] !== false).length;
      input.indeterminate = input.checked && on > 0 && on < members.length;
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
    for (const [id, item] of this.nodes.layers) {
      const layer = this.control.layers.get(id);
      const available = this.control.availableAt(layer.componentIds, zoom);
      item.classList.toggle("q2vt-out-of-scale", !available);
      item.querySelector(".q2vt-name").dataset.scaleNote = available ? "" : t("app.outOfScale");
    }
    for (const [id, item] of this.nodes.rules) {
      const rule = this.control.rules.get(id);
      const available = this.control.availableAt(rule.componentIds, zoom);
      item.classList.toggle("q2vt-out-of-scale", !available);
      item.querySelector(".q2vt-name").dataset.scaleNote = available ? "" : t("app.outOfScale");
    }
  }

  destroy() {
    this.unsubscribe();
    this.map.off("zoomend", this.onZoom);
    this.container.replaceChildren();
  }
}
