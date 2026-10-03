// Parcel report (telekinformáció): clicking a parcel shows what the export
// computed in QGIS for it — area, parts cut by the zoning and the regulation
// lines (numbered on the map), zones with their legend graphic, zone values,
// restrictions with their legend graphic, overlap and explanation, and the
// notice. Data: parcels/catalog.json + one shard per lookup; no geometry.
// Everything goes into the DOM as text.
import { t, formatNumber } from "./i18n.mjs";
import { featureShardKey } from "./search_core.mjs";
import { button, el, icon } from "./icons.mjs";

export function formatArea(m2) {
  if (m2 >= 10000) return `${formatNumber(m2 / 10000, 4)} ha`;
  return `${formatNumber(m2, m2 < 100 ? 1 : 0)} m²`;
}

function value(raw) {
  if (raw === null || raw === undefined || raw === "") return "—";
  return typeof raw === "number" ? formatNumber(raw) : String(raw);
}

function swatchImage(path, base) {
  const img = document.createElement("img");
  img.className = "q2vt-pr-swatch";
  img.alt = "";
  if (typeof path === "string" && /^legend\/[A-Za-z0-9._-]+\.png$/.test(path)) img.src = new URL(path, base).href;
  return img;
}

function shareBar(share) {
  const bar = el("span", "q2vt-pr-bar");
  const fill = el("span");
  fill.style.width = `${Math.max(2, Math.min(100, share))}%`;
  bar.append(fill);
  return bar;
}

export class ParcelReport {
  constructor({ map, manifest, manifestUrl, releaseBase, maplibregl, container, panel, permalink }) {
    Object.assign(this, { map, manifest, releaseBase, maplibregl, container, panel, permalink });
    this.info = manifest.parcelInfo || null;
    this.base = this.info ? new URL(this.info.manifest, manifestUrl) : null;
    this.catalogUrl = this.info ? new URL(this.info.catalog, manifestUrl) : null;
    this.shards = new Map();
    this.markers = [];
    if (this.container) this.empty();
  }

  get available() { return !!this.info; }

  get layerId() { return this.info && this.info.layerId; }

  // The shown report for the print sheet (without its buttons).
  printCard() {
    const card = this.container && this.container.querySelector("article.q2vt-pr");
    if (!card) return null;
    const copy = card.cloneNode(true);
    for (const node of copy.querySelectorAll(".q2vt-chips, button")) node.remove();
    return copy;
  }

  async load() {
    if (!this.ready) {
      this.ready = Promise.all([fetch(this.base), fetch(this.catalogUrl)]).then(async ([a, b]) => {
        if (!a.ok || !b.ok) throw new Error("parcel report: HTTP error");
        this.index = await a.json();
        this.catalog = await b.json();
        this.restrictions = new Map((this.catalog.restrictions || []).map((r) => [r.i, r]));
      });
    }
    return this.ready;
  }

  async get(key) {
    await this.load();
    const shardKey = featureShardKey(this.index.layerId, String(key), this.index.prefixLength);
    const shard = this.index.shards.find((s) => s.key === shardKey);
    if (!shard) return null;
    if (!this.shards.has(shard.path)) {
      this.shards.set(shard.path, fetch(new URL(shard.path, this.base)).then((r) => {
        if (!r.ok) throw new Error(`${shard.path}: HTTP ${r.status}`);
        return r.json();
      }));
    }
    return (await this.shards.get(shard.path)).find((r) => r.k === String(key)) || null;
  }

  empty() {
    this.clearMarkers();
    const box = el("div", "q2vt-empty");
    box.append(icon("pin", 28), el("p", "", t("parcel.hint")));
    this.container.replaceChildren(box);
  }

  clearMarkers() {
    for (const marker of this.markers) marker.remove();
    this.markers = [];
  }

  async show(key) {
    let record = null;
    try { record = await this.get(key); } catch { record = null; }
    this.clearMarkers();
    this.container.replaceChildren();
    if (this.panel) { this.panel.select("parcel"); this.panel.setOpen(true); }
    if (!record) {
      this.container.append(el("p", "q2vt-empty", t("parcel.notFound")));
      return null;
    }
    this.render(record);
    return record;
  }

