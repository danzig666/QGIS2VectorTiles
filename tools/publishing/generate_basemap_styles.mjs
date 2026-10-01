// Generate the vendored Protomaps basemap layer definitions
// (resources/basemaps/protomaps/<lang>-<flavor>.json) from the official
// @protomaps/basemaps package. Development tool; the plugin never runs npm.
//
//   mkdir /tmp/pm && cd /tmp/pm && npm pack @protomaps/basemaps@5.7.2 && tar xzf protomaps-basemaps-5.7.2.tgz
//   node tools/publishing/generate_basemap_styles.mjs /tmp/pm/package
//
// The layers are kept as generated (source "protomaps"); the plugin renames
// the source, replaces the Noto fonts by glyphs it generates and drops the
// sprite icons when it builds a release.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const pkg = process.argv[2];
if (!pkg) throw new Error("usage: node generate_basemap_styles.mjs <unpacked @protomaps/basemaps package>");
const version = JSON.parse(readFileSync(join(pkg, "package.json"), "utf8")).version;
const { layers, namedFlavor } = await import(pathToFileURL(join(pkg, "dist", "esm", "index.js")).href);
const out = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "resources", "basemaps", "protomaps");
mkdirSync(out, { recursive: true });
const flavors = ["light", "dark", "white", "grayscale", "black"];
for (const lang of ["hu", "en"]) {
  for (const name of flavors) {
    const flavor = namedFlavor(name);
    const doc = {
      generator: `@protomaps/basemaps ${version}`, flavor: name, lang,
      colors: { background: flavor.background, earth: flavor.earth, water: flavor.water,
        park: flavor.park_a, road: flavor.major, highway: flavor.highway, buildings: flavor.buildings,
        text: flavor.city_label },
      layers: layers("protomaps", flavor, { lang }),
    };
    writeFileSync(join(out, `${lang}-${name}.json`), JSON.stringify(doc) + "\n");
  }
}
console.log(`@protomaps/basemaps ${version}: ${flavors.length * 2} files in ${out}`);
