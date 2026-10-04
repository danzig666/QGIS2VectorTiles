// Mounts the interactive controls of a published map (called by app.mjs
// once the map and the visible-polygon label helper are ready): the panel
// (layers, legend, filters, tools, share), theme presets, the basemap
// switcher, attribution and the light/dark switch.
import { t } from "./i18n.mjs";
import { ViewerState, defaults as defaultState, presetMatches, presets, sanitizeFilters } from "./state.mjs";
import { StyleControl } from "./style_control.mjs";
import { LayerControls } from "./layer_controls.mjs";
import { Legend } from "./legend.mjs";
import { Printer } from "./print.mjs";
import { Filters } from "./filters.mjs";
import { Identify } from "./identify.mjs";
import { FeatureLookup } from "./features.mjs";
import { Search, goTo } from "./search.mjs";
import { Permalink, decodeState } from "./permalink.mjs";
import { Tools } from "./tools.mjs";
import { Basemap } from "./basemap.mjs";
import { ParcelReport } from "./parcel_report.mjs";
import { warn } from "./diagnostics.mjs";
import { button, el, icon } from "./icons.mjs";

const MOBILE = "(max-width: 760px)";
const THEME_KEY = "q2vt:ui-theme";
const PANES = { layers: "layers", parcel: "pin", legend: "legend", filters: "filter", tools: "tools", share: "share" };

function panes(manifest) {
  // The Layers tab can be switched off in the plugin: the legend only.
  const list = manifest.interaction?.layersPanel === false ? [] : [["layers", "app.layers"]];
  if (manifest.parcelInfo) list.push(["parcel", "app.parcel"]);
  list.push(["legend", "app.legend"]);
  if (manifest.interaction?.filters !== false && manifest.layers.some((l) => (l.filterFields || []).length)) list.push(["filters", "app.filters"]);
  const tools = manifest.tools || {};
  if (tools.coordinates || tools.measure || tools.print) list.push(["tools", "app.tools"]);
  if (manifest.interaction?.permalinks !== false) list.push(["share", "app.share"]);
  return list;
}

function isMobile() {
  return window.matchMedia ? window.matchMedia(MOBILE).matches : window.innerWidth <= 760;
}

function buildPanel(manifest) {
  const tabs = document.getElementById("q2vt-tabs");
  const container = document.getElementById("q2vt-panes");
  const result = {};
  const buttons = [];
  for (const [id, label] of panes(manifest)) {
    const tab = button("", null, PANES[id], { text: t(label) });
    tab.id = `q2vt-tab-${id}`;
    tab.title = t(label);
    tab.setAttribute("aria-label", t(label));
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", `q2vt-pane-${id}`);
    const pane = el("div", "q2vt-pane");
    pane.id = `q2vt-pane-${id}`;
    pane.dataset.pane = id;
    pane.setAttribute("role", "tabpanel");
    pane.setAttribute("aria-labelledby", tab.id);
    tab.addEventListener("click", () => select(id));
    tabs.append(tab);
    container.append(pane);
    buttons.push([id, tab, pane]);
    result[id] = pane;
  }
  function select(id) {
    for (const [paneId, tab, pane] of buttons) {
      tab.setAttribute("aria-selected", String(paneId === id));
      tab.tabIndex = paneId === id ? 0 : -1;
      pane.hidden = paneId !== id;
    }
    container.scrollTop = 0;
  }
  tabs.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    const index = buttons.findIndex(([, tab]) => tab.getAttribute("aria-selected") === "true");
    const next = buttons[(index + (event.key === "ArrowRight" ? 1 : buttons.length - 1)) % buttons.length];
    select(next[0]);
    next[1].focus();
  });
  select(result.layers ? "layers" : (result.legend ? "legend" : panes(manifest)[0][0]));
  const panel = document.getElementById("q2vt-panel");
  const menu = document.getElementById("q2vt-menu");
  menu.replaceChildren(icon("menu", 22));
  const setOpen = (open) => {
    panel.hidden = !open;
    menu.setAttribute("aria-expanded", String(open));
    menu.replaceChildren(icon(open && isMobile() ? "close" : "menu", 22));
    document.body.classList.toggle("q2vt-panel-open", open);
    if (!open) panel.classList.remove("q2vt-expanded");
  };
  menu.setAttribute("aria-label", t("app.menu"));
  menu.title = t("app.menu");
  menu.addEventListener("click", () => { setOpen(panel.hidden); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !panel.hidden && isMobile()) { setOpen(false); menu.focus(); }
  });
  // Bottom sheet on phones: tap the handle to grow/shrink, drag down to close.
  const handle = document.getElementById("q2vt-sheet-handle");
  if (handle) {
    handle.setAttribute("aria-label", t("app.resize"));
    handle.addEventListener("click", () => panel.classList.toggle("q2vt-expanded"));
    let startY = null;
    handle.addEventListener("touchstart", (e) => { startY = e.touches[0].clientY; }, { passive: true });
    handle.addEventListener("touchmove", (e) => {
      if (startY === null) return;
      const dy = e.touches[0].clientY - startY;
      if (dy > 70) { startY = null; if (panel.classList.contains("q2vt-expanded")) panel.classList.remove("q2vt-expanded"); else setOpen(false); }
      else if (dy < -50) { startY = null; panel.classList.add("q2vt-expanded"); }
    }, { passive: true });
    handle.addEventListener("touchend", () => { startY = null; });
  }
  setOpen(!isMobile());
  return { panes: result, select, setOpen };
}

