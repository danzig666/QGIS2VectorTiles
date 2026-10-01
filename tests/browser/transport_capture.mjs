// Capture a published web map (the real viewer) at several views.
// Usage: node transport_capture.mjs <entry url> <views.json> <out dir> [width height]
// views.json: [{"id", "lon", "lat", "zoom"}]. Prints JSON per view:
// {id, labels: visible-polygon label points, errors, warnings}.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const [url, viewsPath, outDir, width = "800", height = "600"] = process.argv.slice(2);
const views = JSON.parse(readFileSync(viewsPath, "utf8"));
const executablePath = process.env.Q2VT_CHROMIUM || "/opt/pw-browsers/chromium";
const browser = await chromium.launch({ executablePath, args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"] });
const page = await browser.newPage({ viewport: { width: +width, height: +height }, deviceScaleFactor: 1 });
const pageErrors = [];
page.on("pageerror", (e) => pageErrors.push(String(e)));
await page.goto(url);
await page.waitForFunction(() => window.q2vtViewer && (window.q2vtViewer.ready || window.q2vtViewer.diagnostics.errors.length), null, { timeout: 60000 });
const results = [];
for (const view of views) {
  const info = await page.evaluate(async (v) => {
    const viewer = window.q2vtViewer;
    const map = viewer.map;
    if (!map) return { errors: viewer.diagnostics.errors };
    map.setMaxZoom(24);
    const idle = () => new Promise((resolve) => { map.once("idle", resolve); map.triggerRepaint(); });
    map.jumpTo({ center: [v.lon, v.lat], zoom: v.zoom });
    await idle();
    if (viewer.labels) viewer.labels.update();
    await new Promise((r) => setTimeout(r, 400));
    await idle();
    if (viewer.labels && viewer.labels.sync) viewer.labels.sync();
    await new Promise((r) => setTimeout(r, 400));
    await idle();
    let labels = 0;
    for (const features of (viewer.labels ? viewer.labels.snapshot().values() : [])) labels += features.length;
    return { labels, errors: viewer.diagnostics.errors, warnings: viewer.diagnostics.warnings.length,
             zoom: map.getZoom() };
  }, view);
  await page.screenshot({ path: join(outDir, `${view.id}.png`) });
  results.push({ id: view.id, ...info });
}
await browser.close();
process.stdout.write(JSON.stringify({ results, pageErrors }) + "\n");
