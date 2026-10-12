// Print a map extract on one page (A4 or A3, landscape or portrait): the
// map at its printed size (left, or on top in portrait) with a north arrow
// and the scale bar, and the sheet: "map extract", the title, who issued the
// map, its decree and legal date (manifest.info), the scale, the print date,
// and a legend of what is drawn in the printed area (or the parcel report). The map is resized to its print size and
// redrawn before the browser prints (a CSS-stretched map canvas printed
// distorted or clipped); a long legend continues on the next page between
// entries, never inside one. The map prints at a map scale (M 1:500): the
// chosen one, or the screen's rounded to a standard scale; the sheet says it.
// A parcel report prints zoomed on the parcel (focusView), down to 1:100.
import { el } from "./icons.mjs";
import { t, formatNumber } from "./i18n.mjs";

const SHEET = "q2vt-print-sheet";
const PAGE_STYLE = "q2vt-print-page";
// Paper layouts: the page, the map's printed size (mm) inside 8 mm margins
// (the sheet takes the rest: a column beside it, or rows under it).
export const PAPERS = {
  "a4-landscape": { page: "A4 landscape", map: [186, 190], portrait: false },
  "a4-portrait": { page: "A4 portrait", map: [194, 200], portrait: true },
  "a3-landscape": { page: "A3 landscape", map: [300, 277], portrait: false },
  "a3-portrait": { page: "A3 portrait", map: [281, 300], portrait: true },
};

function northArrow(bearing) {
  const ns = "http://www.w3.org/2000/svg";
  const box = document.createElement("div");
  box.className = "q2vt-print-north";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 34");
  svg.style.transform = `rotate(${-bearing}deg)`;
  const add = (tag, attrs, text) => {
    const node = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (text) node.textContent = text;
    svg.append(node);
  };
  add("path", { d: "M12 10 L18 32 L12 27 L6 32 Z", fill: "#000" });
  add("path", { d: "M12 10 L12 27 L6 32 Z", fill: "#fff", stroke: "#000", "stroke-width": "0.8" });
  add("text", { x: "12", y: "8", "text-anchor": "middle", "font-size": "8", "font-weight": "700",
                "font-family": "sans-serif" }, t("print.north"));
  box.append(svg);
  return box;
}
const EARTH = 40075016.686;      // m, equator (Web Mercator)
const PAPER_PX = 0.0254 / 96;    // m, one CSS pixel on paper at 100 %
export const PRINT_SCALES = [100, 200, 250, 500, 1000, 1500, 2000, 2500, 4000, 5000, 10000, 20000, 25000, 50000, 100000, 200000, 500000];

// Scale denominator of the map at a zoom (512 px tiles) and latitude, on paper.
export function scaleAt(zoom, lat) {
  return EARTH * Math.cos(lat * Math.PI / 180) / (512 * 2 ** zoom) / PAPER_PX;
}

export function zoomFor(scale, lat) {
  return Math.log2(EARTH * Math.cos(lat * Math.PI / 180) / (512 * scale * PAPER_PX));
}

// A parcel printed on its own: the margin (share of the map on each side)
// left around it, so the neighbouring parcels' edges show, no more.
const FOCUS_MARGIN = 0.12;

// The closest standard scale showing [west, south, east, north] (WGS 84)
// whole on a map of width x height CSS px, with FOCUS_MARGIN around it, and
// its centre; null for an empty box.
export function focusView(bounds, width, height) {
  const [w, s, e, n] = bounds || [];
  if (![w, s, e, n].every(Number.isFinite) || !(e > w) || !(n > s) || !(width > 0) || !(height > 0)) return null;
  const merc = (lat) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));
  const lat = (s + n) / 2;
  const dx = (e - w) / 360, dy = (merc(n) - merc(s)) / (2 * Math.PI);  // world fractions
  const room = 1 - 2 * FOCUS_MARGIN;
  const zoom = Math.log2(Math.min((width * room) / (dx * 512), (height * room) / (dy * 512)));
  const fit = scaleAt(zoom, lat);  // the largest scale with the whole parcel on paper
  const scale = PRINT_SCALES.find((candidate) => candidate >= fit) || PRINT_SCALES[PRINT_SCALES.length - 1];
  const centerLat = (Math.atan(Math.sinh((merc(s) + merc(n)) / 2)) * 180) / Math.PI;
  return { center: { lng: (w + e) / 2, lat: centerLat }, scale };
}

// The standard scale nearest to a scale (on a log scale).
export function standardScale(scale) {
  return PRINT_SCALES.reduce((best, s) => (Math.abs(Math.log(s / scale)) < Math.abs(Math.log(best / scale)) ? s : best));
}

function idle(map, timeout = 6000) {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, timeout);
    map.once("idle", () => { clearTimeout(timer); resolve(); });
    map.triggerRepaint();
  });
}

