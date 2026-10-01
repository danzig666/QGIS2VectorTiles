// Open a published web map and report its state.
// Usage: node open_viewer.mjs <url> <screenshot.png> [width] [height] [eval-json]
// Prints JSON: {ready, errors, warnings, console, csp, center, zoom, ...}.
import { chromium } from "playwright-core";

const [url, shot, width = "1000", height = "700", script = ""] = process.argv.slice(2);
const executablePath = process.env.Q2VT_CHROMIUM || "/opt/pw-browsers/chromium";
const browser = await chromium.launch({ executablePath, args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"] });
const page = await browser.newPage({ viewport: { width: +width, height: +height }, deviceScaleFactor: 1 });
const consoleMessages = [];
const pageErrors = [];
const external = [];
page.on("console", (m) => { if (["error", "warning"].includes(m.type())) consoleMessages.push(`${m.type()}: ${m.text()}`); });
page.on("pageerror", (e) => pageErrors.push(String(e)));
page.on("request", (r) => { if (!r.url().startsWith(new URL(url).origin)) external.push(r.url()); });
await page.goto(url);
let ready = true;
try {
  await page.waitForFunction(() => window.q2vtViewer && (window.q2vtViewer.ready || window.q2vtViewer.diagnostics.errors.length), null, { timeout: 60000 });
  await page.evaluate(() => new Promise((resolve) => {
    const map = window.q2vtViewer.map;
    if (!map) return resolve();
    const done = () => setTimeout(resolve, 300);
    if (map.loaded() && !map.isMoving()) map.once("idle", done); else map.once("idle", done);
    map.triggerRepaint();
  }));
} catch (error) {
  ready = false;
}
let extra = null;
if (script) extra = await page.evaluate(script).catch((e) => `eval error: ${e}`);
const state = await page.evaluate(() => {
  const v = window.q2vtViewer || {};
  const map = v.map;
  return {
    url: location.href, ready: !!v.ready, errors: v.diagnostics ? v.diagnostics.errors : [],
    warnings: v.diagnostics ? v.diagnostics.warnings : [], maplibre: v.maplibreVersion,
    center: map ? map.getCenter().toArray() : null, zoom: map ? map.getZoom() : null,
    title: document.title, styleLayers: map ? map.getStyle().layers.length : 0,
  };
}).catch((e) => ({ evalError: String(e) }));
if (shot) await page.screenshot({ path: shot });
await browser.close();
process.stdout.write(JSON.stringify({ ...state, readyWait: ready, console: consoleMessages.slice(0, 20), pageErrors, external, extra }) + "\n");