function shareBlock(container, permalink) {
  const block = el("div", "q2vt-share");
  const status = el("p", "q2vt-muted");
  status.setAttribute("aria-live", "polite");
  for (const [which, label] of [["stable", "app.linkCurrent"], ["versioned", "app.linkRelease"]]) {
    const node = button("q2vt-chip", null, "link", { text: t(label) });
    node.addEventListener("click", async () => {
      await permalink.copy({}, which);
      status.textContent = t("app.copied");
    });
    block.append(node);
  }
  container.append(block, status);
}

// Light / dark switch (follows the system until chosen; per browser).
function themeToggle(container) {
  const root = document.documentElement;
  const saved = (() => { try { return localStorage.getItem(THEME_KEY); } catch { return null; } })();
  if (saved === "light" || saved === "dark") root.dataset.theme = saved;
  const dark = () => root.dataset.theme === "dark"
    || (!root.dataset.theme && window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  const node = button("q2vt-icon-btn", t("app.darkMode"), dark() ? "sun" : "moon");
  node.addEventListener("click", () => {
    const next = dark() ? "light" : "dark";
    root.dataset.theme = next;
    try { localStorage.setItem(THEME_KEY, next); } catch { /* storage unavailable */ }
    node.replaceChildren(icon(next === "dark" ? "sun" : "moon"));
  });
  container.append(node);
}

// Flavor preview: earth, park, water and two roads in the flavor's colours.
function preview(colors) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 80 80");
  svg.setAttribute("preserveAspectRatio", "xMidYMid slice");
  const color = (value, fallback) => (/^#[0-9a-f]{3,8}$/i.test(value || "") ? value : fallback);
  const add = (tag, attrs) => {
    const node = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    svg.append(node);
  };
  add("rect", { width: 80, height: 80, fill: color(colors.earth, "#e2dfda") });
  add("path", { d: "M0 54 C 18 44, 30 62, 52 56 S 80 60, 80 60 V80 H0z", fill: color(colors.water, "#80deea") });
  add("path", { d: "M50 6 h22 v20 h-22z", fill: color(colors.park, "#cfddd5"), rx: 3 });
  add("rect", { x: 10, y: 14, width: 9, height: 7, fill: color(colors.buildings, "#cccccc") });
  add("rect", { x: 24, y: 26, width: 8, height: 9, fill: color(colors.buildings, "#cccccc") });
  add("path", { d: "M-2 38 L 82 30", stroke: color(colors.highway, "#ffffff"), "stroke-width": 5, fill: "none" });
  add("path", { d: "M38 -2 L 42 82", stroke: color(colors.road, "#ffffff"), "stroke-width": 3, fill: "none" });
  return svg;
}

class BasemapControl {
  constructor(basemap, state) {
    this.basemap = basemap;
    this.state = state;
  }

  onAdd() {
    const root = el("div", "maplibregl-ctrl q2vt-basemap");
    const menu = el("div", "q2vt-basemap-menu q2vt-floating");
    menu.hidden = true;
    menu.setAttribute("role", "menu");
    menu.append(el("h4", "", t("basemap.title")));
    const toggleButton = el("button", "q2vt-basemap-btn");
    toggleButton.type = "button";
    toggleButton.setAttribute("aria-haspopup", "true");
    toggleButton.setAttribute("aria-expanded", "false");
    const options = [{ id: "none", title: t("basemap.none") }, ...this.basemap.options()];
    this.optionButtons = new Map();
    for (const option of options) {
      const node = el("button", "q2vt-basemap-option");
      node.type = "button";
      node.setAttribute("role", "menuitemradio");
      const thumb = el("span", "q2vt-basemap-thumb");
      if (option.id === "none") {
        const blank = el("span", "q2vt-basemap-none");
        blank.append(icon("close", 18));
        thumb.append(blank);
      } else if (option.tiles) {  // a web basemap (XYZ tiles)
        const web = el("span", "q2vt-basemap-none");
        web.append(icon("image", 20));
        thumb.append(web);
      } else {
        thumb.append(preview(option.colors || {}));
      }
      node.append(thumb, el("span", "", option.title));
      node.addEventListener("click", () => {
        this.state.set({ basemap: option.id }, "basemap");
        menu.hidden = true;
        toggleButton.setAttribute("aria-expanded", "false");
      });
      this.optionButtons.set(option.id, node);
      menu.append(node);
    }
    toggleButton.addEventListener("click", () => {
      menu.hidden = !menu.hidden;
      toggleButton.setAttribute("aria-expanded", String(!menu.hidden));
    });
    document.addEventListener("click", (event) => {
      if (!root.contains(event.target)) { menu.hidden = true; toggleButton.setAttribute("aria-expanded", "false"); }
    });
    this.toggleButton = toggleButton;
    root.append(menu, toggleButton);
    this.sync(this.state.value.basemap);
    return root;
  }

  sync(active) {
    const flavor = this.basemap.option(active);
    this.toggleButton.replaceChildren(flavor && !flavor.tiles ? preview(flavor.colors || {}) : (() => {
      const blank = el("span", "q2vt-basemap-none");
      blank.append(icon(flavor ? "image" : "map", 22));
      return blank;
    })(), el("span", "q2vt-basemap-caption", t("basemap.title")));
    this.toggleButton.setAttribute("aria-label", `${t("basemap.title")}: ${flavor ? flavor.title : t("basemap.none")}`);
    this.toggleButton.title = this.toggleButton.getAttribute("aria-label");
    for (const [id, node] of this.optionButtons) node.setAttribute("aria-pressed", String(id === active));
  }

  onRemove() {}
}

// Attribution as plain text (never HTML from the manifest).
class AttributionControl {
  constructor(parts) { this.parts = parts; }

  onAdd() {
    this.node = el("div", "maplibregl-ctrl q2vt-attribution");
    this.render();
    return this.node;
  }

  render() {
    const text = this.parts.filter(Boolean).join(" · ");
    this.node.textContent = text;
    this.node.hidden = !text;
  }

  onRemove() {}
}

function themeBar(manifest, state) {
  const bar = document.getElementById("q2vt-themes");
  const list = presets(manifest);
  // One view only: nothing to choose, no bar (its layers are the start view).
  if (!bar || list.length < 2) return null;
  const label = el("span", "q2vt-themes-label");
  label.append(icon("sparkle", 16), el("span", "", t("app.themes")));
  bar.setAttribute("aria-label", t("app.themes"));
  bar.append(label);
  const chips = list.map((preset) => {
    const chip = button("q2vt-chip", null, null, { text: preset.title });
    chip.addEventListener("click", () => state.applyTheme(preset.id));
    bar.append(chip);
    return [preset, chip];
  });
  const sync = () => {
    for (const [preset, chip] of chips) chip.setAttribute("aria-pressed", String(presetMatches(manifest, state.value, preset)));
  };
  state.onChange(sync);
  sync();
  bar.hidden = false;
  return bar;
}

// Keep CSS informed of the floating header's height (phone layout).
function trackHeader() {
  const header = document.getElementById("q2vt-header");
  const themes = document.getElementById("q2vt-themes");
  if (!header || typeof ResizeObserver === "undefined") return;
  const update = () => {
    document.documentElement.style.setProperty("--q2vt-header-h", `${Math.round(header.getBoundingClientRect().height)}px`);
    const height = themes && !themes.hidden ? themes.getBoundingClientRect().height + 8 : 0;
    document.documentElement.style.setProperty("--q2vt-themes-h", `${Math.round(height)}px`);
  };
  new ResizeObserver(update).observe(header);
  update();
}

export async function mount({ map, manifest, manifestUrl, pageUrl, assetsUrl, maplibregl, viewer }) {
  const releaseBase = pageUrl;
  const state = new ViewerState(manifest);
  const initial = defaultState(manifest);
  state.load();
  const fromUrl = decodeState(manifest, location.hash);  // URL wins over saved state
  if (fromUrl) {
    state.merge({ ...fromUrl, filters: fromUrl.filters ? sanitizeFilters(manifest, fromUrl.filters) : undefined });
    if (fromUrl.camera) map.jumpTo({ center: [fromUrl.camera.lng, fromUrl.camera.lat], zoom: fromUrl.camera.zoom });
  }
  // Basemap first: it goes under every project layer.
  const basemap = new Basemap({ map, manifest, manifestUrl, maplibregl });
  viewer.basemap = basemap;
  if (basemap.available) {
    try {
      await basemap.init();
      await basemap.set(state.value.basemap);
    } catch (error) {
      warn("error.basemap", String(error && error.message || error));
    }
  }
  const control = new StyleControl(map, manifest);
  const apply = () => {
    control.apply(state.value);
    if (viewer.labels && viewer.labels.setEligibility) viewer.labels.setEligibility(control.eligibility(state.value));
  };
  state.onChange(apply);
  apply();

  const panel = buildPanel(manifest);
  const permalink = new Permalink({ map, manifest, state, defaults: initial,
    enabled: manifest.interaction?.permalinks !== false });
  viewer.permalink = permalink;
  const lookup = new FeatureLookup(manifest, manifestUrl);
  const identify = manifest.interaction?.popups === false ? null
    : new Identify({ map, manifest, maplibregl, control, state, lookup, viewer });
  const parts = { state, control, permalink, lookup, identify, panel, basemap };
  if (panel.panes.layers) {
    parts.layers = new LayerControls({ map, manifest, state, control, container: panel.panes.layers, releaseBase });
  }
  if (panel.panes.parcel) {
    const report = new ParcelReport({ map, manifest, manifestUrl, releaseBase, maplibregl,
      container: panel.panes.parcel, panel, permalink });
    parts.parcel = report;
    viewer.parcel = report;
    if (identify) {
      // A click opens the parcel (no chooser), and its report in the panel
      // instead of a popup.
      identify.prefer = report.layerId;
      identify.intercept = (layerId, key) => {
        if (layerId !== report.layerId) return false;
        report.show(key).catch((error) => warn("feature.notFound", String(error)));
        return true;
      };
      identify.onEmpty = () => report.clearMarkers();
    }
  }
  parts.legend = new Legend({ map, manifest, state, control, container: panel.panes.legend, releaseBase });
  // Print: the map with the legend of what it draws, or the parcel report.
  parts.printer = new Printer({ map, manifest, content: (mode) =>
    (mode === "parcel" && parts.parcel ? parts.parcel.printCard() : null) || parts.legend.printList() });
  viewer.printer = parts.printer;
  if (parts.parcel) parts.parcel.onPrint = () => parts.printer.print("parcel");
  if (panel.panes.filters) parts.filters = new Filters({ manifest, state, container: panel.panes.filters });
  if (panel.panes.tools) parts.tools = new Tools({ map, manifest, container: panel.panes.tools, viewer });
  if (panel.panes.share) shareBlock(panel.panes.share, permalink);
  if (manifest.search && manifest.interaction?.search !== false && identify) {
    parts.search = new Search({ map, manifest, manifestUrl, assetsUrl, container: document.getElementById("q2vt-searchbox"), identify });
  }
  parts.themes = themeBar(manifest, state);
  // Bottom right: basemap switcher above the scale and the attribution.
  const attribution = new AttributionControl([manifest.attribution,
    basemap.available && state.value.basemap !== "none" ? basemap.attribution(state.value.basemap) : null]);
  map.addControl(attribution, "bottom-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");
  if (basemap.available) {
    const switcher = new BasemapControl(basemap, state);
    map.addControl(switcher, "bottom-right");
    let shown = state.value.basemap;
    state.onChange((value) => {
      if (value.basemap === shown) return;
      shown = value.basemap;
      basemap.set(shown).then(() => {
        switcher.sync(shown);
        attribution.parts = [manifest.attribution, shown !== "none" ? basemap.attribution(shown) : null];
        attribution.render();
      }).catch((error) => warn("error.basemap", String(error)));
    });
  }
  const about = document.getElementById("q2vt-about-actions");
  if (about) themeToggle(about);
  trackHeader();
  // A link to a feature: exact lookup (works with search disabled, offscreen, cold).
  const selected = fromUrl && fromUrl.selected;
  if (selected && identify) {
    lookup.get(selected.layerId, selected.key).then(async (record) => {
      if (!record) { warn("feature.notFound", `${selected.layerId}/${selected.key}`); return; }
      if (!(fromUrl && fromUrl.camera)) await goTo(map, record);
      identify.open(selected.layerId, selected.key, record.p);
    }).catch((error) => warn("feature.notFound", String(error)));
  }
  viewer.state = state;
  return parts;
}