export class Printer {
  // content(mode) -> the sheet's body for "map" (legend) or "parcel" (report).
  constructor({ map, manifest, content, threeD = null }) {
    Object.assign(this, { map, manifest, content, viewer3d: threeD });
    this.active = false;
    this.scale = 0;  // 0: the screen's scale, rounded to a standard scale
    this.paper = "a4-landscape";
    // Ctrl+P (the browser's own print): the same layout, without waiting.
    this.onBefore = () => { if (!this.active) this.prepare("map"); };
    this.onAfter = () => this.restore();
    window.addEventListener("beforeprint", this.onBefore);
    window.addEventListener("afterprint", this.onAfter);
  }

  sheet() {
    let sheet = document.getElementById(SHEET);
    if (!sheet) {
      sheet = el("section");
      sheet.id = SHEET;
      document.getElementById("q2vt-app").append(sheet);
    }
    return sheet;
  }

  // ``focus`` ([west, south, east, north], a parcel): printed zoomed on it
  // (unless a print scale was chosen), else the screen's view.
  prepare(mode, focus = null) {
    this.active = true;
    this.view = { center: this.map.getCenter(), zoom: this.map.getZoom(), bearing: this.map.getBearing(),
                  pitch: this.map.getPitch() };
    const paper = PAPERS[this.paper] || PAPERS["a4-landscape"];
    const body = document.body;
    body.style.setProperty("--q2vt-pmap-w", `${paper.map[0]}mm`);
    body.style.setProperty("--q2vt-pmap-h", `${paper.map[1]}mm`);
    body.classList.toggle("q2vt-print-portrait", paper.portrait);
    let page = document.getElementById(PAGE_STYLE);
    if (!page) {
      page = document.createElement("style");
      page.id = PAGE_STYLE;
      document.head.append(page);
    }
    page.textContent = `@media print { @page { size: ${paper.page}; margin: 8mm; } }`;
    body.classList.add("q2vt-printing", `q2vt-printing-${mode}`);
    this.map.resize();
    const canvas = this.map.getContainer();
    const fitted = focus ? focusView(focus, canvas.clientWidth, canvas.clientHeight) : null;
    // A parcel is printed centred on it, also at a chosen scale.
    const center = fitted ? fitted.center : this.view.center;
    const lat = center.lat;
    const wanted = this.scale || (fitted ? fitted.scale : standardScale(scaleAt(this.view.zoom, lat)));
    // The centre (the parcel's, or the screen's), at the scale on paper (seen
    // from above: a scale holds on a flat map only).
    // Flat and from above (a scale holds there only): the 3D view is left
    // for the print and comes back after it.
    this.threeD = this.viewer3d && this.viewer3d.active ? this.viewer3d : null;
    if (this.threeD) this.threeD.set(false);
    this.map.jumpTo({ ...this.view, pitch: 0, center, zoom: zoomFor(wanted, lat) });
    if (this.map.redraw) this.map.redraw();  // drawn now: Ctrl+P snapshots the page at once
    this.map.getContainer().append(northArrow(this.view.bearing));
    const actual = scaleAt(this.map.getZoom(), lat);  // the map's zoom limits may not reach it
    this.printScale = Math.abs(actual / wanted - 1) < 0.005 ? wanted : Math.round(actual);
    this.fill(mode);
  }

  fill(mode) {
    const head = el("header", "q2vt-print-head");
    const info = this.manifest.info || {};
    head.append(el("p", "q2vt-print-kicker", t("print.extract")));
    head.append(el("h1", "", this.manifest.title || ""));
    const rows = [["info.issuer", info.issuer], ["info.decree", info.decree], ["info.legalDate", info.legalDate],
                  ["info.dataDate", info.dataDate]].filter(([, v]) => typeof v === "string" && v);
    if (rows.length) {
      const list = el("dl", "q2vt-print-info");
      for (const [key, value] of rows) list.append(el("dt", "", t(key)), el("dd", "", value));
      head.append(list);
    }
    if (this.printScale) head.append(el("p", "q2vt-print-scale", t("print.scale", { scale: formatNumber(this.printScale, 0) })));
    const date = new Date().toLocaleString(document.documentElement.lang || undefined);
    head.append(el("p", "", [`${t("print.date")}: ${date}`, this.manifest.attribution].filter(Boolean).join(" — ")));
    const body = this.content(mode) || el("div");
    body.classList.add("q2vt-print-body");
    this.sheet().replaceChildren(head, body);
  }

  async print(mode = "map", focus = null) {
    if (this.active) return;
    this.prepare(mode, focus);
    await idle(this.map);
    this.fill(mode);  // the legend of what the printed map draws
    window.print();
  }

  restore() {
    if (!this.active) return;
    this.active = false;
    document.body.classList.remove("q2vt-printing", "q2vt-printing-map", "q2vt-printing-parcel", "q2vt-print-portrait");
    const sheet = document.getElementById(SHEET);
    if (sheet) sheet.replaceChildren();
    for (const node of this.map.getContainer().querySelectorAll(".q2vt-print-north")) node.remove();
    this.map.resize();
    if (this.view) this.map.jumpTo(this.view);
    if (this.threeD) { this.threeD.set(true); this.threeD = null; }
  }

  destroy() {
    window.removeEventListener("beforeprint", this.onBefore);
    window.removeEventListener("afterprint", this.onAfter);
  }
}
