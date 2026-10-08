// Overview map: a small second map in the bottom-right corner, a few zoom
// levels further out, with the main map's view marked and the publication
// extent outlined. A click there moves the main map. It draws the main map's
// own style without labels (sources are shared through the pmtiles://
// protocol), refreshed after the main style changes (layers, basemap).
import { t } from "./i18n.mjs";
import { button, el } from "./icons.mjs";

const KEY = "q2vt:overview";
const VIEW = "q2vt_overview_view";
const EXTENT = "q2vt_overview_extent";
const ZOOM_OUT = 4;

// The main style for the overview: no labels, icons, 3D or terrain.
export function overviewStyle(style) {
  // Not the runtime overlays (measuring, drawing, highlights: GeoJSON sources).
  const keep = (layer) => !["symbol", "fill-extrusion", "hillshade", "heatmap"].includes(layer.type)
    && !(layer.source && style.sources[layer.source] && style.sources[layer.source].type === "geojson");
  const out = { version: 8, sources: {}, layers: (style.layers || []).filter(keep) };
  if (style.sprite) out.sprite = style.sprite;  // fill patterns
  for (const layer of out.layers) if (layer.source && style.sources[layer.source]) out.sources[layer.source] = style.sources[layer.source];
  return out;
}

// [[w, s], [e, n]] of the main map's view as a closed ring.
export function boundsRing(bounds) {
  const [[w, s], [e, n]] = bounds;
  return [[w, s], [e, s], [e, n], [w, n], [w, s]];
}

export class OverviewControl {
  constructor({ map, manifest, maplibregl }) {
    Object.assign(this, { map, manifest, maplibregl });
    this.timer = null;
  }

  onAdd() {
    const root = el("div", "maplibregl-ctrl q2vt-overview");
    const box = el("div", "q2vt-overview-map");
    box.setAttribute("aria-label", t("overview.title"));
    box.setAttribute("role", "img");
    const toggle = button("q2vt-icon-btn q2vt-overview-toggle", t("overview.title"), "inset");
    root.append(box, toggle);
    this.root = root;
    this.box = box;
    const saved = (() => { try { return localStorage.getItem(KEY); } catch { return null; } })();
    const small = window.matchMedia && window.matchMedia("(max-width: 760px)").matches;
    this.setOpen(saved ? saved === "1" : !small && !document.body.classList.contains("q2vt-embed"));
    toggle.addEventListener("click", () => {
      this.setOpen(box.hidden);
      try { localStorage.setItem(KEY, box.hidden ? "0" : "1"); } catch { /* storage unavailable */ }
    });
    return root;
  }

  setOpen(open) {
    this.box.hidden = !open;
    this.root.classList.toggle("q2vt-overview-open", open);
    if (open && !this.mini) this.create();
    if (open && this.mini) { this.mini.resize(); this.sync(); }
  }

  create() {
    const { map, maplibregl } = this;
    this.mini = new maplibregl.Map({
      container: this.box, style: overviewStyle(map.getStyle()), interactive: false,
      attributionControl: false, renderWorldCopies: false, fadeDuration: 0,
      center: map.getCenter(), zoom: Math.max(0, map.getZoom() - ZOOM_OUT),
    });
    this.mini.on("load", () => {
      const bounds = this.manifest.view && this.manifest.view.bounds;
      if (Array.isArray(bounds) && bounds.length === 4) {
        this.mini.addSource(EXTENT, { type: "geojson", data: { type: "Feature", properties: {},
          geometry: { type: "Polygon", coordinates: [boundsRing([[bounds[0], bounds[1]], [bounds[2], bounds[3]]])] } } });
        this.mini.addLayer({ id: EXTENT, type: "line", source: EXTENT,
          paint: { "line-color": "#64748b", "line-width": 1, "line-dasharray": [2, 2] } });
      }
      this.mini.addSource(VIEW, { type: "geojson", data: this.viewFeature() });
      const accent = getComputedStyle(document.documentElement).getPropertyValue("--q2vt-accent").trim() || "#2563eb";
      this.mini.addLayer({ id: `${VIEW}-fill`, type: "fill", source: VIEW, paint: { "fill-color": accent, "fill-opacity": 0.15 } });
      this.mini.addLayer({ id: VIEW, type: "line", source: VIEW, paint: { "line-color": accent, "line-width": 2 } });
      this.sync();
    });
    // A click on the overview centres the main map there.
    this.box.addEventListener("click", (event) => {
      const rect = this.box.getBoundingClientRect();
      const lngLat = this.mini.unproject([event.clientX - rect.left, event.clientY - rect.top]);
      map.easeTo({ center: lngLat });
    });
    this.onMove = () => this.sync();
    map.on("move", this.onMove);
    // Layers switched on/off, another basemap: the overview follows (later).
    this.onStyle = () => {
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.restyle(), 600);
    };
    map.on("styledata", this.onStyle);
  }

  viewFeature() {
    const b = this.map.getBounds();
    return { type: "Feature", properties: {},
      geometry: { type: "Polygon", coordinates: [boundsRing([[b.getWest(), b.getSouth()], [b.getEast(), b.getNorth()]])] } };
  }

  sync() {
    if (!this.mini || this.box.hidden) return;
    this.mini.jumpTo({ center: this.map.getCenter(), zoom: Math.max(0, this.map.getZoom() - ZOOM_OUT) });
    const source = this.mini.getSource(VIEW);
    if (source) source.setData(this.viewFeature());
  }

  restyle() {
    if (!this.mini || !this.mini.isStyleLoaded()) return;
    const style = overviewStyle(this.map.getStyle());
    for (const id of [EXTENT, VIEW]) if (this.mini.getSource(id)) style.sources[id] = this.mini.getStyle().sources[id];
    style.layers.push(...this.mini.getStyle().layers.filter((l) => [EXTENT, `${VIEW}-fill`, VIEW].includes(l.id)));
    this.mini.setStyle(style, { diff: true });
  }

  onRemove() {
    this.map.off("move", this.onMove);
    this.map.off("styledata", this.onStyle);
    if (this.mini) this.mini.remove();
    this.root.remove();
  }
}
