// Planning tools: coordinate readout (WGS 84 and the project's CRS: EOV with
// its own transformation, any other projected CRS through proj4js), distance and
// area measurement on user-drawn temporary geometry, print of the current
// view. Measurement geometry is an in-memory GeoJSON source (allowed
// runtime helper); it is never saved or published.
import { t, formatNumber } from "./i18n.mjs";
import { lineLength, polygonArea, wgs84ToEov } from "./geo.mjs";
import { button as iconButton, el } from "./icons.mjs";
import { PRINT_SCALES } from "./print.mjs";
import { Snapper } from "./snap.mjs";

const SOURCE = "q2vt_measure";
const SNAP_SOURCE = "q2vt_measure_snap";
const SNAP_KEY = "q2vt:snap";
const SCALE_KEY = "q2vt:print-scale";

export function formatDistance(metres) {
  return metres >= 1000 ? `${formatNumber(metres / 1000, 3)} km` : `${formatNumber(metres, 1)} m`;
}

export function formatArea(m2) {
  if (m2 >= 1e6) return `${formatNumber(m2 / 1e6, 4)} km²`;
  if (m2 >= 1e4) return `${formatNumber(m2 / 1e4, 4)} ha`;
  return `${formatNumber(m2, 1)} m²`;
}

// Coordinates in the project CRS need proj4js unless it is WGS 84 (shown
// anyway) or EOV (own transformation).
export function needsProj4(crs) {
  return !!(crs && crs.proj && !["EPSG:4326", "EPSG:23700", "OGC:CRS84"].includes(crs.authid));
}

