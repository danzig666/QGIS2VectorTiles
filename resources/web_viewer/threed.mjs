// 3D view (optional: manifest.interaction.threeD): a map button that tilts
// the map, raises the polygons of layers with a height field (fill-extrusion
// copies of their fill style layers, shown only while their layer is) and
// turns on the terrain relief of the publication's DEM (manifest.terrain:
// terrain-RGB tiles, raster-dem). The terrain's optional hillshade (drawn
// above the basemap in 2D) and the elevations of profiles need no 3D view.
import { t } from "./i18n.mjs";
import { button } from "./icons.mjs";
import { resolveUrl, checkRelative } from "./transport.mjs";

export const TERRAIN = "q2vt_terrain";
const HILLSHADE = "q2vt_hillshade";
const SUFFIX = "__q2vt3d";
const PITCH = 55;

// A height in metres from a feature property: numbers or numeric strings.
export function heightExpression(field) {
  return ["max", 0, ["coalesce", ["to-number", ["get", field], 0], 0]];
}

// A fill drawn with a solid, visible colour (an extrusion ignores the
// colour's alpha and fill patterns: those would become opaque blocks).
function solidFill(paint) {
  if (paint["fill-pattern"] !== undefined) return false;
  if (paint["fill-opacity"] === 0) return false;
  const color = paint["fill-color"];
  if (typeof color !== "string") return true;  // an expression: per feature
  const text = color.replace(/\s+/g, "").toLowerCase();
  return text !== "transparent" && !/^(rgba|hsla)\(.*,0(\.0*)?\)$/.test(text) && !/^#[0-9a-f]{6}00$/.test(text);
}

// Fill-extrusion copies of the fill style layers of layers with heights: one
// per component and zoom range (a rule's symbol layers would raise the same
// block twice; its per-zoom copies are kept).
export function extrusionLayers(style, manifest) {
  const byId = new Map((style.layers || []).map((l) => [l.id, l]));
  const out = [];
  for (const layer of manifest.layers || []) {
    if (!layer.heightField || layer.geometry !== "polygon") continue;
    for (const component of manifest.components || []) {
      if (component.layerId !== layer.id) continue;
      const ranges = new Set();
      for (const id of component.styleLayerIds || []) {
        const fill = byId.get(id);
        if (!fill || fill.type !== "fill" || !solidFill(fill.paint || {})) continue;
        const range = `${fill.minzoom ?? ""}-${fill.maxzoom ?? ""}`;
        if (ranges.has(range)) continue;
        ranges.add(range);
        const paint = fill.paint || {};
        const color = paint["fill-color"] ?? "#cccccc";
        const extrusion = { id: `${id}${SUFFIX}`, type: "fill-extrusion", source: fill.source,
          "source-layer": fill["source-layer"], layout: { visibility: "none" },
          paint: { "fill-extrusion-color": color, "fill-extrusion-height": heightExpression(layer.heightField),
                   "fill-extrusion-base": 0, "fill-extrusion-opacity": 0.92 },
          metadata: { "q2vt:3d-of": id } };
        if (fill.filter) extrusion.filter = fill.filter;
        if (fill.minzoom !== undefined) extrusion.minzoom = fill.minzoom;
        if (fill.maxzoom !== undefined) extrusion.maxzoom = fill.maxzoom;
        out.push(extrusion);
      }
    }
  }
  return out;
}

export class ThreeD {
  constructor({ map, manifest, manifestUrl, basemap, maplibregl }) {
    Object.assign(this, { map, manifest, manifestUrl, basemap, maplibregl });
    this.terrain = manifest.terrain && manifest.terrain.kind === "pmtiles" ? manifest.terrain : null;
    this.enabled = manifest.interaction?.threeD === true;
    this.active = false;
    this.extrusions = [];
    if (this.terrain) this.addTerrain();
    if (this.enabled) this.addExtrusions();
    this.onStyle = () => this.syncVisibility();
  }

  // The 3D button: switched on, with terrain or a height field to show.
  get available() { return this.enabled && (!!this.terrain || this.extrusions.length > 0); }

  addTerrain() {
    const url = "pmtiles://" + resolveUrl(checkRelative(this.terrain.href, "terrain"), this.manifestUrl);
    const source = { type: "raster-dem", url, tileSize: this.terrain.tileSize || 256, encoding: "mapbox",
      minzoom: this.terrain.minTileZoom, maxzoom: this.terrain.maxTileZoom };
    if (Array.isArray(this.terrain.bounds)) source.bounds = this.terrain.bounds;
    if (this.enabled) this.map.addSource(TERRAIN, source);
    if (this.terrain.hillshade) {
      // Its own source (MapLibre's advice for terrain + hillshade), above the basemap.
      this.map.addSource(`${HILLSHADE}_src`, { ...source });
      this.map.addLayer({ id: HILLSHADE, type: "hillshade", source: `${HILLSHADE}_src`,
        paint: { "hillshade-exaggeration": 0.35, "hillshade-shadow-color": "#3d3d3d",
                 "hillshade-highlight-color": "#ffffff", "hillshade-accent-color": "#5a5a5a" } },
        this.basemap && this.basemap.beforeId ? this.basemap.beforeId() : undefined);
    }
  }

