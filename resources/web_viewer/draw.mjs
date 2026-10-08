// Drawing ("redlining"): points, lines, areas and text drawn by the visitor
// over the map, in a few colours. Nothing is stored on the site: the
// drawing travels in the shared link (#…&d=, compact polyline coordinates)
// and can be saved as GeoJSON or KML. Text is a DOM marker (no glyphs
// needed) and is never interpreted as HTML.
import { t } from "./i18n.mjs";
import { button as iconButton, el } from "./icons.mjs";

const SOURCE = "q2vt_draw";
export const COLORS = ["#e11d48", "#2563eb", "#16a34a", "#f59e0b", "#111827"];
export const KINDS = ["point", "line", "area", "text"];
const MAX_FEATURES = 200;
const MAX_POINTS = 2000;
const MAX_TEXT = 120;

// --- link encoding --------------------------------------------------------
// Google's polyline algorithm (precision 1e-6) for [lng, lat] lists.
export function encodeLine(points) {
  let out = "", lastLat = 0, lastLng = 0;
  const put = (value) => {
    let v = value < 0 ? ~(value << 1) : value << 1;
    while (v >= 0x20) { out += String.fromCharCode((0x20 | (v & 0x1f)) + 63); v >>>= 5; }
    out += String.fromCharCode(v + 63);
  };
  for (const [lng, lat] of points) {
    const la = Math.round(lat * 1e6), ln = Math.round(lng * 1e6);
    put(la - lastLat);
    put(ln - lastLng);
    lastLat = la;
    lastLng = ln;
  }
  return out;
}

export function decodeLine(text) {
  const points = [];
  let index = 0, lat = 0, lng = 0;
  const take = () => {
    let shift = 0, result = 0, byte;
    do {
      if (index >= text.length) throw new Error("truncated");
      byte = text.charCodeAt(index++) - 63;
      result |= (byte & 0x1f) << shift;
      shift += 5;
    } while (byte >= 0x20 && shift < 35);
    return result & 1 ? ~(result >> 1) : result >> 1;
  };
  while (index < text.length && points.length < MAX_POINTS) {
    lat += take();
    lng += take();
    points.push([lng / 1e6, lat / 1e6]);
  }
  return points;
}

