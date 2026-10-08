// Drive the published viewer with a list of actions and report results.
// Usage: node interact.mjs <url> <actions.json> [width height]
// actions: [{"eval": "<async JS expression>"} | {"click": [x, y]} |
//           {"clickLngLat": [lng, lat]} | {"type": ["#selector", "text"]} |
//           {"press": "Enter"} | {"wait": ms} | {"idle": true} |
//           {"screenshot": "path.png"} | {"goto": "url"} | {"media": "print" | "screen"} |
//           {"viewport": [width, height]}]
// Every "eval" result is collected in order. Prints JSON {results, pageErrors, console}.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const [url, actionsPath, width = "1000", height = "700"] = process.argv.slice(2);
const actions = JSON.parse(readFileSync(actionsPath, "utf8"));
const executablePath = process.env.Q2VT_CHROMIUM || "/opt/pw-browsers/chromium";
const browser = await chromium.launch({ executablePath, args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"] });
const context = await browser.newContext({ viewport: { width: +width, height: +height }, deviceScaleFactor: 1,
  reducedMotion: process.env.Q2VT_MOTION === "1" ? "no-preference" : "reduce" });
await context.grantPermissions(["clipboard-read", "clipboard-write"]).catch(() => {});
// Google's Street View coverage tiles (streetview tests stub the session
// only): answered with a transparent image, so the overlay does not fail
// whenever the blocked network gives up first.
const EMPTY_PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==", "base64");
await context.route("https://tile.googleapis.com/v1/2dtiles/**",
  (route) => route.fulfill({ status: 200, contentType: "image/png", body: EMPTY_PNG }));
const page = await context.newPage();
const pageErrors = [];
const consoleMessages = [];
page.on("pageerror", (e) => pageErrors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") consoleMessages.push(m.text()); });
page.on("dialog", (d) => d.dismiss());

async function waitReady() {
  await page.waitForFunction(() => window.q2vtViewer && (window.q2vtViewer.ready || window.q2vtViewer.diagnostics.errors.length), null, { timeout: 60000 });
  await idle();
}

async function idle() {
  await page.evaluate(() => new Promise((resolve) => {
    const map = window.q2vtViewer && window.q2vtViewer.map;
    if (!map) return resolve();
    const timer = setTimeout(resolve, 5000);
    map.once("idle", () => { clearTimeout(timer); setTimeout(resolve, 250); });
    map.triggerRepaint();
  }));
}

await page.goto(url);
await waitReady();
const results = [];
for (const action of actions) {
  if (action.eval) results.push(await page.evaluate(`(async () => { ${action.eval} })()`));
  else if (action.click) await page.mouse.click(action.click[0], action.click[1]);
  else if (action.clickLngLat) {
    const point = await page.evaluate((ll) => {
      const map = window.q2vtViewer.map;
      const p = map.project(ll);
      const rect = map.getCanvas().getBoundingClientRect();
      return [rect.left + p.x, rect.top + p.y];
    }, action.clickLngLat);
    await page.mouse.click(point[0], point[1]);
  } else if (action.type) await page.fill(action.type[0], action.type[1]);
  else if (action.press) await page.keyboard.press(action.press);
  else if (action.wait) await page.waitForTimeout(action.wait);
  else if (action.idle) await idle();
  else if (action.screenshot) await page.screenshot({ path: action.screenshot });
  else if (action.goto) { await page.goto(action.goto); await waitReady(); }
  else if (action.media) await page.emulateMedia({ media: action.media });
  else if (action.viewport) await page.setViewportSize({ width: action.viewport[0], height: action.viewport[1] });
}
await browser.close();
process.stdout.write(JSON.stringify({ results, pageErrors, console: consoleMessages.slice(0, 20) }) + "\n");