  addExtrusions() {
    for (const layer of extrusionLayers(this.map.getStyle(), this.manifest)) {
      const parent = layer.metadata["q2vt:3d-of"];
      // Right above its flat fill (the order of the QGIS layers is kept).
      const layers = this.map.getStyle().layers;
      const index = layers.findIndex((l) => l.id === parent);
      const next = index >= 0 && index + 1 < layers.length ? layers[index + 1].id : undefined;
      this.map.addLayer(layer, next);
      this.extrusions.push(layer);
    }
  }

  // Shown in 3D while the layer's own fill is shown (layer switches, filters).
  syncVisibility() {
    for (const layer of this.extrusions) {
      const parent = layer.metadata["q2vt:3d-of"];
      if (!this.map.getLayer(parent) || !this.map.getLayer(layer.id)) continue;
      const shown = this.active && this.map.getLayoutProperty(parent, "visibility") !== "none" ? "visible" : "none";
      if (this.map.getLayoutProperty(layer.id, "visibility") !== shown) this.map.setLayoutProperty(layer.id, "visibility", shown);
      const filter = this.map.getFilter(parent) || null;
      if (JSON.stringify(this.map.getFilter(layer.id) || null) !== JSON.stringify(filter)) this.map.setFilter(layer.id, filter);
    }
  }

  set(active) {
    this.active = active;
    const map = this.map;
    if (active) {
      map.setMaxPitch(75);
      map.dragRotate.enable();
      map.touchZoomRotate.enableRotation();
      map.touchPitch.enable();
      if (this.terrain) map.setTerrain({ source: TERRAIN, exaggeration: this.terrain.exaggeration || 1.5 });
      map.easeTo({ pitch: PITCH, duration: 600 });
      map.on("styledata", this.onStyle);
    } else {
      map.off("styledata", this.onStyle);
      if (this.terrain) map.setTerrain(null);
      // Listened to first: with reduced motion the move ends inside easeTo.
      map.once("moveend", () => {
        if (this.active) return;
        map.setMaxPitch(0);
        map.dragRotate.disable();
        map.touchZoomRotate.disableRotation();
        map.touchPitch.disable();
      });
      map.easeTo({ pitch: 0, bearing: 0, duration: 400 });
    }
    this.syncVisibility();
    if (this.buttonNode) {
      this.buttonNode.setAttribute("aria-pressed", String(active));
      this.buttonNode.classList.toggle("q2vt-active", active);
    }
  }

  // The map button (MapLibre IControl).
  onAdd() {
    const root = document.createElement("div");
    root.className = "maplibregl-ctrl maplibregl-ctrl-group q2vt-3d";
    this.buttonNode = button("q2vt-3d-btn", t("threed.title"), "cube");
    this.buttonNode.setAttribute("aria-pressed", "false");
    this.buttonNode.addEventListener("click", () => this.set(!this.active));
    root.append(this.buttonNode);
    return root;
  }

  onRemove() { this.map.off("styledata", this.onStyle); }

  // Elevation (m) at [lng, lat] from the terrain tiles at their best zoom;
  // null outside them. Tiles are fetched from the archive and decoded once.
  async elevations(points) {
    if (!this.terrain) return null;
    const viewer = window.q2vtViewer;
    const archive = viewer && viewer.archives && viewer.archives.__terrain && viewer.archives.__terrain.archive;
    if (!archive) return null;
    const z = this.terrain.maxTileZoom;
    const n = 2 ** z;
    this.tiles ??= new Map();
    if (this.tiles.size > 48) this.tiles.clear();  // ~256 KB of pixels per tile
    const decode = async (x, y) => {
      const key = `${z}/${x}/${y}`;
      if (!this.tiles.has(key)) {
        const loading = (async () => {
          const tile = await archive.getZxy(z, x, y);
          if (!tile || !tile.data) return null;
          const bitmap = await createImageBitmap(new Blob([tile.data], { type: "image/png" }));
          const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
          const context = canvas.getContext("2d", { willReadFrequently: true });
          context.drawImage(bitmap, 0, 0);
          return { size: bitmap.width, pixels: context.getImageData(0, 0, bitmap.width, bitmap.height).data };
        })();
        this.tiles.set(key, loading);
        loading.catch(() => this.tiles.delete(key));  // tried again next time
      }
      return this.tiles.get(key);
    };
    const out = [];
    for (const [lng, lat] of points) {
      const fx = ((lng + 180) / 360) * n;
      const sin = Math.sin((lat * Math.PI) / 180);
      const fy = (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * n;
      const tile = await decode(Math.floor(fx), Math.floor(fy));
      if (!tile) { out.push(null); continue; }
      const px = Math.min(tile.size - 1, Math.floor((fx % 1) * tile.size));
      const py = Math.min(tile.size - 1, Math.floor((fy % 1) * tile.size));
      const i = (py * tile.size + px) * 4;
      out.push(-10000 + (tile.pixels[i] * 65536 + tile.pixels[i + 1] * 256 + tile.pixels[i + 2]) * 0.1);
    }
    return out;
  }
}
