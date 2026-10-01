// Mounts the interactive controls of a published map (called by app.mjs
// once the map and the visible-polygon label helper are ready).
import { t } from "./i18n.mjs";
import { ViewerState, defaults as defaultState, sanitizeFilters } from "./state.mjs";
import { StyleControl } from "./style_control.mjs";
import { LayerControls } from "./layer_controls.mjs";
import { Legend } from "./legend.mjs";
import { Filters } from "./filters.mjs";
import { Identify } from "./identify.mjs";
import { FeatureLookup } from "./features.mjs";
import { Search, goTo } from "./search.mjs";
import { Permalink, decodeState } from "./permalink.mjs";
import { Tools } from "./tools.mjs";
import { warn } from "./diagnostics.mjs";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function panes(manifest) {
  const list = [["layers", "app.layers"], ["legend", "app.legend"]];
  if (manifest.interaction?.filters !== false && manifest.layers.some((l) => (l.filterFields || []).length)) list.push(["filters", "app.filters"]);
  const tools = manifest.tools || {};
  if (tools.coordinates || tools.measure || tools.print) list.push(["tools", "app.tools"]);
  if (manifest.interaction?.permalinks !== false) list.push(["share", "app.share"]);
  return list;
}

function buildPanel(manifest) {
  const tabs = document.getElementById("q2vt-tabs");
  const container = document.getElementById("q2vt-panes");
  const result = {};
  const buttons = [];
  for (const [id, label] of panes(manifest)) {
    const button = el("button", "", t(label));
    button.type = "button";
    button.id = `q2vt-tab-${id}`;
    button.setAttribute("role", "tab");
    button.setAttribute("aria-controls", `q2vt-pane-${id}`);
    const pane = el("div", "q2vt-pane");
    pane.id = `q2vt-pane-${id}`;
    pane.dataset.pane = id;
    pane.setAttribute("role", "tabpanel");
    pane.setAttribute("aria-labelledby", button.id);
    button.addEventListener("click", () => select(id));
    tabs.append(button);
    container.append(pane);
    buttons.push([id, button, pane]);
    result[id] = pane;
  }
  function select(id) {
    for (const [paneId, button, pane] of buttons) {
      button.setAttribute("aria-selected", String(paneId === id));
      pane.hidden = paneId !== id;
    }
  }
  select("layers");
  const panel = document.getElementById("q2vt-panel");
  const menu = document.getElementById("q2vt-menu");
  const setOpen = (open) => {
    panel.hidden = !open;
    menu.setAttribute("aria-expanded", String(open));
    document.body.classList.toggle("q2vt-panel-open", open);
  };
  menu.setAttribute("aria-label", t("app.menu"));
  menu.addEventListener("click", () => { setOpen(panel.hidden); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !panel.hidden && window.innerWidth <= 700) { setOpen(false); menu.focus(); }
  });
  setOpen(window.innerWidth > 700);
  return { panes: result, select, setOpen };
}

function shareBlock(container, permalink) {
  const status = el("p", "q2vt-muted");
  status.setAttribute("aria-live", "polite");
  for (const [which, label] of [["stable", "app.linkCurrent"], ["versioned", "app.linkRelease"]]) {
    const button = el("button", "", t(label));
    button.type = "button";
    button.addEventListener("click", async () => {
      await permalink.copy({}, which);
      status.textContent = t("app.copied");
    });
    container.append(button);
  }
  container.append(status);
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
  const parts = { state, control, permalink, lookup, identify, panel };
  parts.layers = new LayerControls({ map, manifest, state, control, container: panel.panes.layers, releaseBase });
  parts.legend = new Legend({ map, manifest, state, control, container: panel.panes.legend, releaseBase });
  if (panel.panes.filters) parts.filters = new Filters({ manifest, state, container: panel.panes.filters });
  if (panel.panes.tools) parts.tools = new Tools({ map, manifest, container: panel.panes.tools, viewer });
  if (panel.panes.share) shareBlock(panel.panes.share, permalink);
  if (manifest.search && manifest.interaction?.search !== false && identify) {
    parts.search = new Search({ map, manifest, manifestUrl, assetsUrl, container: document.getElementById("q2vt-searchbox"), identify });
  }
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
