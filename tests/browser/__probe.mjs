import { chromium } from "playwright-core";
import { writeFileSync } from "node:fs";
const [dir, lon, lat, zoom, variant, shot] = process.argv.slice(2);
const html = `<!doctype html><html><head><meta charset="utf-8"><script src="maplibre-gl.js"></script>
<style>html,body{margin:0}#map{width:400px;height:400px}</style></head><body><div id="map"></div><script>
fetch('/style/style.json').then(r=>r.json()).then(style=>{
  style.layers = style.layers.filter(l => l.type==='background' || l.id.startsWith('l00t00d01r00g01c01o16'));
  const v = ${JSON.stringify(variant)};
  for (const l of style.layers) if (l.layout) {
    if (v==='const') { l.layout['symbol-spacing']=10; l.layout['icon-size']=0.0625; }
    if (v==='bigsprite') { l.layout['symbol-spacing']=10; }
  }
  window.map = new maplibregl.Map({container:'map', style, center:[${lon},${lat}], zoom:${zoom}, fadeDuration:0, pixelRatio:1});
  map.once('idle', ()=>{ window.count = map.queryRenderedFeatures().length; window.done=true; });
});
</script></body></html>`;
writeFileSync(dir + "/utils/viewer/__probe.html", html);
const b = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium", args: ["--use-gl=swiftshader","--enable-unsafe-swiftshader"] });
const p = await b.newPage({ viewport: { width: 400, height: 400 } });
await p.goto("http://localhost:9000/viewer/__probe.html");
await p.waitForFunction(() => window.done, null, { timeout: 30000 });
console.log(variant, await p.evaluate(() => window.count));
await p.screenshot({ path: shot }); await b.close();