function b64url(text) {
  return btoa(String.fromCharCode(...new TextEncoder().encode(text))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function unb64url(text) {
  const bin = atob(text.replace(/-/g, "+").replace(/_/g, "/"));
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
}

// [{kind, coords, text, color}] <-> link text. Decoding is strict: unknown
// kinds, colours and out-of-range coordinates are dropped, sizes capped.
export function encodeDrawing(features) {
  if (!features.length) return "";
  return b64url(JSON.stringify(features.map((f) => [KINDS.indexOf(f.kind), encodeLine(f.coords),
    COLORS.indexOf(f.color), f.kind === "text" ? f.text : 0])));
}

export function decodeDrawing(text) {
  if (typeof text !== "string" || !text || text.length > 20000) return [];
  let raw;
  try { raw = JSON.parse(unb64url(text)); } catch { return []; }
  if (!Array.isArray(raw)) return [];
  const out = [];
  let total = 0;
  for (const item of raw.slice(0, MAX_FEATURES)) {
    if (!Array.isArray(item) || typeof item[1] !== "string") continue;
    const kind = KINDS[item[0]];
    let coords;
    try { coords = decodeLine(item[1]); } catch { continue; }
    coords = coords.filter(([lng, lat]) => Math.abs(lng) <= 180 && Math.abs(lat) <= 85.06);
    const need = { point: 1, text: 1, line: 2, area: 3 }[kind];
    if (!need || coords.length < need) continue;
    if (kind === "point" || kind === "text") coords = coords.slice(0, 1);
    total += coords.length;
    if (total > MAX_POINTS) break;
    const color = COLORS[item[2]] || COLORS[0];
    const label = kind === "text" ? String(item[3] ?? "").slice(0, MAX_TEXT).trim() : "";
    if (kind === "text" && !label) continue;
    out.push({ kind, coords, color, text: label });
  }
  return out;
}

// --- exports ----------------------------------------------------------------
export function toGeoJSON(features) {
  return { type: "FeatureCollection", features: features.map((f) => ({
    type: "Feature", properties: { kind: f.kind, color: f.color, ...(f.text ? { text: f.text } : {}) },
    geometry: f.kind === "line" ? { type: "LineString", coordinates: f.coords }
      : f.kind === "area" ? { type: "Polygon", coordinates: [[...f.coords, f.coords[0]]] }
        : { type: "Point", coordinates: f.coords[0] },
  })) };
}

function xml(text) {
  return String(text).replace(/[<>&"']/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&apos;" }[c]));
}

// KML colours are aabbggrr.
function kmlColor(hex, alpha = "ff") {
  const [r, g, b] = [hex.slice(1, 3), hex.slice(3, 5), hex.slice(5, 7)];
  return `${alpha}${b}${g}${r}`;
}

export function toKML(features, name = "") {
  const coords = (list) => list.map(([lng, lat]) => `${lng.toFixed(7)},${lat.toFixed(7)}`).join(" ");
  const placemarks = features.map((f, index) => {
    const style = `<Style><LineStyle><color>${kmlColor(f.color)}</color><width>3</width></LineStyle>`
      + `<PolyStyle><color>${kmlColor(f.color, "40")}</color></PolyStyle>`
      + `<IconStyle><color>${kmlColor(f.color)}</color></IconStyle></Style>`;
    const geometry = f.kind === "line" ? `<LineString><coordinates>${coords(f.coords)}</coordinates></LineString>`
      : f.kind === "area" ? `<Polygon><outerBoundaryIs><LinearRing><coordinates>${coords([...f.coords, f.coords[0]])}`
        + "</coordinates></LinearRing></outerBoundaryIs></Polygon>"
        : `<Point><coordinates>${coords(f.coords)}</coordinates></Point>`;
    return `<Placemark><name>${xml(f.text || `${f.kind} ${index + 1}`)}</name>${style}${geometry}</Placemark>`;
  });
  return `<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document>`
    + `<name>${xml(name)}</name>${placemarks.join("")}</Document></kml>\n`;
}

function download(text, type, filename) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// --- the tool -----------------------------------------------------------------
export class Draw {
  constructor({ map, maplibregl, manifest, container, viewer, permalink, initial }) {
    Object.assign(this, { map, maplibregl, manifest, container, viewer, permalink });
    this.features = decodeDrawing(initial);
    this.mode = null;
    this.points = [];
    this.color = COLORS[0];
    this.markers = [];
    this.render();
    if (this.map.isStyleLoaded()) this.refresh(); else this.map.once("load", () => this.refresh());
    this.onClick = (e) => this.click(e);
    this.onDblClick = (e) => { if (this.mode === "line" || this.mode === "area") { e.preventDefault(); this.finish(); } };
    this.onKey = (e) => {
      if (!this.mode) return;
      if (e.key === "Escape") this.cancel();
      if (e.key === "Enter") this.finish();
    };
    this.map.on("click", this.onClick);
    this.map.on("dblclick", this.onDblClick);
    document.addEventListener("keydown", this.onKey);
  }

  render() {
    const block = el("section", "q2vt-card q2vt-draw");
    block.append(el("h3", "", t("draw.title")));
    const row = el("div", "q2vt-chips");
    this.modeButtons = {};
    for (const [kind, iconName] of [["point", "point"], ["line", "line"], ["area", "area"], ["text", "text"]]) {
      const node = iconButton("q2vt-chip", null, iconName, { text: t(`draw.${kind}`) });
      node.addEventListener("click", () => (this.mode === kind ? this.cancel() : this.start(kind)));
      this.modeButtons[kind] = node;
      row.append(node);
    }
    const colors = el("div", "q2vt-draw-colors");
    colors.setAttribute("role", "radiogroup");
    colors.setAttribute("aria-label", t("draw.color"));
    this.colorButtons = COLORS.map((color) => {
      const node = el("button", "q2vt-draw-color");
      node.type = "button";
      node.style.background = color;
      node.setAttribute("role", "radio");
      node.setAttribute("aria-label", `${t("draw.color")} ${color}`);
      node.addEventListener("click", () => this.setColor(color));
      colors.append(node);
      return [color, node];
    });
    this.textInput = document.createElement("input");
    this.textInput.type = "text";
    this.textInput.maxLength = MAX_TEXT;
    this.textInput.className = "q2vt-draw-text-input";
    this.textInput.placeholder = t("draw.textPlaceholder");
    this.textInput.hidden = true;
    this.hint = el("p", "q2vt-muted q2vt-draw-hint");
    this.hint.setAttribute("aria-live", "polite");
    const actions = el("div", "q2vt-chips");
    this.finishButton = iconButton("q2vt-chip", null, "chevron", { text: t("draw.finish") });
    this.finishButton.addEventListener("click", () => this.finish());
    this.finishButton.hidden = true;
    const undo = iconButton("q2vt-chip q2vt-chip-ghost", null, "reset", { text: t("draw.undo") });
    undo.addEventListener("click", () => this.undo());
    const clear = iconButton("q2vt-chip q2vt-chip-ghost", null, "trash", { text: t("draw.clear") });
    clear.addEventListener("click", () => { this.cancel(); this.features = []; this.changed(); });
    const geojson = iconButton("q2vt-chip q2vt-chip-ghost", null, "download", { text: "GeoJSON" });
    geojson.addEventListener("click", () => this.save("geojson"));
    const kml = iconButton("q2vt-chip q2vt-chip-ghost", null, "download", { text: "KML" });
    kml.addEventListener("click", () => this.save("kml"));
    actions.append(this.finishButton, undo, clear, geojson, kml);
    block.append(row, colors, this.textInput, this.hint, actions,
      el("p", "q2vt-muted", t(this.manifest.interaction?.permalinks === false ? "draw.noteLocal" : "draw.note")));
    this.container.append(block);
    this.setColor(this.color);
  }

  setColor(color) {
    this.color = color;
    for (const [value, node] of this.colorButtons) node.setAttribute("aria-checked", String(value === color));
    if (this.points.length) this.refresh();
  }

  ensureSource() {
    if (this.map.getSource(SOURCE)) return;
    this.map.addSource(SOURCE, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    const color = ["get", "color"];
    this.map.addLayer({ id: `${SOURCE}_fill`, type: "fill", source: SOURCE, filter: ["==", ["geometry-type"], "Polygon"],
      paint: { "fill-color": color, "fill-opacity": 0.18 } });
    this.map.addLayer({ id: `${SOURCE}_line`, type: "line", source: SOURCE, filter: ["!=", ["geometry-type"], "Point"],
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": color, "line-width": 3,
               "line-opacity": ["case", ["boolean", ["get", "draft"], false], 0.6, 1] } });
    this.map.addLayer({ id: `${SOURCE}_points`, type: "circle", source: SOURCE,
      filter: ["all", ["==", ["geometry-type"], "Point"], ["!=", ["get", "kind"], "text"]],
      paint: { "circle-radius": ["case", ["boolean", ["get", "draft"], false], 4, 7], "circle-color": color,
               "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 } });
  }

  start(kind) {
    this.viewer.streetViewControl?.stop();  // one map tool at a time
    if (this.viewer.tools?.mode) this.viewer.tools.stop(false);
    this.cancel();
    this.ensureSource();
    this.mode = kind;
    this.points = [];
    this.viewer.drawing = true;
    this.map.doubleClickZoom.disable();
    this.map.getCanvas().style.cursor = "crosshair";
    for (const [name, node] of Object.entries(this.modeButtons)) node.setAttribute("aria-pressed", String(name === kind));
    this.textInput.hidden = kind !== "text";
    if (kind === "text") this.textInput.focus();
    this.finishButton.hidden = !(kind === "line" || kind === "area");
    this.hint.textContent = t(`draw.hint.${kind}`);
  }

  cancel() {
    this.mode = null;
    this.points = [];
    this.viewer.drawing = false;
    this.map.doubleClickZoom.enable();
    this.map.getCanvas().style.cursor = "";
    for (const node of Object.values(this.modeButtons || {})) node.setAttribute("aria-pressed", "false");
    if (this.finishButton) this.finishButton.hidden = true;
    if (this.textInput) this.textInput.hidden = true;
    if (this.hint) this.hint.textContent = "";
    this.refresh();
  }

  click(e) {
    if (!this.mode) return;
    const point = [Number(e.lngLat.lng.toFixed(7)), Number(e.lngLat.lat.toFixed(7))];
    if (this.mode === "point") {
      this.add({ kind: "point", coords: [point], color: this.color, text: "" });
    } else if (this.mode === "text") {
      const text = this.textInput.value.trim().slice(0, MAX_TEXT);
      if (!text) { this.hint.textContent = t("draw.textNeeded"); this.textInput.focus(); return; }
      this.add({ kind: "text", coords: [point], color: this.color, text });
    } else {
      this.points.push(point);
      this.refresh();
    }
  }

  finish() {
    const need = this.mode === "area" ? 3 : 2;
    if ((this.mode === "line" || this.mode === "area") && this.points.length >= need) {
      const kind = this.mode;
      this.add({ kind, coords: this.points.slice(), color: this.color, text: "" }, false);
    }
    this.cancel();
  }

  add(feature, keepMode = true) {
    if (this.features.length >= MAX_FEATURES) { this.hint.textContent = t("draw.full"); return; }
    this.features.push(feature);
    if (keepMode) this.points = [];
    this.changed();
  }

  undo() {
    if (this.points.length) this.points.pop();
    else this.features.pop();
    this.changed();
  }

  changed() {
    this.refresh();
    if (this.permalink) this.permalink.schedule();
  }

  encode() { return encodeDrawing(this.features); }

  refresh() {
    if (!this.features.length && !this.points.length && !this.map.getSource(SOURCE)) {
      this.renderText();
      return;
    }
    this.ensureSource();
    const data = toGeoJSON(this.features.filter((f) => f.kind !== "text"));
    if (this.points.length) {
      for (const p of this.points) data.features.push({ type: "Feature", properties: { color: this.color, draft: true }, geometry: { type: "Point", coordinates: p } });
      if (this.points.length >= 2) {
        const ring = this.mode === "area" && this.points.length >= 3 ? [...this.points, this.points[0]] : this.points;
        data.features.push({ type: "Feature", properties: { color: this.color, draft: true }, geometry: { type: "LineString", coordinates: ring } });
      }
    }
    this.map.getSource(SOURCE).setData(data);
    this.renderText();
  }

  // Text as DOM markers: plain text (textContent), coloured with a halo.
  renderText() {
    for (const marker of this.markers) marker.remove();
    this.markers = this.features.filter((f) => f.kind === "text").map((f) => {
      const node = el("div", "q2vt-draw-label", f.text);
      node.style.color = f.color;
      return new this.maplibregl.Marker({ element: node }).setLngLat(f.coords[0]).addTo(this.map);
    });
  }

  save(format) {
    if (!this.features.length) { this.hint.textContent = t("draw.empty"); return; }
    const base = (this.manifest.title || "map").normalize("NFKD").replace(/[^\w-]+/g, "-").replace(/^-+|-+$/g, "") || "map";
    if (format === "kml") download(toKML(this.features, this.manifest.title || ""), "application/vnd.google-earth.kml+xml", `${base}-drawing.kml`);
    else download(JSON.stringify(toGeoJSON(this.features), null, 1), "application/geo+json", `${base}-drawing.geojson`);
  }

  destroy() {
    this.map.off("click", this.onClick);
    this.map.off("dblclick", this.onDblClick);
    document.removeEventListener("keydown", this.onKey);
    for (const marker of this.markers) marker.remove();
  }
}
