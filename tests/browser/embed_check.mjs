// Open a host page that shows a published web map in an <iframe> and report
// the framed viewer's state. Usage: node embed_check.mjs <host url> [width height]
// Prints JSON {ready, errors, embed, panelHidden, fullMap, cooperative, pageErrors}.
import { chromium } from "playwright-core";

const [url, width = "900", height = "700"] = process.argv.slice(2);
const executablePath = process.env.Q2VT_CHROMIUM || "/opt/pw-browsers/chromium";
const browser = await chromium.launch({ executablePath, args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"] });
const page = await browser.newPage({ viewport: { width: +width, height: +height }, deviceScaleFactor: 1 });
const pageErrors = [];
page.on("pageerror", (e) => pageErrors.push(String(e)));
await page.goto(url);
const frame = await (await page.waitForSelector("iframe")).contentFrame();
let ready = true;
try {
  await frame.waitForFunction(() => window.q2vtViewer && (window.q2vtViewer.ready || window.q2vtViewer.diagnostics.errors.length), null, { timeout: 60000 });
} catch (error) {
  ready = false;
}
const state = await frame.evaluate(() => {
  const v = window.q2vtViewer || {};
  return { ready: !!v.ready, errors: v.diagnostics ? v.diagnostics.errors : [],
    embed: document.body.classList.contains("q2vt-embed"),
    panelHidden: document.getElementById("q2vt-panel").hidden,
    fullMap: document.getElementById("q2vt-full-map")?.href || null,
    cooperative: v.map ? v.map.cooperativeGestures.isEnabled() : null,
    framed: window.top !== window };
});
// The host page scrolls with the wheel over the map; the map keeps its zoom.
const box = await (await page.$("iframe")).boundingBox();
const zoom0 = await frame.evaluate(() => window.q2vtViewer.map.getZoom());
await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
await page.mouse.wheel(0, -400);
await page.waitForTimeout(600);
state.zoomAfterWheel = (await frame.evaluate(() => window.q2vtViewer.map.getZoom())) - zoom0;
await browser.close();
process.stdout.write(JSON.stringify({ ...state, ready: ready && state.ready, pageErrors }) + "\n");
