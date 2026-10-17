// Search box: publication-wide search in a Web Worker (lazy: the index is
// fetched on first use, never before the map). Results show the matched
// layer; arrows/Enter/Escape work from the keyboard. Choosing a result
// moves to its bounds/anchor, marks it, selects the feature and opens its
// popup (offering to switch a hidden layer on; filters are not overridden).
import { t, formatNumber } from "./i18n.mjs";
import { button, el, icon } from "./icons.mjs";

const MARKER = "q2vt_search_marker";
const STREETS = "q2vt-streets";  // OpenStreetMap street names (no map layer behind them)
const ADDRESSES = "q2vt-addresses";  // house numbers "street number" (no map layer behind them)

export class Search {
  constructor({ map, manifest, manifestUrl, assetsUrl, container, identify }) {
    Object.assign(this, { map, manifest, identify });
    this.layers = new Map(manifest.layers.map((l) => [l.id, l]));
    this.manifestUrl = new URL(manifest.search.manifest, manifestUrl).href;
    this.workerUrl = new URL("search_worker.mjs", assetsUrl);
    this.pending = new Map();
    this.seq = 0;
    this.items = [];
    this.active = -1;
    this.input = el("input");
    this.input.id = "q2vt-search";
    this.input.type = "search";
    this.input.placeholder = t("search.placeholder");
    this.input.autocomplete = "off";
    this.input.setAttribute("role", "combobox");
    this.input.setAttribute("aria-autocomplete", "list");
    this.input.setAttribute("aria-controls", "q2vt-results");
    this.input.setAttribute("aria-expanded", "false");
    this.input.setAttribute("aria-label", t("app.search"));
    this.list = el("ul");
    this.list.id = "q2vt-results";
    this.list.setAttribute("role", "listbox");
    this.list.hidden = true;
    const field = el("div", "q2vt-search-field");
    const clear = button("q2vt-icon-btn q2vt-search-clear", t("app.close"), "close");
    clear.hidden = true;
    clear.addEventListener("click", () => { this.input.value = ""; clear.hidden = true; this.close(); this.marker(null); this.input.focus(); });
    this.input.addEventListener("input", () => { clear.hidden = !this.input.value; });
    field.append(icon("search", 18), this.input, clear);
    container.append(field, this.list);
    this.collapsible(container, clear);
    this.input.addEventListener("focus", () => this.start(), { once: true });
    this.input.addEventListener("input", () => this.debounce());
    this.input.addEventListener("keydown", (e) => this.key(e));
    document.addEventListener("click", (e) => { if (!container.contains(e.target)) this.close(); });
  }

  // Only a magnifier in the header row (styles.css); the field opens when
  // it is clicked and closes again when left empty, with Escape, or once a
  // result is chosen (the magnifier then shows that a search is set).
  collapsible(container, clear) {
    const header = container.closest("#q2vt-header");
    const row = header && header.querySelector(".q2vt-header-row");
    if (!row) return;
    const toggle = button("q2vt-icon-btn q2vt-search-toggle", t("app.search"), "search");
    toggle.setAttribute("aria-controls", "q2vt-searchbox");
    toggle.setAttribute("aria-expanded", "false");
    row.append(toggle);
    this.setSearchOpen = (open) => {
      header.classList.toggle("q2vt-search-open", open);
      toggle.setAttribute("aria-expanded", String(open));
      toggle.classList.toggle("q2vt-has-query", !open && Boolean(this.input.value));
    };
    toggle.addEventListener("click", () => {
      const open = !header.classList.contains("q2vt-search-open");
      this.setSearchOpen(open);
      if (open) this.input.focus();  // in the tap: phones show the keyboard
      else this.close();
    });
    this.input.addEventListener("blur", () => setTimeout(() => {
      if (!this.input.value && this.list.hidden && !container.contains(document.activeElement)) this.setSearchOpen(false);
    }, 200));
    clear.addEventListener("click", () => toggle.classList.remove("q2vt-has-query"));
  }

  start() {
    if (this.worker) return this.ready;
    this.worker = new Worker(this.workerUrl, { type: "module" });
    this.ready = new Promise((resolve, reject) => {
      this.worker.onmessage = (event) => {
        const msg = event.data;
        if (msg.type === "ready") { this.coverage = msg.records; resolve(msg); return; }
        if (msg.type === "error" && msg.id === undefined) { reject(new Error(msg.message)); return; }
        const callback = this.pending.get(msg.id);
        if (callback) { this.pending.delete(msg.id); callback(msg); }
      };
      this.worker.onerror = (error) => reject(error);
    });
    this.worker.postMessage({ type: "init", manifestUrl: this.manifestUrl });
    return this.ready;
  }

