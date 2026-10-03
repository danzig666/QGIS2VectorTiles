// Print on one A4 landscape page: the map at its printed size on the left,
// the title, date and a legend of what is drawn in the printed area (or the
// parcel report) on the right. The map is resized to its print size and
// redrawn before the browser prints (a CSS-stretched map canvas printed
// distorted or clipped); a long legend continues on the next page between
// entries, never inside one.
import { el } from "./icons.mjs";

const SHEET = "q2vt-print-sheet";

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
    this.map.jumpTo(this.view);  // the same centre and scale on paper
    this.fill(mode);
  }

  fill(mode) {
    const head = el("header", "q2vt-print-head");
    head.append(el("h1", "", this.manifest.title || ""));
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
