// QWebMap web map viewer (release entry point).
//
// Loads manifest.json (relative to this page), validates it (schema 1,
// vector-only), binds the vector tile transport (PMTiles over HTTP ranges or
// XYZ MVT), loads the exported MapLibre style unchanged except for transport
// URLs, creates the map and the existing visible-polygon label helper, then
// mounts the interactive controls described by the manifest.
import { replaceUrl } from "./backstack.mjs";
import * as maplibregl from "./maplibre-gl.mjs";
import { enableVisibleLabels } from "./visible_labels.mjs";
import { RASTER_TILE_TYPES, ViewerError, bindStyle, checkRelative, openArchives, resolveUrl } from "./transport.mjs";
import { loadLocale, t } from "./i18n.mjs";
import { fetchChecked, notice, showError, state as diagnostics, warn } from "./diagnostics.mjs";
import { installDiagrams } from "./diagrams.mjs";
import { installShadows, prepareShadows } from "./label_shadows.mjs";

export const EXPECTED_MAPLIBRE = "6.14.0";
const RELEASE_ID = /^r-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$/;
// Style layers end at zoom 24 (the style spec maximum) and MapLibre hides a
// layer at exactly its maxzoom: the browser stops just short of it.
const MAX_VIEW_ZOOM = 23.99;

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

// Accent colour, logo and subtitle from the manifest (validated values only).
function applyBranding(manifest, pageUrl) {
  const accent = manifest.ui && manifest.ui.accent;
  if (typeof accent === "string" && /^#[0-9a-fA-F]{6}$/.test(accent)) {
    document.documentElement.style.setProperty("--q2vt-accent", accent);
  }
  const logo = document.getElementById("q2vt-logo");
  if (logo && typeof manifest.logo === "string" && /^assets\/logo\.(png|jpe?g|webp)$/.test(manifest.logo)) {
    logo.src = new URL(manifest.logo, pageUrl).href;
    logo.hidden = false;
  }
  const subtitle = document.getElementById("q2vt-subtitle");
  if (subtitle && manifest.description) {
    subtitle.textContent = manifest.description.split("\n")[0].slice(0, 140);
    subtitle.hidden = false;
    const full = document.getElementById("q2vt-description");
    if (full && full.textContent.trim() === subtitle.textContent.trim()) full.hidden = true;
  }
}

// The map's own data (manifest.info): the legal and data date under the
// title (separate from the export date), and in the panel footer who issued
// it, the decree and the documents published with it (links to docs/).
export function infoStamp(info) {
  if (!info) return "";
  return [info.legalDate && `${t("info.legalDate")}: ${info.legalDate}`,
          info.dataDate && `${t("info.dataDate")}: ${info.dataDate}`].filter(Boolean).join(" · ");
}

function applyInfo(manifest, pageUrl) {
  const info = manifest.info;
  if (!info || typeof info !== "object") return;
  const stamp = document.getElementById("q2vt-stamp");
  const text = infoStamp(info);
  if (stamp && text) { stamp.textContent = text; stamp.hidden = false; }
  const host = document.getElementById("q2vt-info");
  if (!host) return;
  // Collapsed: the dates are under the title already; the panel keeps its room.
  const box = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = t("info.about");
  box.append(summary);
  const rows = [["info.issuer", info.issuer], ["info.decree", info.decree], ["info.legalDate", info.legalDate],
                ["info.dataDate", info.dataDate]].filter(([, value]) => typeof value === "string" && value);
  const list = document.createElement("dl");
  for (const [key, value] of rows) {
    const term = document.createElement("dt");
    term.textContent = t(key);
    const detail = document.createElement("dd");
    detail.textContent = value;
    list.append(term, detail);
  }
  if (rows.length) box.append(list);
  const documents = (Array.isArray(info.documents) ? info.documents : [])
    .filter((d) => d && typeof d.href === "string" && /^docs\/[a-z0-9-]+\.[a-z]+$/.test(d.href));
  if (documents.length) {
    const heading = document.createElement("h3");
    heading.textContent = t("info.documents");
    const items = document.createElement("ul");
    items.className = "q2vt-documents";
    for (const doc of documents) {
      const link = document.createElement("a");
      link.href = new URL(doc.href, pageUrl).href;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = doc.title || doc.file;
      const item = document.createElement("li");
      item.append(link);
      const ext = doc.href.split(".").pop().toUpperCase();
      const size = Number(doc.size) > 0 ? `${ext}, ${Math.max(1, Math.round(doc.size / 1024))} kB` : ext;
      const note = document.createElement("span");
      note.className = "q2vt-muted";
      note.textContent = ` (${size})`;
      item.append(note);
      items.append(item);
    }
    box.append(heading, items);
  }
  if (box.childElementCount > 1) host.append(box);
  host.hidden = !host.childElementCount;
}

