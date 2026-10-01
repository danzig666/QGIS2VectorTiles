// QGIS2VectorTiles web map viewer (release entry point).
//
// Loads manifest.json (relative to this page), validates it (schema 1,
// vector-only), binds the vector tile transport (PMTiles over HTTP ranges or
// XYZ MVT), loads the exported MapLibre style unchanged except for transport
// URLs, creates the map and the existing visible-polygon label helper, then
// mounts the interactive controls described by the manifest.
import * as maplibregl from "./maplibre-gl.mjs";
import { enableVisibleLabels } from "./visible_labels.mjs";
import { RASTER_TILE_TYPES, ViewerError, bindStyle, checkRelative, openArchives, resolveUrl } from "./transport.mjs";
import { loadLocale, t } from "./i18n.mjs";
import { fetchChecked, showError, state as diagnostics, warn } from "./diagnostics.mjs";

export const EXPECTED_MAPLIBRE = "6.11.2";
const RELEASE_ID = /^r-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$/;

function validateManifest(manifest) {
  if (!manifest || typeof manifest !== "object") throw new ViewerError("Q2VT_PUB_MANIFEST", "manifest is not an object");
  if (manifest.schemaVersion !== 1) {
    throw new ViewerError(manifest.schemaVersion > 1 ? "Q2VT_PUB_SCHEMA_UNSUPPORTED" : "Q2VT_PUB_MANIFEST",
      `manifest schemaVersion ${manifest.schemaVersion}`);
  }
  if (manifest.vectorOnly !== true) throw new ViewerError("Q2VT_PUB_RASTER_SOURCE", "manifest is not vector-only");
  if (!RELEASE_ID.test(manifest.releaseId || "")) throw new ViewerError("Q2VT_PUB_MANIFEST", "bad releaseId");
  if (!Array.isArray(manifest.sources) || !manifest.sources.length) throw new ViewerError("Q2VT_PUB_MANIFEST", "no sources");
  for (const [index, source] of manifest.sources.entries()) {
    // The map data is MVT; QGIS raster layers have their own image archives.
    const raster = source.role === "raster";
    if (index === 0 && raster) throw new ViewerError("Q2VT_PUB_NOT_MVT", "the first source must be the vector map");
    if (raster ? !RASTER_TILE_TYPES.includes(source.tileType) || source.kind !== "pmtiles"
        || !String(source.id).startsWith("q2vt_raster_") : source.tileType !== "mvt") {
      throw new ViewerError("Q2VT_PUB_NOT_MVT", `source ${source.id} tileType ${source.tileType}`);
    }
    if (!["pmtiles", "xyz"].includes(source.kind)) throw new ViewerError("Q2VT_PUB_MANIFEST", `source kind ${source.kind}`);
    checkRelative(source.href, "source");
  }
  checkRelative(manifest.style, "style");
  for (const key of ["groups", "layers", "rules", "components"]) {
    if (!Array.isArray(manifest[key])) throw new ViewerError("Q2VT_PUB_MANIFEST", `${key} missing`);
  }
  const view = manifest.view || {};
  if (!Array.isArray(view.center) || view.center.length !== 2 || typeof view.zoom !== "number") {
    throw new ViewerError("Q2VT_PUB_MANIFEST", "bad view");
  }
  return manifest;
}

function webglAvailable() {
  try {
    const canvas = document.createElement("canvas");
    return !!(canvas.getContext("webgl2") || canvas.getContext("webgl"));
  } catch {
    return false;
  }
}

function setText(id, text) {
  const node = document.getElementById(id);
  if (node) node.textContent = text;
}

async function start() {
  const pageUrl = new URL(".", location.href);
  const assetsUrl = new URL("./", import.meta.url);  // locales, legend code, workers
  const manifestUrl = new URL("manifest.json", pageUrl).href;
  const viewer = (window.q2vtViewer = { ready: false, diagnostics, maplibreVersion: maplibregl.getVersion() });
  let manifest;
  try {
    manifest = validateManifest(await fetchChecked(manifestUrl));
  } catch (error) {
    await loadLocale(navigator.language.slice(0, 2), assetsUrl).catch(() => {});
    if (!error.code || error.code === "Q2VT_PUB_FETCH") error.code = "Q2VT_PUB_MANIFEST";
    showError(error);
    return;
  }
  await loadLocale(manifest.locale || "en", assetsUrl).catch(() => {});
  document.title = manifest.title || document.title;
  setText("q2vt-title", manifest.title || "");
  setText("q2vt-description", manifest.description || "");
  setText("q2vt-release", `${t("app.release")}: ${manifest.releaseId}`);
  setText("q2vt-loading", t("app.loading"));
  viewer.manifest = manifest;
  if (maplibregl.getVersion() !== EXPECTED_MAPLIBRE) {
    warn("error.labels", `MapLibre ${maplibregl.getVersion()} is not the tested ${EXPECTED_MAPLIBRE}`);
  }
  try {
    if (!webglAvailable()) throw new ViewerError("Q2VT_PUB_WEBGL", "WebGL unavailable");
    viewer.archives = await openArchives(manifest, manifestUrl, maplibregl);
    const styleUrl = resolveUrl(manifest.style, manifestUrl);
    const style = bindStyle(await fetchChecked(styleUrl), manifest, manifestUrl, styleUrl);
    viewer.style = style;
    const view = manifest.view;
    const map = new maplibregl.Map({
      container: "q2vt-map",
      style,
      center: view.center,
      zoom: view.zoom,
      minZoom: view.minZoom ?? 0,
      maxZoom: view.maxZoom ?? 22,
      bearing: 0,
      pitch: 0,
      maxPitch: 0,
      dragRotate: false,
      pitchWithRotate: false,
      touchPitch: false,
      attributionControl: false,
      renderWorldCopies: false,
      canvasContextAttributes: { preserveDrawingBuffer: new URLSearchParams(location.search).has("print") },
    });
    map.touchZoomRotate.disableRotation();
    viewer.map = map;
    map.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: manifest.attribution || undefined }));
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.FullscreenControl(), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    map.on("error", (event) => {
      const message = String(event && event.error && event.error.message || event);
      diagnostics.warnings.push({ key: "map", detail: message.slice(0, 500) });
      if (/sprite/i.test(message)) warn("error.sprite", message);
    });
    await new Promise((resolve) => map.once("load", resolve));
    document.body.classList.add("q2vt-loaded");
    const sourceId = manifest.sources[0].id;
    viewer.labels = enableVisibleLabels(map, maplibregl, sourceId, {
      onUnsupported: (reason) => warn("error.labels", reason),
    });
    // Interactive controls (layer tree, legend, popups, search, filters,
    // permalinks, tools) live in their own modules.
    const controls = await import("./controls.mjs").catch((error) => {
      diagnostics.warnings.push({ key: "controls", detail: String(error).slice(0, 500) });
      return null;
    });
    if (controls) viewer.controls = await controls.mount({ map, manifest, manifestUrl, pageUrl, assetsUrl, maplibregl, viewer });
    viewer.ready = true;
    document.dispatchEvent(new CustomEvent("q2vt:ready"));
  } catch (error) {
    showError(error);
  }
}

start();