  debounce() {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.query(this.input.value), 180);
  }

  async query(text) {
    if (!text.trim()) { this.close(); return; }
    const id = ++this.seq;
    try {
      await this.start();
    } catch {
      this.show([], 0, t("search.unavailable"));
      return;
    }
    this.show([], 0, t("search.loading"));
    const msg = await new Promise((resolve) => {
      this.pending.set(id, resolve);
      this.worker.postMessage({ type: "query", id, query: text, limit: 30 });
    });
    if (id !== this.seq) return; // a newer query is running (cancelled)
    if (msg.type === "error") { this.show([], 0, t("search.unavailable")); return; }
    if (msg.needMore) { this.show([], 0, t("search.typeMore")); return; }
    this.show(msg.results, msg.total);
  }

  show(results, total, note) {
    this.items = results;
    this.active = -1;
    this.list.replaceChildren();
    results.forEach((result, index) => {
      const item = el("li");
      item.id = `q2vt-result-${index}`;
      item.setAttribute("role", "option");
      const layer = this.layers.get(result.layerId);
      const street = result.layerId === STREETS;
      const address = result.layerId === ADDRESSES;
      item.append(icon(street ? "road" : "pin", 18));
      const text = el("span", "q2vt-result-text");
      text.append(el("span", "q2vt-result-label", result.label || result.key),
        el("span", "q2vt-layer-name", street ? t("search.street") : address ? t("search.address")
          : (layer ? layer.title : "")));
      item.append(text);
      item.addEventListener("click", () => this.choose(index));
      this.list.append(item);
    });
    if (note || !results.length) this.list.append(el("li", "q2vt-muted", note || t("search.noResults")));
    else if (total > results.length) this.list.append(el("li", "q2vt-muted", t("search.more", { n: formatNumber(total - results.length) })));
    this.list.hidden = false;
    this.input.setAttribute("aria-expanded", "true");
  }

  close() {
    this.seq += 1;  // a query still running must not reopen the list
    this.list.hidden = true;
    this.input.setAttribute("aria-expanded", "false");
    this.input.removeAttribute("aria-activedescendant");
  }

  key(event) {
    if (event.key === "Escape") {
      if (this.list.hidden && this.setSearchOpen) { this.setSearchOpen(false); this.input.blur(); }
      this.close();
      return;
    }
    if (!this.items.length) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      this.active = (this.active + step + this.items.length) % this.items.length;
      for (const [index, node] of [...this.list.children].entries()) node.setAttribute("aria-selected", String(index === this.active));
      this.input.setAttribute("aria-activedescendant", `q2vt-result-${this.active}`);
    } else if (event.key === "Enter") {
      event.preventDefault();
      this.choose(this.active >= 0 ? this.active : 0);
    }
  }

  marker(anchor) {
    const data = { type: "FeatureCollection", features: anchor ? [{ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: anchor } }] : [] };
    if (!this.map.getSource(MARKER)) {
      this.map.addSource(MARKER, { type: "geojson", data });
      this.map.addLayer({ id: MARKER, type: "circle", source: MARKER, paint: {
        "circle-radius": 7, "circle-color": getComputedStyle(document.documentElement).getPropertyValue("--q2vt-accent").trim() || "#2563eb",
        "circle-stroke-color": "#ffffff", "circle-stroke-width": 3 } });
    } else {
      this.map.getSource(MARKER).setData(data);
    }
  }

  async choose(index) {
    const result = this.items[index];
    if (!result) return;
    this.close();
    this.input.value = result.label || result.key;
    if (this.setSearchOpen) {  // phones: the map gets the room back (the keyboard too)
      if (matchMedia("(max-width: 760px)").matches) this.input.blur();
      this.setSearchOpen(false);
    }
    await goTo(this.map, result);
    this.marker(result.anchor);
    if (this.identify && result.layerId !== STREETS && result.layerId !== ADDRESSES) {
      this.identify.open(result.layerId, result.key, result.anchor);
    }
  }
}

// Fit a record's bounds (or centre its anchor), then wait for the tiles.
export function goTo(map, record) {
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const maxZoom = Math.min(record.zoom ?? record.z ?? 18, map.getMaxZoom());
  const bounds = record.bounds || record.b;
  const anchor = record.anchor || record.p;
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, 10000);
    // The movement starts on the next frame: wait for its end, then tiles.
    // Animated markers repaint every frame, so "idle" never comes while they
    // play (it fell back after 10 s): a frame drawn with every tile of the
    // new view loaded will do (a frame updates the tiles before "render").
    map.once("moveend", () => {
      const done = () => { clearTimeout(timer); map.off("idle", done); map.off("render", drawn); resolve(); };
      const drawn = () => { if (map.areTilesLoaded()) done(); };
      map.once("idle", done);
      map.on("render", drawn);
      map.triggerRepaint();
    });
    if (bounds && bounds[0] !== bounds[2]) {
      map.fitBounds([[bounds[0], bounds[1]], [bounds[2], bounds[3]]],
        { padding: 60, maxZoom, animate: !reduce, maxDuration: 2500 });
    } else if (anchor) {
      map.jumpTo({ center: anchor, zoom: Math.max(map.getZoom(), maxZoom) });
    }
    map.triggerRepaint();
  });
}
