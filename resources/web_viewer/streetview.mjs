// Google Street View (optional: manifest.interaction.streetView with a Google
// Maps API key). A map button switches the mode on: blue lines show where
// Street View exists (Google Map Tiles API, Street View layer), and a tap on
// what the visitor wants to see opens the nearest panorama *looking at that
// spot* (the tap chooses the direction). The panorama turns by dragging; the
// cone on the map follows, and dragging the cone moves the viewpoint. A tap
// with no panorama nearby says so at once. Desktop: a resizable panel;
// phones: the top half of the screen (map below), or full screen.
//
// Google's code and imagery load only when the mode is first used.
import { el, icon } from "./icons.mjs";
import { t } from "./i18n.mjs";

const COVERAGE = "q2vt_streetview_coverage";
const SEARCH_RADII = [30, 80, 200];  // m: the nearest panorama, a little further if none
const FACE_MIN_M = 3;                // a tap this close to the panorama keeps the heading

let googleReady = null;

// The Maps JavaScript API (loaded once, on first use).
export function loadGoogle(key, language) {
  if (window.google && window.google.maps) return Promise.resolve(window.google.maps);
  if (googleReady) return googleReady;
  googleReady = new Promise((resolve, reject) => {
    const callback = `__q2vtGoogle${Date.now()}`;
    window[callback] = () => { delete window[callback]; resolve(window.google.maps); };
    const script = document.createElement("script");
    const params = new URLSearchParams({ key, v: "weekly", loading: "async", callback,
                                         language: language || "en" });
    script.src = `https://maps.googleapis.com/maps/api/js?${params}`;
    script.async = true;
    script.onerror = () => { googleReady = null; reject(new Error("Google Maps could not be loaded")); };
    document.head.append(script);
  });
  return googleReady;
}

async function streetViewLibrary(maps) {
  if (maps.importLibrary) {
    try { return await maps.importLibrary("streetView"); } catch { /* the classic namespace */ }
  }
  return maps;
}

// Metres and compass bearing (degrees clockwise from north) from a to b.
export function distanceBearing(a, b) {
  const rad = Math.PI / 180;
  const lat1 = a.lat * rad, lat2 = b.lat * rad, dLng = (b.lng - a.lng) * rad;
  const x = Math.sin((lat2 - lat1) / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
  const metres = 2 * 6371008.8 * Math.asin(Math.min(1, Math.sqrt(x)));
  const y = Math.sin(dLng) * Math.cos(lat2);
  const z = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLng);
  return { metres, bearing: (Math.atan2(y, z) / rad + 360) % 360 };
}

// The viewpoint marker: a dot with a cone towards the heading.
function markerElement() {
  const node = el("div", "q2vt-sv-marker");
  node.innerHTML = '<svg viewBox="-30 -30 60 60" width="60" height="60" aria-hidden="true">'
    + '<path class="q2vt-sv-cone" d="M0 0 L-17 -26 A31 31 0 0 1 17 -26 Z"/>'
    + '<circle class="q2vt-sv-dot" r="8"/></svg>';
  node.title = t("streetview.drag");
  return node;
}

export class StreetView {
  constructor({ map, maplibregl, manifest, viewer }) {
    Object.assign(this, { map, maplibregl, manifest, viewer });
    const interaction = manifest.interaction || {};
    this.key = interaction.googleApiKey || "";
    this.language = manifest.locale || document.documentElement.lang || "en";
    this.active = false;
    this.heading = 0;
    this.onMapClick = (event) => { this.showAt(event.lngLat); };
  }

  get available() { return !!this.key; }

  // MapLibre control: the Street View button.
  onAdd() {
    this.container = el("div", "maplibregl-ctrl maplibregl-ctrl-group q2vt-sv-ctrl");
    this.button = el("button", "q2vt-sv-button");
    this.button.type = "button";
    this.button.append(icon("person", 20));
    this.button.title = t("streetview.button");
    this.button.setAttribute("aria-label", t("streetview.button"));
    this.button.setAttribute("aria-pressed", "false");
    this.button.addEventListener("click", () => (this.active ? this.stop() : this.start()));
    this.container.append(this.button);
    return this.container;
  }

  onRemove() { this.stop(); }

