// Vector basemap: an OpenStreetMap extract bundled with the release
// (data/basemap.pmtiles) drawn with one of the published flavors
// (basemaps/<flavor>.json). The chosen flavor is added under every project
// layer; the project's own background is hidden while a basemap is shown.
// Only vector layers of the basemap's own source are accepted.
// Web basemaps (XYZ tiles, manifest basemap.xyz) are raster tiles loaded by
// the browser from their own servers (allowed by the page's policy).
import { ViewerError, checkRelative, registerPmtiles, resolveUrl } from "./transport.mjs";

export const SOURCE_ID = "q2vt_basemap";
const LAYER_PREFIX = "q2vt-bm-";
const ALLOWED_TYPES = new Set(["background", "fill", "line", "symbol", "circle", "fill-extrusion"]);

export class Basemap {
  constructor({ map, manifest, manifestUrl, maplibregl }) {
    Object.assign(this, { map, manifest, manifestUrl, maplibregl });
    this.config = manifest.basemap || null;
    this.flavors = new Map((this.config?.flavors || []).map((f) => [f.id, f]));
    this.xyz = new Map((this.config?.xyz || []).filter((x) => Array.isArray(x.tiles) && x.tiles.length
      && x.tiles.every((u) => typeof u === "string" && u.startsWith("https://"))).map((x) => [x.id, x]));
    this.cache = new Map();
    this.active = "none";
    this.layerIds = [];
    this.listeners = new Set();
  }

  get available() { return !!this.config && (this.flavors.size > 0 || this.xyz.size > 0); }

  // Every choice (vector flavors, then web basemaps) and its attribution.
  options() { return [...this.flavors.values(), ...this.xyz.values()]; }

  option(id) { return this.flavors.get(id) || this.xyz.get(id) || null; }

  attribution(id) {
    if (this.xyz.has(id)) return this.xyz.get(id).attribution || "";
    return this.flavors.has(id) ? (this.config.attribution || "") : "";
  }

  onChange(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }

  async init() {
    if (!this.available || !this.config.source) return;
    const source = this.config.source;
    if (source.tileType !== "mvt" || source.kind !== "pmtiles") {
      throw new ViewerError("Q2VT_PUB_NOT_MVT", "The basemap is not a vector tile archive.");
    }
    const url = resolveUrl(checkRelative(source.href, "basemap"), this.manifestUrl);
    const proto = registerPmtiles(this.maplibregl);
    const archive = new globalThis.pmtiles.PMTiles(url);
    const header = await archive.getHeader();
    if (header.tileType !== 1) throw new ViewerError("Q2VT_PUB_NOT_MVT", "The basemap archive does not hold vector tiles.");
    proto.add(archive);
    this.map.addSource(SOURCE_ID, {
      type: "vector", url: `pmtiles://${url}`, minzoom: source.minTileZoom, maxzoom: source.maxTileZoom,
      attribution: this.config.attribution || "",
    });
  }

  async layers(id) {
    if (this.cache.has(id)) return this.cache.get(id);
    const flavor = this.flavors.get(id);
    if (!flavor) throw new ViewerError("Q2VT_PUB_MANIFEST", `Unknown basemap ${id}`);
    const response = await fetch(resolveUrl(checkRelative(flavor.style, "basemap style"), this.manifestUrl));
    if (!response.ok) throw new ViewerError("Q2VT_PUB_FETCH", `Basemap style ${flavor.style}: HTTP ${response.status}`);
    const doc = await response.json();
    const layers = (doc.layers || []).filter((layer) => typeof layer.id === "string" && layer.id.startsWith(LAYER_PREFIX)
      && ALLOWED_TYPES.has(layer.type) && (layer.type === "background" || layer.source === SOURCE_ID));
    this.cache.set(id, layers);
    return layers;
  }

  // The first project layer that is not the project's own background.
  beforeId() {
    const own = new Set(this.layerIds);
    for (const layer of this.map.getStyle().layers) {
      if (own.has(layer.id) || layer.id.startsWith(LAYER_PREFIX)) continue;
      if (layer.type === "background") continue;
      return layer.id;
    }
    return undefined;
  }

  // A web basemap: one raster layer of its own source.
  xyzLayers(id) {
    const entry = this.xyz.get(id);
    const source = `q2vt_xyz_${id.replace(/[^a-z0-9-]/gi, "")}`;
    if (!this.map.getSource(source)) {
      this.map.addSource(source, { type: "raster", tiles: entry.tiles, scheme: entry.scheme === "tms" ? "tms" : "xyz",
        tileSize: entry.tileSize || 256, minzoom: entry.minzoom ?? 0, maxzoom: entry.maxzoom ?? 19,
        attribution: entry.attribution || "" });
    }
    return [{ id: `${LAYER_PREFIX}${id}`, type: "raster", source }];
  }

  async set(id) {
    if (!this.available) return;
    if (id !== "none" && !this.flavors.has(id) && !this.xyz.has(id)) id = "none";
    const layers = id === "none" ? [] : this.xyz.has(id) ? this.xyzLayers(id) : await this.layers(id);
    for (const layerId of this.layerIds) if (this.map.getLayer(layerId)) this.map.removeLayer(layerId);
    this.layerIds = [];
    const before = this.beforeId();
    for (const layer of layers) {
      this.map.addLayer(layer, before);
      this.layerIds.push(layer.id);
    }
    for (const layer of this.map.getStyle().layers) {
      if (layer.type === "background" && !layer.id.startsWith(LAYER_PREFIX)) {
        this.map.setLayoutProperty(layer.id, "visibility", id === "none" ? "visible" : "none");
      }
    }
    this.active = id;
    for (const listener of this.listeners) listener(id);
  }
}
