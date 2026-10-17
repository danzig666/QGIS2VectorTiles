// Browser memory of a published web map: Chromium's processes (RSS) and the
// page's JS heap after loading the start view and after browsing (zooming in
// step by step, then panning at zooms 16-18).
// Usage: node measure.mjs <url> [width] [height]
// Prints JSON {loaded, browsed, layers, tiles}; MB per process type.
import { chromium } from "../../tests/browser/node_modules/playwright-core/index.mjs";
import { execSync } from "node:child_process";

const [url, width = "1600", height = "900"] = process.argv.slice(2);
const executablePath = process.env.Q2VT_CHROMIUM || "/opt/pw-browsers/chromium";
const browser = await chromium.launch({ executablePath, args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"] });
const page = await browser.newPage({ viewport: { width: Number(width), height: Number(height) } });
const cdp = await page.context().newCDPSession(page);
await cdp.send("Performance.enable");

function processes() {
  // Chromium runs as this script's child; its helpers below it.
  const rows = execSync("ps -eo pid=,ppid=,rss=,args=").toString().trim().split("\n")
    .map((line) => line.trim().split(/\s+/));
  const tree = new Set([String(process.pid)]);
  for (let grew = true; grew;) {
    grew = false;
    for (const [pid, ppid] of rows) if (tree.has(ppid) && !tree.has(pid)) { tree.add(pid); grew = true; }
  }
  const out = { total: 0 };
  for (const [pid, , rss, ...args] of rows) {
    if (pid === String(process.pid) || !tree.has(pid) || !/chrom/.test(args[0])) continue;
    const kind = (args.join(" ").match(/--type=([a-z-]+)/) || [, "browser"])[1];
    out[kind] = (out[kind] || 0) + Number(rss) / 1024;
    out.total += Number(rss) / 1024;
  }
  return Object.fromEntries(Object.entries(out).map(([k, v]) => [k, Math.round(v)]));
}

async function sample() {
  const metrics = (await cdp.send("Performance.getMetrics")).metrics;
  const heap = metrics.find((m) => m.name === "JSHeapUsedSize")?.value || 0;
  return { ...processes(), jsHeap: Math.round(heap / 1048576) };
}

await page.goto(url);
await page.waitForFunction(() => window.q2vtViewer?.ready, null, { timeout: 180000 });
await page.waitForTimeout(4000);
const start = await page.evaluate(() => {
  const m = q2vtViewer.map;
  return { center: m.getCenter(), zoom: m.getZoom(), maxZoom: m.getMaxZoom(), layers: m.getLayersOrder().length };
});
const loaded = await sample();
const c = start.center;
for (let z = Math.ceil(start.zoom); z <= Math.min(18, start.maxZoom); z++) {
  await page.evaluate(([lng, lat, z]) => q2vtViewer.map.jumpTo({ center: [lng, lat], zoom: z }), [c.lng, c.lat, z]);
  await page.waitForTimeout(1500);
}
for (let i = 0; i < 10; i++) {
  await page.evaluate(([lng, lat, z]) => q2vtViewer.map.jumpTo({ center: [lng, lat], zoom: z }),
    [c.lng + 0.006 * Math.cos(i), c.lat + 0.004 * Math.sin(i), 16 + (i % 3)]);
  await page.waitForTimeout(1500);
}
await page.waitForTimeout(3000);
const browsed = await sample();
const tiles = await page.evaluate(() => {
  const managers = q2vtViewer.map.style.tileManagers || {};
  let inView = 0, cached = 0;
  for (const m of Object.values(managers)) {
    inView += m._inViewTiles?.getAllIds ? m._inViewTiles.getAllIds().length : 0;
    cached += m._outOfViewCache?.order?.length || 0;
  }
  return { inView, cached };
});
await browser.close();
process.stdout.write(JSON.stringify({ start, loaded, browsed, tiles }) + "\n");