  async start() {
    if (this.active) return;
    if (this.viewer.controls?.tools?.stop) this.viewer.controls.tools.stop(false);  // no measuring meanwhile
    this.active = true;
    this.viewer.streetView = true;
    // Phones: the panorama takes the top of the screen; the panel sheet goes.
    if (window.matchMedia("(max-width: 760px)").matches) this.viewer.controls?.panel?.setOpen?.(false);
    this.button.setAttribute("aria-pressed", "true");
    this.map.getCanvas().style.cursor = "crosshair";
    this.map.on("click", this.onMapClick);
    this.hint(t("streetview.hint"));
    // Google calls this when the key is refused (wrong key, API or site).
    window.gm_authFailure = () => {
      this.viewer.diagnostics?.warnings?.push({ key: "streetview", detail: "Google refused the API key" });
      this.hint(t("streetview.unavailable"), true, true);
    };
    try {
      this.maps = await loadGoogle(this.key, this.language);
      this.lib = await streetViewLibrary(this.maps);
      this.service = new this.lib.StreetViewService();
    } catch (error) {
      this.hint(t("streetview.unavailable"), true);
      this.viewer.diagnostics?.warnings?.push({ key: "streetview", detail: String(error).slice(0, 300) });
      return;
    }
    if (this.active) this.showCoverage();
  }

  stop() {
    if (!this.active) return;
    this.active = false;
    this.viewer.streetView = false;
    this.button?.setAttribute("aria-pressed", "false");
    this.map.off("click", this.onMapClick);
    this.map.getCanvas().style.cursor = "";
    if (this.map.getLayer(COVERAGE)) this.map.setLayoutProperty(COVERAGE, "visibility", "none");
    this.marker?.remove();
    this.marker = null;
    this.panel?.remove();
    this.panel = null;
    this.panorama = null;
    clearTimeout(this.hintTimer);
    this.hintNode?.remove();
    this.hintNode = null;
  }

