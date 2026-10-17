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
// What the tiles hold (steadier than process sizes, which the allocator
// and the garbage collector move by tens of MB): the GPU buffers of their
// buckets, their raw tile data and their feature indexes, in MB.
const tiles = await page.evaluate(() => {
  const managers = q2vtViewer.map.style.tileManagers || {};
  let inView = 0, cached = 0, buffers = 0, raw = 0, buckets = 0;
  const all = [];
  for (const m of Object.values(managers)) {
    const ids = m._inViewTiles?.getAllIds ? m._inViewTiles.getAllIds() : [];
    inView += ids.length;
    for (const id of ids) all.push(m._inViewTiles.getTileById(id));
    const cache = m._outOfViewCache;
    cached += cache?.order?.length || 0;
    for (const entries of Object.values(cache?.data || {})) for (const e of entries) all.push(e.value);
  }
  const seen = new Set();
  const bufferBytes = (value, depth) => {
    if (!value || typeof value !== "object" || seen.has(value) || depth > 3) return 0;
    seen.add(value);
    if (typeof WebGLBuffer !== "undefined" && value.buffer instanceof WebGLBuffer) {
      // The buffer's own size (measured last: rebinding does not matter).
      const gl = value.context?.gl;
      if (!gl) return 0;
      const target = value.itemSize !== undefined ? gl.ARRAY_BUFFER : gl.ELEMENT_ARRAY_BUFFER;
      if (target === gl.ELEMENT_ARRAY_BUFFER) gl.bindVertexArray?.(null);
      gl.bindBuffer(target, value.buffer);
      return gl.getBufferParameter(target, gl.BUFFER_SIZE) || 0;
    }
    let sum = 0;
    for (const v of Object.values(value)) sum += bufferBytes(v, depth + 1);
    return sum;
  };
  for (const tile of all) {
    if (!tile) continue;
    raw += tile.latestRawTileData?.byteLength || 0;
    for (const bucket of Object.values(tile.buckets || {})) { buckets++; buffers += bufferBytes(bucket, 0); }
  }
  const mb = (n) => Math.round(n / 104857.6) / 10;
  return { inView, cached, buckets, bufferMB: mb(buffers), rawMB: mb(raw) };
});
await browser.close();
process.stdout.write(JSON.stringify({ start, loaded, browsed, tiles }) + "\n");