// Opened through the publication's stable entry: show that (short, stable)
// address again. Relative URLs keep resolving in the release (<base>); the
// release's own address stays known for "this version" links.
function showEntryAddress(releasePage) {
  let entry = null;
  try {
    entry = JSON.parse(sessionStorage.getItem("q2vt:entry") || "null");
    sessionStorage.removeItem("q2vt:entry");
  } catch { return; }
  if (!entry || entry.release !== location.pathname || typeof entry.entry !== "string"
      || !entry.entry.startsWith("/") || entry.entry.startsWith("//")) return;
  const base = document.createElement("base");
  base.href = releasePage.href;
  document.head.prepend(base);
  replaceUrl(entry.entry + location.search + location.hash);
}

async function start() {
  const releasePage = new URL(location.href);
  const pageUrl = new URL(".", releasePage);
  showEntryAddress(releasePage);
  const assetsUrl = new URL("./", import.meta.url);  // locales, legend code, workers
  const manifestUrl = new URL("manifest.json", pageUrl).href;
  const viewer = (window.q2vtViewer = { ready: false, diagnostics, maplibreVersion: maplibregl.getVersion(),
    releaseUrl: releasePage.href });
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
  // Embedded in another page (?embed): compact header, panel closed, a link
  // to the full map, and the page keeps the scroll wheel (Ctrl + scroll or
  // two fingers zoom the map).
  const embedded = new URLSearchParams(location.search).has("embed");
  if (embedded) mountEmbed();
  setText("q2vt-title", manifest.title || "");
  setText("q2vt-description", manifest.description || "");
  setText("q2vt-release", `${t("app.release")}: ${manifest.releaseId}`);
  setText("q2vt-loading-text", t("app.loading"));
  applyBranding(manifest, pageUrl);
  applyInfo(manifest, pageUrl);
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
    prepareShadows(style);  // feature ids for point label shadows
    const view = manifest.view;
    const stay = view.limitToExtent && extentLimit(view, maplibregl);
    const map = new maplibregl.Map({
      container: "q2vt-map",
      style,
      center: view.center,
      zoom: view.zoom,
      minZoom: view.minZoom ?? 0,
      maxZoom: Math.min(view.maxZoom ?? 22, MAX_VIEW_ZOOM),
      bearing: 0,
      pitch: 0,
      maxPitch: 0,
      dragRotate: false,
      pitchWithRotate: false,
      touchPitch: false,
      attributionControl: false,
      renderWorldCopies: false,
      cooperativeGestures: embedded,
      locale: {
        "CooperativeGesturesHandler.WindowsHelpText": t("embed.zoomWindows"),
        "CooperativeGesturesHandler.MacHelpText": t("embed.zoomMac"),
        "CooperativeGesturesHandler.MobileHelpText": t("embed.zoomMobile"),
      },
      transformConstrain: stay ? stay.constrain : null,
      canvasContextAttributes: { preserveDrawingBuffer: new URLSearchParams(location.search).has("print") },
    });
    map.touchZoomRotate.disableRotation();
    viewer.map = map;
    viewer.diagrams = installDiagrams(map, style);
    viewer.labelShadows = installShadows(map, style);
    if (stay) stay.attach(map);
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.FullscreenControl(), "top-right");
    if (manifest.interaction?.geolocation === true && window.isSecureContext && navigator.geolocation) {
      map.addControl(locateControl(map, maplibregl, view.bounds), "top-right");
    }
    map.on("error", (event) => {
      const message = String(event && event.error && event.error.message || event);
      diagnostics.warnings.push({ key: "map", detail: message.slice(0, 500) });
      if (/sprite/i.test(message)) warn("error.sprite", message);
    });
    // A style MapLibre rejects fires "error" and never "style.load": shown
    // instead of loading for ever. Errors after the style was accepted (a
    // source layer with no tiles, a missing tile or image) do not stop it.
    await new Promise((resolve, reject) => {
      const failed = (event) => {
        map.off("error", failed);
        const message = String(event && event.error && event.error.message || event);
        reject(new ViewerError("Q2VT_PUB_STYLE", "the map style was rejected", message));
      };
      map.on("error", failed);
      map.once("style.load", () => map.off("error", failed));
      map.once("load", () => { map.off("error", failed); resolve(); });
    });
    document.body.classList.add("q2vt-loaded");
    screenHairlines(map);
    const sourceId = manifest.sources[0].id;
    // Layers whose every feature keeps its label (shrunk where it does not fit).
    const always = new Set();
    for (const layer of manifest.layers || []) {
      if (!layer.labelAlways) continue;
      for (const component of manifest.components || []) {
        if (component.layerId === layer.id) for (const id of component.styleLayerIds || []) always.add(id);
      }
    }
    viewer.labels = enableVisibleLabels(map, maplibregl, sourceId, {
      always,
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


// "Thin hairlines" (style metadata q2vt:hairline): the exported width is in
// device pixels - half of one where lines crowd. A pixel cannot be thinner,
// only lighter: at a 2x (4K) screen 0.5 CSS px was a whole device pixel,
// QGIS's hairline. Divided by the screen's pixel ratio (again when the window
// moves to another screen).
export function screenHairlines(map) {
  const exported = new Map();
  for (const layer of map.getStyle().layers || []) {
    if (layer.metadata?.["q2vt:hairline"]) exported.set(layer.id, layer.paint?.["line-width"]);
  }
  if (!exported.size) return;
  const scaled = (value, ratio) => (typeof value === "number" ? value / ratio
    : Array.isArray(value) && value[0] === "interpolate"
      ? value.map((v, i) => (i > 3 && i % 2 === 0 && typeof v === "number" ? v / ratio : v)) : value);
  const apply = () => {
    const ratio = Math.max(1, window.devicePixelRatio || 1);
    for (const [id, width] of exported) {
      if (map.getLayer(id)) map.setPaintProperty(id, "line-width", scaled(width, ratio));
    }
    matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`)
      .addEventListener("change", apply, { once: true });
  };
  apply();
}


// "Keep the web map on the extent": the map's centre cannot leave the
// publication extent, and it zooms out at most one level beyond the zoom
// that shows the whole extent (recomputed when the window is resized).
// "My location" (an option of the map): one position, then the view goes
// there with a dot. A position outside the map's extent only says so; the
// map stays (an empty map far away helps no one).
function locateControl(map, maplibregl, bounds) {
  let marker = null;
  return {
    onAdd() {
      const box = document.createElement("div");
      box.className = "maplibregl-ctrl maplibregl-ctrl-group";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "maplibregl-ctrl-geolocate";
      button.title = t("app.locate");
      button.setAttribute("aria-label", t("app.locate"));
      const icon = document.createElement("span");
      icon.className = "maplibregl-ctrl-icon";
      icon.setAttribute("aria-hidden", "true");
      button.append(icon);
      box.append(button);
      button.addEventListener("click", () => {
        navigator.geolocation.getCurrentPosition((position) => {
          const { longitude, latitude } = position.coords;
          const [west, south, east, north] = bounds || [];
          if ([west, south, east, north].every(Number.isFinite) &&
              (longitude < west || longitude > east || latitude < south || latitude > north)) {
            notice("app.locateOutside");
            return;
          }
          if (!marker) {
            const dot = document.createElement("div");
            dot.className = "maplibregl-user-location-dot";
            marker = new maplibregl.Marker({ element: dot });
          }
          marker.setLngLat([longitude, latitude]).addTo(map);
          map.flyTo({ center: [longitude, latitude],
            zoom: Math.max(map.getZoom(), Math.min(17, map.getMaxZoom())) });
        }, () => notice("app.locateFailed"), { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 });
      });
      return box;
    },
    onRemove() {
      if (marker) marker.remove();
    },
  };
}

export function extentLimit(view, maplibregl) {
  const [west, south, east, north] = view.bounds || [];
  if (![west, south, east, north].every(Number.isFinite) || west >= east || south >= north) return null;
  const clamp = (value, low, high) => Math.min(Math.max(value, low), high);
  let map = null;
  let floor = view.minZoom ?? 0;
  return {
    constrain(center, zoom) {
      const low = Math.max(floor, map ? map.getMinZoom() : view.minZoom ?? 0);
      const high = map ? map.getMaxZoom() : view.maxZoom ?? 22;
      return {
        center: new maplibregl.LngLat(clamp(center.lng, west, east), clamp(center.lat, south, north)),
        zoom: clamp(zoom, low, high),
      };
    },
    attach(target) {
      map = target;
      const update = () => {
        const camera = map.cameraForBounds([[west, south], [east, north]], { padding: 0 });
        if (camera && Number.isFinite(camera.zoom)) floor = Math.max(view.minZoom ?? 0, camera.zoom - 1);
        map.jumpTo({ center: map.getCenter(), zoom: map.getZoom() });  // apply the new floor
      };
      update();
      map.on("resize", update);
    },
  };
}


// Embedded mode: the body class compacts the layout (styles.css) and the
// controls keep the panel closed; "Open the full map" opens this view
// (the current address without ?embed, hash included) in a new tab.
function mountEmbed() {
  document.body.classList.add("q2vt-embed");
  const row = document.querySelector("#q2vt-header .q2vt-header-row");
  if (!row) return;
  const link = document.createElement("a");
  link.id = "q2vt-full-map";
  link.target = "_blank";
  link.rel = "noopener";
  link.textContent = t("embed.fullMap");
  link.title = t("embed.fullMap");
  const update = () => {
    const url = new URL(location.href);
    url.searchParams.delete("embed");
    link.href = url.href;
  };
  update();
  for (const name of ["mousedown", "focus", "touchstart"]) link.addEventListener(name, update, { passive: true });
  row.append(link);
}

