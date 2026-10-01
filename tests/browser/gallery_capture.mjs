// Capture browser renders of an exported package for a list of views.
// Usage: node capture.mjs <export_dir> <port> <views.json> <out_dir>
// views.json: [{"id": "...", "lon": .., "lat": .., "zoom": .., "width": .., "height": .., "only": [style layer ids, optional]}]
import { chromium } from "playwright-core";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const [exportDir, port, viewsPath, outDir] = process.argv.slice(2);
const views = JSON.parse(readFileSync(viewsPath, "utf8"));
const executablePath = process.env.Q2VT_CHROMIUM || "/opt/pw-browsers/chromium";
const w = views[0].width, h = views[0].height;
const html = `<!doctype html><html><head><meta charset="utf-8">
<link href="maplibre-gl.css" rel="stylesheet">
<style>html,body{margin:0}#map{width:${w}px;height:${h}px}</style></head><body><div id="map"></div>
<script type="module">
import * as maplibregl from "./maplibre-gl.mjs";
import { enableVisibleLabels } from "./visible_labels.mjs";
window.maplibregl = maplibregl;
window.q2vt = { errors: [], missing: [] };
window.map = new maplibregl.Map({ container: "map", style: "http://localhost:${port}/style/style.json",
  center: [0, 0], zoom: 1, fadeDuration: 0, attributionControl: false, pixelRatio: 1,
  canvasContextAttributes: { preserveDrawingBuffer: true } });
map.on("error", (e) => window.q2vt.errors.push(String(e.error && e.error.message || e)));
map.on("styleimagemissing", (e) => window.q2vt.missing.push(e.id));
map.once("load", () => { window.q2vtVisibleLabels = enableVisibleLabels(map, maplibregl); });
window.q2vtGo = (v) => new Promise((resolve) => {
  // Never wait forever: a view whose tiles or images never settle is
  // captured as is after 20 s and reported.
  const timer = setTimeout(() => { window.q2vt.timeouts = (window.q2vt.timeouts || []).concat([v.id]); resolve(false); }, 20000);
  map.once("idle", () => {
    if (!window.q2vtVisibleLabels) { clearTimeout(timer); resolve(true); return; }
    // Visible-polygon labels: placed for this view, then drawn.
    window.q2vtVisibleLabels.update();
    // setData is processed by a worker: let it start before waiting for idle.
    setTimeout(() => {
      map.once("idle", () => {
        // Overlap fallback: the labels that could not be placed, drawn.
        window.q2vtVisibleLabels.sync();
        map.once("idle", () => { clearTimeout(timer); resolve(true); });
        map.triggerRepaint();
      });
      map.triggerRepaint();
    }, 400);
  });
  if (v.only) {  // project_compare.py: show one QGIS layer's style layers
    const keep = new Set(v.only);
    for (const l of map.getStyle().layers) {
      if (l.type === "background" || l.id.startsWith("q2vt_visible_loader_")) continue;
      const own = keep.has(l.id) || keep.has(l.id.replace(/_q2vt_overlap$/, ""));
      map.setLayoutProperty(l.id, "visibility", own ? "visible" : "none");
    }
  }
  map.jumpTo({ center: [v.lon, v.lat], zoom: v.zoom });
  map.triggerRepaint();
});
</script></body></html>`;
writeFileSync(join(exportDir, "utils", "viewer", "__gallery.html"), html);
const browser = await chromium.launch({ executablePath, args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"] });
const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
await page.goto(`http://localhost:${port}/viewer/__gallery.html`);
await page.waitForFunction(() => window.map && window.map.isStyleLoaded() && "q2vtVisibleLabels" in window, null, { timeout: 60000 });
for (const v of views) {
  await page.evaluate((view) => window.q2vtGo(view), v);
  await page.waitForTimeout(50);
  await page.screenshot({ path: join(outDir, `${v.id}_browser.png`) });
}
const result = await page.evaluate(() => window.q2vt);
await browser.close();
process.stdout.write(JSON.stringify(result) + "\n");
