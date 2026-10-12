// Attribute filters with fixed schemas from the manifest (values with
// published counts, numeric ranges, text contains). The allowed values come
// from the whole published dataset, not from loaded tiles. Filters become
// MapLibre expressions in style_control.mjs; no free-form expressions.
import { t, formatNumber } from "./i18n.mjs";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export class Filters {
  constructor({ manifest, state, container }) {
    Object.assign(this, { manifest, state, container });
    this.render();
    this.unsubscribe = state.onChange((_, reason) => { if (reason === "reset" || reason === "url") this.render(); });
  }

  get layers() {
    return this.manifest.layers.filter((l) => (l.filterFields || []).length);
  }

  update(layerId, field, value) {
    const filters = JSON.parse(JSON.stringify(this.state.value.filters || {}));
    filters[layerId] = filters[layerId] || {};
    if (value) filters[layerId][field] = value;
    else delete filters[layerId][field];
    if (!Object.keys(filters[layerId]).length) delete filters[layerId];
    this.state.set({ filters }, "filters");
  }

  render() {
    this.container.replaceChildren();
    const current = this.state.value.filters || {};
    for (const layer of this.layers) {
      const block = el("section", "q2vt-card q2vt-filter");
      block.append(el("h3", "", layer.title));
      for (const config of layer.filterFields) {
        const value = (current[layer.id] || {})[config.field];
        const wrap = el("div", "q2vt-filter-field");
        const id = `q2vt-f-${layer.id}-${config.field}`.replace(/[^A-Za-z0-9_-]/g, "_");
        const label = el("label", "", config.title || config.field);
        label.htmlFor = id;
        wrap.append(label);
        if (config.kind === "values") {
          const options = [...(config.values || []).map(([option, count]) => [option, option, count])];
          if (config.nulls) options.push(["\u0000null", t("filter.nulls"), config.nulls]);
          const list = el("div", "q2vt-checklist");
          list.id = id;
          list.setAttribute("role", "group");
          list.setAttribute("aria-label", config.title || config.field);
          const boxes = [];
          const changed = () => {
            const chosen = boxes.filter((b) => b.checked).map((b) => b.value);
            const values = chosen.filter((v) => v !== "\u0000null");
            const nulls = chosen.includes("\u0000null");
            this.update(layer.id, config.field, values.length || nulls ? { values, nulls } : null);
          };
          for (const [optionValue, text, count] of options) {
            const row = el("label", "q2vt-check");
            const box = document.createElement("input");
            box.type = "checkbox";
            box.value = optionValue;
            box.checked = !!value && (optionValue === "\u0000null" ? !!value.nulls : (value.values || []).includes(optionValue));
            box.addEventListener("change", changed);
            boxes.push(box);
            row.append(box, el("span", "q2vt-check-text", text), el("span", "q2vt-count", formatNumber(count)));
            list.append(row);
          }
          if (options.length > 10) {
            const find = document.createElement("input");
            find.type = "search";
            find.className = "q2vt-input q2vt-input-small";
            find.placeholder = t("filter.contains");
            find.setAttribute("aria-label", `${config.title || config.field}: ${t("filter.contains")}`);
            find.addEventListener("input", () => {
              const needle = find.value.trim().toLowerCase();
              for (const row of list.children) row.hidden = !!needle && !row.textContent.toLowerCase().includes(needle);
            });
            wrap.append(find);
          }
          wrap.append(list);
        } else if (config.kind === "range") {
          const range = el("div", "q2vt-range");
          const inputs = ["min", "max"].map((side) => {
            const input = document.createElement("input");
            input.type = "number";
            input.className = "q2vt-input";
            input.placeholder = `${t(`filter.${side}`)} ${config[side] ?? ""}`;
            input.setAttribute("aria-label", `${config.title || config.field} ${t(`filter.${side}`)}`);
            if (value && value[side] !== null && value[side] !== undefined) input.value = String(value[side]);
            range.append(input);
            return input;
          });
          const apply = () => {
            const [min, max] = inputs.map((i) => (i.value === "" ? null : Number(i.value)));
            this.update(layer.id, config.field, min !== null || max !== null ? { min, max } : null);
          };
          inputs.forEach((i) => i.addEventListener("change", apply));
          inputs[0].id = id;
          wrap.append(range);
        } else {
          const input = document.createElement("input");
          input.type = "search";
          input.className = "q2vt-input";
          input.id = id;
          input.placeholder = t("filter.contains");
          if (value && value.text) input.value = value.text;
          input.addEventListener("change", () => this.update(layer.id, config.field, input.value.trim() ? { text: input.value.trim() } : null));
          wrap.append(input);
        }
        block.append(wrap);
      }
      this.container.append(block);
    }
    if (this.layers.length) {
      const clear = el("button", "q2vt-chip", t("filter.clear"));
      clear.type = "button";
      clear.addEventListener("click", () => { this.state.set({ filters: {} }, "reset"); });
      this.container.append(clear, el("p", "q2vt-muted", t("filter.loadedOnly")));
    }
  }

  destroy() { this.unsubscribe(); }
}
