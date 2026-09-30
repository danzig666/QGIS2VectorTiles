// Validate a style.json with the MapLibre style-spec validator pinned to the
// version used by the bundled MapLibre GL JS 5.11.0 (style-spec ^24.3.1).
// Usage: node validate_style.mjs path/to/style.json   -> prints JSON errors
import { readFileSync } from "node:fs";
import { validateStyleMin } from "@maplibre/maplibre-gl-style-spec";

const style = JSON.parse(readFileSync(process.argv[2], "utf8"));
const errors = validateStyleMin(style).map((e) => e.message);
process.stdout.write(JSON.stringify({ errors }) + "\n");
process.exit(errors.length ? 1 : 0);