  // Where Street View exists: the Map Tiles API's Street View layer, as a
  // transparent overlay. Without that API enabled the lines are missing and
  // a tap still finds (or not) the nearest panorama.
  async showCoverage() {
    if (this.map.getLayer(COVERAGE)) {
      this.map.setLayoutProperty(COVERAGE, "visibility", "visible");
      return;
    }
    try {
      const region = (this.language.split("-")[1] || (this.language === "hu" ? "HU" : "US")).toUpperCase();
      const response = await fetch(`https://tile.googleapis.com/v1/createSession?key=${encodeURIComponent(this.key)}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mapType: "roadmap", language: this.language, region,
                               layerTypes: ["layerStreetview"], overlay: true,
                               scale: window.devicePixelRatio > 1 ? "scaleFactor2x" : "scaleFactor1x" }),
      });
      if (!response.ok) throw new Error(`Map Tiles API: HTTP ${response.status}`);
      const { session } = await response.json();
      if (!session || !this.active) return;
      this.map.addSource(COVERAGE, {
        type: "raster", tileSize: 256, maxzoom: 22,
        tiles: [`https://tile.googleapis.com/v1/2dtiles/{z}/{x}/{y}?session=${encodeURIComponent(session)}&key=${encodeURIComponent(this.key)}`],
        attribution: "Street View © Google",
      });
      this.map.addLayer({ id: COVERAGE, type: "raster", source: COVERAGE, paint: { "raster-opacity": 0.85 } });
    } catch (error) {
      this.coverageMissing = true;
      this.viewer.diagnostics?.warnings?.push({ key: "streetview-coverage", detail: String(error).slice(0, 300) });
      if (this.active && !this.panorama) this.hint(this.baseHint());
    }
  }

  // The nearest panorama to ``target``, looking at it.
  async showAt(target) {
    if (!this.service) return;
    const ticket = (this.ticket = (this.ticket || 0) + 1);  // only the last tap counts
    let found = null;
    for (const radius of SEARCH_RADII) {
      try {
        const { data } = await this.service.getPanorama({
          location: { lat: target.lat, lng: target.lng }, radius,
          preference: this.lib.StreetViewPreference?.NEAREST || "nearest",
          sources: [this.lib.StreetViewSource?.OUTDOOR || "outdoor"],
        });
        found = data;
        break;
      } catch { /* none within this radius */ }
      if (ticket !== this.ticket || !this.active) return;
    }
    if (ticket !== this.ticket || !this.active) return;
    if (!found || !found.location) {
      this.hint(t("streetview.none"), true);
      return;
    }
    const at = found.location.latLng;
    const position = { lat: typeof at.lat === "function" ? at.lat() : at.lat,
                       lng: typeof at.lng === "function" ? at.lng() : at.lng };
    const { metres, bearing } = distanceBearing(position, target);
    if (metres >= FACE_MIN_M) this.heading = bearing;
    this.open(found.location.pano, position);
  }

  open(pano, position) {
    this.ensurePanel();
    if (!this.panorama) {
      this.panorama = new this.lib.StreetViewPanorama(this.panoNode, {
        pano, pov: { heading: this.heading, pitch: 0 }, zoom: 0, visible: true,
        addressControl: true, fullscreenControl: false, motionTracking: false,
        motionTrackingControl: false, enableCloseButton: false, showRoadLabels: true,
      });
      this.panorama.addListener("pov_changed", () => {
        this.heading = this.panorama.getPov().heading;
        this.marker?.setRotation(this.heading);
      });
      this.panorama.addListener("position_changed", () => {
        const p = this.panorama.getPosition();
        if (p) this.place({ lat: p.lat(), lng: p.lng() }, true);
      });
    } else {
      this.panorama.setPano(pano);
      this.panorama.setPov({ heading: this.heading, pitch: 0 });
      this.panorama.setVisible(true);
    }
    this.place(position, false);
    this.hint(t("streetview.hintMove"));
  }

  // The viewpoint on the map; kept in sight (below the panel on phones).
  place(position, fromPanorama) {
    if (!this.marker) {
      this.marker = new this.maplibregl.Marker({ element: markerElement(), draggable: true,
                                                 rotationAlignment: "map", pitchAlignment: "map" });
      this.marker.on("dragend", () => this.showAt(this.marker.getLngLat()));
      this.marker.setLngLat(position).addTo(this.map);
    } else {
      this.marker.setLngLat(position);
    }
    this.marker.setRotation(this.heading);
    // Keep the viewpoint in sight: out of the panel (on top on phones, at
    // the bottom on desktops) and off the edges.
    const mapBox = this.map.getContainer().getBoundingClientRect();
    const panel = this.panel && !this.panel.classList.contains("q2vt-sv-full") && this.panel.getBoundingClientRect();
    const padding = { top: 0, bottom: 0 };
    if (panel && panel.top <= mapBox.top + 1) padding.top = panel.bottom - mapBox.top;
    else if (panel) padding.bottom = mapBox.bottom - panel.top;
    const point = this.map.project(position);
    const inside = point.x > 40 && point.x < mapBox.width - 40
      && point.y > padding.top + 40 && point.y < mapBox.height - padding.bottom - 40;
    // offset, not padding: MapLibre keeps a padding for good.
    if (!inside) this.map.easeTo({ center: position, offset: [0, (padding.top - padding.bottom) / 2],
                                   duration: fromPanorama ? 250 : 400 });
  }

  ensurePanel() {
    if (this.panel) return;
    const panel = el("section", "q2vt-sv-panel");
    panel.setAttribute("aria-label", t("streetview.title"));
    const head = el("header", "q2vt-sv-head");
    const title = el("h2", "", t("streetview.title"));
    const expand = el("button", "q2vt-icon-btn");
    expand.type = "button";
    expand.append(icon("expand", 18));
    expand.title = t("streetview.expand");
    expand.setAttribute("aria-label", t("streetview.expand"));
    expand.addEventListener("click", () => {
      panel.classList.toggle("q2vt-sv-full");
      this.maps?.event?.trigger?.(this.panorama, "resize");
    });
    const close = el("button", "q2vt-icon-btn");
    close.type = "button";
    close.append(icon("close", 18));
    close.title = t("app.close");
    close.setAttribute("aria-label", t("streetview.close"));
    close.addEventListener("click", () => this.stop());
    head.append(title, expand, close);
    this.panoNode = el("div", "q2vt-sv-pano");
    panel.append(head, this.panoNode);
    document.getElementById("q2vt-app").append(panel);
    this.panel = panel;
  }

  baseHint() {
    if (this.panorama) return t("streetview.hintMove");
    return t(this.coverageMissing ? "streetview.hintNoCoverage" : "streetview.hint");
  }

  hint(text, alert = false, sticky = false) {
    if (!this.active) return;
    if (!this.hintNode) {
      this.hintNode = el("div", "q2vt-sv-hint");
      this.hintNode.setAttribute("role", "status");
      document.getElementById("q2vt-app").append(this.hintNode);
    }
    this.hintNode.classList.toggle("q2vt-sv-alert", alert);
    this.hintNode.textContent = text;
    clearTimeout(this.hintTimer);
    if (alert && !sticky) this.hintTimer = setTimeout(() => this.hint(this.baseHint()), 3500);
  }
}
