// Headless render of an exported package with the bundled MapLibre build.
//
// Usage: node smoke.mjs <export_dir> <port> <lon> <lat> <zoom> <screenshot.png>
// Prints JSON: console errors, map errors, missing images, rendered layer ids.
// Transitions and fades are disabled so screenshots are reproducible.
import { chromium } from "playwright-core";
import { writeFileSync } from "node:fs";
import { join } from "node:path";

const [exportDir, port, lon, lat, zoom, shot] = process.argv.slice(2);
const executablePath = process.env.Q2VT_CHROMIUM || "/opt/pw-browsers/chromium";

const html = `<!doctype html><html><head><meta charset="utf-8">
<link href="maplibre-gl.css" rel="stylesheet">
<style>html,body,#map{margin:0;width:800px;height:600px}</style></head><body><div id="map"></div>
<script type="module">
import * as maplibregl from "./maplibre-gl.mjs";
window.maplibregl = maplibregl;
window.q2vt = { mapErrors: [], missingImages: [] };
const map = new maplibregl.Map({
  container: "map", style: "http://localhost:${port}/style/style.json",
  center: [${lon}, ${lat}], zoom: ${zoom}, fadeDuration: 0, attributionControl: false,
  pixelRatio: 1, canvasContextAttributes: { preserveDrawingBuffer: true },
  // The export's style points at the plugin's preview port (9000).
  transformRequest: (url) => ({ url: url.replace("://localhost:9000/", "://localhost:${port}/") }) });
map.on("error", (e) => window.q2vt.mapErrors.push(String(e.error && e.error.message || e)));
map.on("styleimagemissing", (e) => window.q2vt.missingImages.push(e.id));
map.once("idle", () => {
  const style = map.getStyle();
  const rendered = new Set(map.queryRenderedFeatures().map((f) => f.layer.id));
  window.q2vt.layers = style.layers.filter((l) => l.source === "q2vt_tiles").map((l) => l.id);
  window.q2vt.rendered = [...rendered];
  window.q2vt.done = true;
});
</script></body></html>`;
writeFileSync(join(exportDir, "utils", "viewer", "__smoke.html"), html);

const browser = await chromium.launch({ executablePath, args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"] });
const page = await browser.newPage({ viewport: { width: 800, height: 600 }, deviceScaleFactor: 1 });
const consoleErrors = [];
page.on("console", (msg) => { if (msg.type() === "error") consoleErrors.push(`${msg.text()} [${msg.location().url}]`); });
page.on("pageerror", (err) => consoleErrors.push(String(err)));
page.on("response", (res) => { if (res.status() >= 400) consoleErrors.push(`HTTP ${res.status()} ${res.url()}`); });
await page.goto(`http://localhost:${port}/viewer/__smoke.html`);
await page.waitForFunction(() => window.q2vt && window.q2vt.done, null, { timeout: 60000 });
const result = await page.evaluate(() => window.q2vt);
await page.screenshot({ path: shot });
await browser.close();
process.stdout.write(JSON.stringify({ consoleErrors, ...result }) + "\n");
