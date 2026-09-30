// Open a static package page and report rendered features and errors.
// Usage: node static_check.mjs <url> <screenshot>
import { chromium } from "playwright-core";

const [url, shot] = process.argv.slice(2);
const executablePath = process.env.Q2VT_CHROMIUM || "/opt/pw-browsers/chromium";
const browser = await chromium.launch({ executablePath, args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"] });
const page = await browser.newPage({ viewport: { width: 400, height: 400 } });
const failed = [];
page.on("requestfailed", (r) => failed.push(r.url()));
page.on("response", (r) => { if (r.status() >= 400) failed.push(`${r.status()} ${r.url()}`); });
await page.goto(url);
await page.waitForFunction(() => window.map && window.map.loaded() && window.map.areTilesLoaded(), null, { timeout: 60000 });
await page.waitForTimeout(500);
const result = await page.evaluate(() => ({
  rendered: window.map.queryRenderedFeatures().length,
  errors: window.mapErrors || [],
}));
await page.screenshot({ path: shot });
await browser.close();
process.stdout.write(JSON.stringify({ ...result, failed }) + "\n");
