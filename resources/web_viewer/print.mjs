// Print on one A4 landscape page: the map at its printed size on the left,
// the title, date and a legend of what is drawn in the printed area (or the
// parcel report) on the right. The map is resized to its print size and
// redrawn before the browser prints (a CSS-stretched map canvas printed
// distorted or clipped); a long legend continues on the next page between
// entries, never inside one. The map prints at a map scale (M 1:500): the
// chosen one, or the screen's rounded to a standard scale; the sheet says it.
import { el } from "./icons.mjs";
import { t, formatNumber } from "./i18n.mjs";

const SHEET = "q2vt-print-sheet";
const EARTH = 40075016.686;      // m, equator (Web Mercator)
const PAPER_PX = 0.0254 / 96;    // m, one CSS pixel on paper at 100 %
export const PRINT_SCALES = [250, 500, 1000, 1500, 2000, 2500, 4000, 5000, 10000, 20000, 25000, 50000, 100000, 200000, 500000];

// Scale denominator of the map at a zoom (512 px tiles) and latitude, on paper.
export function scaleAt(zoom, lat) {
  return EARTH * Math.cos(lat * Math.PI / 180) / (512 * 2 ** zoom) / PAPER_PX;
}

export function zoomFor(scale, lat) {
  return Math.log2(EARTH * Math.cos(lat * Math.PI / 180) / (512 * scale * PAPER_PX));
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
  constructor({ map, manifest, content }) {
    Object.assign(this, { map, manifest, content });
    this.active = false;
    this.scale = 0;  // 0: the screen's scale, rounded to a standard scale
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

  prepare(mode) {
    this.active = true;
    this.view = { center: this.map.getCenter(), zoom: this.map.getZoom(), bearing: this.map.getBearing() };
    document.body.classList.add("q2vt-printing", `q2vt-printing-${mode}`);
    this.map.resize();
    const lat = this.view.center.lat;
    const wanted = this.scale || standardScale(scaleAt(this.view.zoom, lat));
    this.map.jumpTo({ ...this.view, zoom: zoomFor(wanted, lat) });  // the same centre, at the scale on paper
    const actual = scaleAt(this.map.getZoom(), lat);  // the map's zoom limits may not reach it
    this.printScale = Math.abs(actual / wanted - 1) < 0.005 ? wanted : Math.round(actual);
    this.fill(mode);
  }

  fill(mode) {
    const head = el("header", "q2vt-print-head");
    head.append(el("h1", "", this.manifest.title || ""));
    if (this.printScale) head.append(el("p", "q2vt-print-scale", t("print.scale", { scale: formatNumber(this.printScale, 0) })));
    const date = new Date().toLocaleString(document.documentElement.lang || undefined);
    head.append(el("p", "", [date, this.manifest.attribution].filter(Boolean).join(" — ")));
    const body = this.content(mode) || el("div");
    body.classList.add("q2vt-print-body");
    this.sheet().replaceChildren(head, body);
  }

  async print(mode = "map") {
    if (this.active) return;
    this.prepare(mode);
    await idle(this.map);
    this.fill(mode);  // the legend of what the printed map draws
    window.print();
  }

  restore() {
    if (!this.active) return;
    this.active = false;
    document.body.classList.remove("q2vt-printing", "q2vt-printing-map", "q2vt-printing-parcel");
    const sheet = document.getElementById(SHEET);
    if (sheet) sheet.replaceChildren();
    this.map.resize();
    if (this.view) this.map.jumpTo(this.view);
  }

  destroy() {
    window.removeEventListener("beforeprint", this.onBefore);
    window.removeEventListener("afterprint", this.onAfter);
  }
}