  render(record) {
    const c = this.catalog;
    const card = el("article", "q2vt-pr");
    const head = el("header", "q2vt-pr-head");
    head.append(el("div", "q2vt-pr-kicker", c.title || t("parcel.title")),
      el("h2", "", `${t("parcel.hrsz")} ${record.k}`));
    const total = el("div", "q2vt-pr-total");
    total.append(el("span", "q2vt-pr-total-value", formatArea(record.a)),
      el("span", "q2vt-muted", record.a >= 10000 ? `${formatNumber(Math.round(record.a))} m²` : t("parcel.totalArea")));
    head.append(total);
    card.append(head);
    const facts = (c.fields || []).filter((f) => record.f && record.f[f.field] !== null && record.f[f.field] !== undefined && record.f[f.field] !== "");
    if (facts.length) {
      const list = el("dl", "q2vt-pr-facts");
      for (const f of facts) list.append(el("dt", "", f.title || f.field), el("dd", "", value(record.f[f.field])));
      card.append(list);
    }
    // Parts (cut by the zoning and the regulation / zone boundary lines).
    const parts = record.p || [];
    const section = el("section", "q2vt-pr-section");
    section.append(el("h3", "", parts.length > 1 ? t("parcel.parts", { n: parts.length }) : t("parcel.zone")));
    for (const part of parts) {
      const row = el("div", "q2vt-pr-part");
      const top = el("div", "q2vt-pr-row");
      if (parts.length > 1) top.append(el("span", "q2vt-pr-num", String(part.n)));
      top.append(swatchImage(part.sw, this.releaseBase));
      const name = el("div", "q2vt-pr-name");
      name.append(el("strong", "", value(part.c)));
      if (part.zl && part.zl !== part.c) name.append(el("span", "q2vt-muted", part.zl));
      top.append(name);
      const size = el("div", "q2vt-pr-size");
      size.append(el("span", "", formatArea(part.a)), el("span", "q2vt-muted", `${formatNumber(part.s, 1)}%`));
      top.append(size);
      row.append(top);
      if (parts.length > 1) row.append(shareBar(part.s));
      if ((part.b || []).length) {
        const chips = el("div", "q2vt-pr-chips");
        for (const title of part.b) {
          const chip = el("span", "q2vt-pr-chip");
          chip.append(icon("ruler", 14), el("span", "", t("parcel.cutBy", { line: title })));
          chips.append(chip);
        }
        row.append(chips);
      }
      const rows = [];
      for (const f of c.zoneFields || []) {
        const v = part.z ? part.z[f.field] : undefined;
        // Not the zone code again (it is the part's title).
        if (v !== null && v !== undefined && v !== "" && String(v) !== String(part.c)) rows.push([f.title || f.field, v]);
      }
      const regulation = (c.regulations || {})[part.c];
      if (regulation) {
        for (const f of c.regulationFields || []) {
          const v = regulation[f.field];
          if (v !== null && v !== undefined && v !== "") rows.push([f.title || f.field, v]);
        }
      }
      if (rows.length) {
        const list = el("dl", "q2vt-pr-facts q2vt-pr-zone");
        for (const [k, v] of rows) list.append(el("dt", "", k), el("dd", "", value(v)));
        row.append(list);
      }
      section.append(row);
    }
    card.append(section);
    // Restrictions.
    const hits = record.r || [];
    const restrictions = el("section", "q2vt-pr-section");
    restrictions.append(el("h3", "", t("parcel.restrictions", { n: hits.length })));
    if (!hits.length) restrictions.append(el("p", "q2vt-muted", t("parcel.noRestrictions")));
    for (const hit of hits) {
      const meta = this.restrictions.get(hit.i) || {};
      const item = el("div", "q2vt-pr-restriction");
      const top = el("div", "q2vt-pr-row");
      const swatches = el("span", "q2vt-pr-swatches");
      for (const [, path] of (hit.k || []).slice(0, 4)) swatches.append(swatchImage(path, this.releaseBase));
      top.append(swatches);
      const name = el("div", "q2vt-pr-name");
      name.append(el("strong", "", meta.title || ""));
      const labels = [...new Set((hit.k || []).map(([label]) => label).filter((l) => l && l !== meta.title))];
      if (labels.length) name.append(el("span", "q2vt-muted", labels.join(", ")));
      if ((hit.n || []).length) name.append(el("span", "q2vt-pr-names", hit.n.join(", ")));
      top.append(name);
      const size = el("div", "q2vt-pr-size");
      if (typeof hit.a === "number") size.append(el("span", "", formatArea(hit.a)), el("span", "q2vt-muted", `${formatNumber(hit.s, 1)}%`));
      else size.append(el("span", "q2vt-muted", t("parcel.touches")));
      top.append(size);
      item.append(top);
      if (typeof hit.s === "number") item.append(shareBar(hit.s));
      if (meta.distance) item.append(el("p", "q2vt-pr-note", t("parcel.distance", { m: formatNumber(meta.distance) })));
      if (meta.note) item.append(el("p", "q2vt-pr-note", meta.note));
      if (meta.reference) item.append(el("p", "q2vt-pr-ref", meta.reference));
      restrictions.append(item);
    }
    card.append(restrictions);
    const actions = el("div", "q2vt-chips");
    if (this.permalink) {
      const link = button("q2vt-chip", null, "link", { text: t("popup.link") });
      link.addEventListener("click", () => this.permalink.copy({ selected: { layerId: this.layerId, key: record.k } }));
      actions.append(link);
    }
    const print = button("q2vt-chip q2vt-chip-ghost", null, "print", { text: t("parcel.print") });
    print.addEventListener("click", () => (this.onPrint ? this.onPrint() : window.print()));
    actions.append(print);
    card.append(actions);
    card.append(el("p", "q2vt-pr-disclaimer", c.disclaimer || t("parcel.disclaimer")));
    this.container.append(card);
    this.container.scrollTop = 0;
    // Numbered markers of the parts on the map.
    if (parts.length > 1) {
      for (const part of parts) {
        if (!Array.isArray(part.x)) continue;
        const badge = el("div", "q2vt-pr-marker", String(part.n));
        badge.title = `${part.n}. ${value(part.c)} — ${formatArea(part.a)}`;
        this.markers.push(new this.maplibregl.Marker({ element: badge }).setLngLat(part.x).addTo(this.map));
      }
    }
  }
}