let proj4Loading = null;
function loadProj4() {
  if (window.proj4) return Promise.resolve(window.proj4);
  proj4Loading ??= new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "assets/vendor/proj4.js";
    script.onload = () => (window.proj4 ? resolve(window.proj4) : reject(new Error("proj4 missing")));
    script.onerror = reject;
    document.head.append(script);
  });
  return proj4Loading;
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
    block.append(el("div", "q2vt-field-label", t("tools.wgs84")), wgs);
    this.container.append(block);
    // The project's CRS (manifest.crs) next to WGS 84: EOV with its own
    // transformation, other projected CRSs through proj4js (loaded only then).
    let projected = null;  // {output, convert(lng, lat) -> [x, y] | null, digits, outside}
    const addProjected = (label, convert, digits, outside) => {
      const output = el("div", "q2vt-tool-output");
      block.append(el("div", "q2vt-field-label", label), output);
      projected = { output, convert, digits, outside };
    };
    const crs = this.manifest.crs;
    if (crs && crs.authid === "EPSG:23700") {
      addProjected(t("tools.eov"), wgs84ToEov, 0, t("tools.eovOutside"));
    } else if (needsProj4(crs)) {
      loadProj4().then((proj4) => {
        const transform = proj4("EPSG:4326", crs.proj);
        const convert = (lng, lat) => {
          const xy = transform.forward([lng, lat]);
          return xy && xy.every(Number.isFinite) ? xy : null;
        };
        convert(this.map.getCenter().lng, this.map.getCenter().lat);  // a bad definition throws here
        const approx = /\+(towgs84|nadgrids)=/.test(crs.proj) ? ` ${t("tools.approximate")}` : "";
        addProjected(t("tools.projected", { name: crs.name, authid: crs.authid }) + approx, convert,
                     crs.geographic ? 6 : 2, t("tools.projectedOutside"));
        show(this.map.getCenter());
      }).catch(() => { /* no readout in the project CRS */ });
    }
    const show = (lngLat) => {
      wgs.textContent = `${lngLat.lng.toFixed(6)}, ${lngLat.lat.toFixed(6)}`;
      if (!projected) return;
      const xy = projected.convert(lngLat.lng, lngLat.lat);
      projected.output.textContent = xy
        ? xy.map((v) => formatNumber(projected.digits ? Number(v.toFixed(projected.digits)) : Math.round(v),
                                     projected.digits)).join(projected.digits ? "; " : ", ")  // decimal commas
        : projected.outside;
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
    block.append(row, result);
    // Snapping to the publisher's snap layers (vertices first, then edges).
    this.snapper = new Snapper({ map: this.map, manifest: this.manifest });
    this.snapping = false;
    if (this.snapper.available) {
      const saved = (() => { try { return localStorage.getItem(SNAP_KEY); } catch { return null; } })();
      this.snapping = saved !== "0";
      const label = el("label", "q2vt-snap-choice");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = this.snapping;
      box.addEventListener("change", () => {
        this.snapping = box.checked;
        this.showSnap(null);
        try { localStorage.setItem(SNAP_KEY, box.checked ? "1" : "0"); } catch { /* storage unavailable */ }
      });
      label.append(box, el("span", "", t("tools.snap", { layers: this.snapper.titles.join(", ") })));
      block.append(label);
    }
    block.append(el("p", "q2vt-muted", t("tools.measureHelp")),
      ...(this.snapper.available ? [el("p", "q2vt-muted", t("tools.snapHelp"))] : []),
      el("p", "q2vt-muted", t("tools.measureMethod")));
    this.container.append(block);
    this.snapRadius = window.matchMedia && window.matchMedia("(pointer: coarse)").matches ? 22 : 12;
    this.onClick = (e) => {
      if (!this.mode) return;
      const snapped = this.snapAt(e);
      this.points.push(snapped ? snapped.lngLat : [e.lngLat.lng, e.lngLat.lat]);
      this.cursor = null;
      this.draw();
    };
    this.onMeasureMove = (e) => {
      if (!this.mode) return;
      cancelAnimationFrame(this.moveFrame);
      this.moveFrame = requestAnimationFrame(() => {
        const snapped = this.snapAt(e);
        this.showSnap(snapped);
        this.cursor = snapped ? snapped.lngLat : [e.lngLat.lng, e.lngLat.lat];
        if (this.points.length) this.draw();
      });
    };
    this.map.on("mousemove", this.onMeasureMove);
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

  // The snapped point under the pointer (Alt: no snapping for this click).
  snapAt(e) {
    if (!this.snapping || (e.originalEvent && e.originalEvent.altKey)) return null;
    return this.snapper.snap([e.point.x, e.point.y], this.snapRadius);
  }

  showSnap(snapped) {
    const source = this.map.getSource(SNAP_SOURCE);
    if (!source) return;
    source.setData({ type: "FeatureCollection", features: snapped ? [{ type: "Feature",
      properties: { kind: snapped.kind }, geometry: { type: "Point", coordinates: snapped.lngLat } }] : [] });
  }

  ensureSource() {
    if (this.map.getSource(SOURCE)) return;
    this.map.addSource(SNAP_SOURCE, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    this.map.addLayer({ id: SNAP_SOURCE, type: "circle", source: SNAP_SOURCE, paint: {
      "circle-radius": ["match", ["get", "kind"], "vertex", 7, 5], "circle-color": "rgba(0,0,0,0)",
      "circle-stroke-color": "#ff3b30", "circle-stroke-width": 2 } });
    this.map.addSource(SOURCE, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    this.map.addLayer({ id: `${SOURCE}_fill`, type: "fill", source: SOURCE, filter: ["==", ["geometry-type"], "Polygon"],
      paint: { "fill-color": "#ff3b30", "fill-opacity": 0.12 } });
    this.map.addLayer({ id: `${SOURCE}_line`, type: "line", source: SOURCE,
      paint: { "line-color": "#ff3b30", "line-width": 2, "line-dasharray": [2, 1],
               "line-opacity": ["case", ["boolean", ["get", "preview"], false], 0.5, 1] } });
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
    this.cursor = null;
    this.showSnap(null);
    if (this.map.getSource(SOURCE) && this.points.length) this.draw();
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
    if (this.mode && this.cursor && this.points.length) {  // the next segment, to the (snapped) pointer
      features.push({ type: "Feature", properties: { preview: true },
        geometry: { type: "LineString", coordinates: [this.points[this.points.length - 1], this.cursor] } });
    }
    this.map.getSource(SOURCE).setData({ type: "FeatureCollection", features });
    if (this.result) this.result.textContent = this.points.length >= 2 ? this.measurement() : "";
  }

  renderPrint() {
    const block = el("section", "q2vt-card");
    block.append(el("h3", "", t("tools.print")));
    const button = iconButton("q2vt-chip", null, "print", { text: t("tools.print") });
    button.addEventListener("click", () => this.print());
    const label = el("label", "q2vt-print-choice");
    const choice = document.createElement("select");
    choice.append(new Option(t("print.scaleScreen"), "0"));
    for (const scale of PRINT_SCALES) choice.append(new Option(`1:${formatNumber(scale, 0)}`, String(scale)));
    const saved = (() => { try { return localStorage.getItem(SCALE_KEY); } catch { return null; } })();
    if (saved && [...choice.options].some((o) => o.value === saved)) choice.value = saved;
    const apply = () => { if (this.viewer && this.viewer.printer) this.viewer.printer.scale = Number(choice.value) || 0; };
    choice.addEventListener("change", () => {
      apply();
      try { localStorage.setItem(SCALE_KEY, choice.value); } catch { /* storage unavailable */ }
    });
    this.applyScale = apply;
    apply();  // the saved choice also for Ctrl+P and the parcel print
    label.append(el("span", "", t("print.scaleLabel")), choice);
    block.append(label, button, el("p", "q2vt-muted", t("tools.printNote")));
    this.container.append(block);
  }

  print() {
    if (this.applyScale) this.applyScale();
    if (this.viewer && this.viewer.printer) this.viewer.printer.print("map");
    else window.print();
  }

  destroy() {
    if (this.onMove) this.map.off("mousemove", this.onMove);
    if (this.onMoveEnd) this.map.off("moveend", this.onMoveEnd);
    if (this.onClick) this.map.off("click", this.onClick);
    if (this.onMeasureMove) this.map.off("mousemove", this.onMeasureMove);
    if (this.onDblClick) this.map.off("dblclick", this.onDblClick);
    if (this.onKey) document.removeEventListener("keydown", this.onKey);
  }
}
