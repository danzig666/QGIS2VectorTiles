// Planning tools: coordinate readout (WGS 84; EOV approximate), distance and
// area measurement on user-drawn temporary geometry, print of the current
// view. Measurement geometry is an in-memory GeoJSON source (allowed
// runtime helper); it is never saved or published.
import { t, formatNumber } from "./i18n.mjs";
import { lineLength, polygonArea, wgs84ToEov } from "./geo.mjs";
import { button as iconButton, el } from "./icons.mjs";

const SOURCE = "q2vt_measure";

export function formatDistance(metres) {
  return metres >= 1000 ? `${formatNumber(metres / 1000, 3)} km` : `${formatNumber(metres, 1)} m`;
}

export function formatArea(m2) {
  if (m2 >= 1e6) return `${formatNumber(m2 / 1e6, 4)} km²`;
  if (m2 >= 1e4) return `${formatNumber(m2 / 1e4, 4)} ha`;
  return `${formatNumber(m2, 1)} m²`;
}

export class Tools {
  constructor({ map, manifest, container, viewer }) {
    Object.assign(this, { map, manifest, container, viewer });
    this.tools = manifest.tools || {};
    this.points = [];
    this.mode = null;
    this.render();
  }

  render() {
    if (this.tools.coordinates) this.renderCoordinates();
    if (this.tools.measure) this.renderMeasure();
    if (this.tools.print) this.renderPrint();
  }

  renderCoordinates() {
    const block = el("section", "q2vt-card");
    block.append(el("h3", "", t("tools.coordinates")));
    const wgs = el("div", "q2vt-tool-output");
    const eov = el("div", "q2vt-tool-output");
    block.append(el("div", "q2vt-field-label", t("tools.wgs84")), wgs, el("div", "q2vt-field-label", t("tools.eov")), eov);
    this.container.append(block);
    const show = (lngLat) => {
      wgs.textContent = `${lngLat.lng.toFixed(6)}, ${lngLat.lat.toFixed(6)}`;
      const projected = wgs84ToEov(lngLat.lng, lngLat.lat);
      eov.textContent = projected ? `${formatNumber(Math.round(projected[0]))}, ${formatNumber(Math.round(projected[1]))}`
        : t("tools.eovOutside");
    };
    this.onMove = (e) => show(e.lngLat);
    this.onMoveEnd = () => show(this.map.getCenter());
    this.map.on("mousemove", this.onMove);
    this.map.on("moveend", this.onMoveEnd);
    show(this.map.getCenter());
  }

  renderMeasure() {
    const block = el("section", "q2vt-card");
    block.append(el("h3", "", t("tools.measure")));
    const row = el("div", "q2vt-chips");
    const result = el("div", "q2vt-tool-output");
    result.setAttribute("aria-live", "polite");
    this.result = result;
    this.modeButtons = {};
    for (const [mode, iconName] of [["distance", "ruler"], ["area", "area"]]) {
      const button = iconButton("q2vt-chip", null, iconName, { text: t(`tools.${mode}`) });
      button.addEventListener("click", () => this.start(mode));
      this.modeButtons[mode] = button;
      row.append(button);
    }
    const clear = iconButton("q2vt-chip q2vt-chip-ghost", null, "close", { text: t("tools.clear") });
    clear.addEventListener("click", () => this.stop(true));
    row.append(clear);
    block.append(row, result, el("p", "q2vt-muted", t("tools.measureHelp")), el("p", "q2vt-muted", t("tools.measureMethod")));
    this.container.append(block);
    this.onClick = (e) => { if (this.mode) { this.points.push([e.lngLat.lng, e.lngLat.lat]); this.draw(); } };
    this.onDblClick = (e) => { if (this.mode) { e.preventDefault(); this.finish(); } };
    this.onKey = (e) => {
      if (!this.mode) return;
      if (e.key === "Escape") this.stop(true);
      if (e.key === "Enter") this.finish();
    };
    this.map.on("click", this.onClick);
    this.map.on("dblclick", this.onDblClick);
    document.addEventListener("keydown", this.onKey);
  }

  ensureSource() {
    if (this.map.getSource(SOURCE)) return;
    this.map.addSource(SOURCE, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    this.map.addLayer({ id: `${SOURCE}_fill`, type: "fill", source: SOURCE, filter: ["==", ["geometry-type"], "Polygon"],
      paint: { "fill-color": "#ff3b30", "fill-opacity": 0.12 } });
    this.map.addLayer({ id: `${SOURCE}_line`, type: "line", source: SOURCE,
      paint: { "line-color": "#ff3b30", "line-width": 2, "line-dasharray": [2, 1] } });
    this.map.addLayer({ id: `${SOURCE}_points`, type: "circle", source: SOURCE, filter: ["==", ["geometry-type"], "Point"],
      paint: { "circle-radius": 4, "circle-color": "#ff3b30" } });
  }

  start(mode) {
    this.ensureSource();
    this.mode = mode;
    this.points = [];
    this.viewer.measuring = true;
    this.map.doubleClickZoom.disable();
    this.map.getCanvas().style.cursor = "crosshair";
    for (const [name, node] of Object.entries(this.modeButtons || {})) node.setAttribute("aria-pressed", String(name === mode));
    this.draw();
  }

  finish() {
    this.mode = null;
    this.viewer.measuring = false;
    this.map.doubleClickZoom.enable();
    this.map.getCanvas().style.cursor = "";
    for (const node of Object.values(this.modeButtons || {})) node.setAttribute("aria-pressed", "false");
  }

  stop(clear) {
    this.finish();
    if (clear) {
      this.points = [];
      if (this.map.getSource(SOURCE)) this.map.getSource(SOURCE).setData({ type: "FeatureCollection", features: [] });
      if (this.result) this.result.textContent = "";
    }
  }

  measurement() {
    if (this.lastMode === "area" && this.points.length >= 3) return formatArea(polygonArea(this.points));
    return formatDistance(lineLength(this.points));
  }

  draw() {
    if (this.mode) this.lastMode = this.mode;
    const features = this.points.map((p) => ({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: p } }));
    if (this.points.length >= 2) {
      features.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates:
        this.lastMode === "area" ? [...this.points, this.points[0]] : this.points } });
    }
    if (this.lastMode === "area" && this.points.length >= 3) {
      features.push({ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [[...this.points, this.points[0]]] } });
    }
    this.map.getSource(SOURCE).setData({ type: "FeatureCollection", features });
    if (this.result) this.result.textContent = this.points.length >= 2 ? this.measurement() : "";
  }

  renderPrint() {
    const block = el("section", "q2vt-card");
    block.append(el("h3", "", t("tools.print")));
    const button = iconButton("q2vt-chip", null, "print", { text: t("tools.print") });
    button.addEventListener("click", () => this.print());
    block.append(button, el("p", "q2vt-muted", t("tools.printNote")));
    this.container.append(block);
  }

  print() {
    if (this.viewer && this.viewer.printer) this.viewer.printer.print("map");
    else window.print();
  }

  destroy() {
    if (this.onMove) this.map.off("mousemove", this.onMove);
    if (this.onMoveEnd) this.map.off("moveend", this.onMoveEnd);
    if (this.onClick) this.map.off("click", this.onClick);
    if (this.onDblClick) this.map.off("dblclick", this.onDblClick);
    if (this.onKey) document.removeEventListener("keydown", this.onKey);
  }
}
