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
      const block = el("section", "q2vt-filter");
      block.append(el("h3", "", layer.title));
      for (const config of layer.filterFields) {
        const value = (current[layer.id] || {})[config.field];
        const wrap = el("div", "q2vt-filter");
        const id = `q2vt-f-${layer.id}-${config.field}`.replace(/[^A-Za-z0-9_-]/g, "_");
        const label = el("label", "", config.title || config.field);
        label.htmlFor = id;
        wrap.append(label);
        if (config.kind === "values") {
          const select = document.createElement("select");
          select.id = id;
          select.multiple = true;
          select.size = Math.min(6, (config.values || []).length + (config.nulls ? 1 : 0)) || 2;
          for (const [option, count] of config.values || []) {
            const opt = el("option", "", `${option} (${formatNumber(count)})`);
            opt.value = option;
            opt.selected = !!value && (value.values || []).includes(option);
            select.append(opt);
          }
          if (config.nulls) {
            const opt = el("option", "", `${t("filter.nulls")} (${formatNumber(config.nulls)})`);
            opt.value = "\u0000null";
            opt.selected = !!value && value.nulls;
            select.append(opt);
          }
          select.addEventListener("change", () => {
            const chosen = [...select.selectedOptions].map((o) => o.value);
            const values = chosen.filter((v) => v !== "\u0000null");
            const nulls = chosen.includes("\u0000null");
            this.update(layer.id, config.field, values.length || nulls ? { values, nulls } : null);
          });
          wrap.append(select);
        } else if (config.kind === "range") {
          const range = el("div", "q2vt-range");
          const inputs = ["min", "max"].map((side) => {
            const input = document.createElement("input");
            input.type = "number";
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
      const clear = el("button", "", t("filter.clear"));
      clear.type = "button";
      clear.addEventListener("click", () => { this.state.set({ filters: {} }, "reset"); });
      this.container.append(clear, el("p", "q2vt-muted", t("filter.loadedOnly")));
    }
  }

  destroy() { this.unsubscribe(); }
}
