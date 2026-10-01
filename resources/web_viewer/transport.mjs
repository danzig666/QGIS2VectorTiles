// Transport binding for published QGIS2VectorTiles maps.
//
// The manifest is the authority: the style file on disk carries the vector
// source without tile URLs; here each manifest source is bound to an
// absolute URL (PMTiles archive through the official pmtiles protocol, or
// an XYZ MVT template), stale tiles/url entries are removed, and relative
// sprite/glyph URLs are resolved against the style URL without losing
// {placeholders}. Only vector sources are accepted.

export const MVT_TILE_TYPE = 1; // pmtiles TileType.Mvt
export const RASTER_TILE_TYPES = ["png", "jpeg", "webp"];
const IMAGE_TILE_TYPE = { png: 2, jpeg: 3, webp: 4 }; // pmtiles TileType

const NON_VECTOR = new Set(["raster", "raster-dem", "image", "video", "canvas"]);

export class ViewerError extends Error {
  constructor(code, message, detail = "") {
    super(message);
    this.code = code;
    this.detail = detail;
  }
}

export function resolveUrl(relative, base) {
  return new URL(relative, base).href;
}

// Resolve a URL template ("glyphs/{fontstack}/{range}.pbf",
// "data/tiles/{z}/{x}/{y}.pbf") against base, keeping the placeholders
// literal (new URL() would percent-encode the braces).
export function resolveTemplate(template, base) {
  const brace = template.indexOf("{");
  if (brace < 0) return resolveUrl(template, base);
  const slash = template.lastIndexOf("/", brace);
  const head = slash < 0 ? "./" : template.slice(0, slash + 1);
  return resolveUrl(head, base) + template.slice(slash + 1);
}

export function checkRelative(path, what) {
  if (typeof path !== "string" || !path || /^[a-z][a-z0-9+.-]*:/i.test(path) || path.startsWith("/")
      || path.split("/").includes("..") || path.includes("\\")) {
    throw new ViewerError("Q2VT_PUB_PATH_UNSAFE", `${what}: unsafe path ${String(path).slice(0, 80)}`);
  }
  return path;
}

let protocol = null;

// Register the pmtiles:// protocol once (before the map is created).
export function registerPmtiles(maplibregl) {
  if (protocol) return protocol;
  const pm = globalThis.pmtiles;
  if (!pm || !pm.Protocol) {
    throw new ViewerError("Q2VT_PUB_DEPENDENCY", "The PMTiles reader (assets/vendor/pmtiles.js) did not load.");
  }
  protocol = new pm.Protocol({ metadata: true });
  maplibregl.addProtocol("pmtiles", protocol.tile);
  return protocol;
}

// Open each PMTiles archive once, share it with the protocol and check its
// header: MVT tiles, readable over HTTP ranges.
export async function openArchives(manifest, manifestUrl, maplibregl) {
  const archives = {};
  for (const source of manifest.sources) {
    if (source.kind !== "pmtiles") continue;
    const proto = registerPmtiles(maplibregl);
    const url = resolveUrl(checkRelative(source.href, "source"), manifestUrl);
    const archive = new globalThis.pmtiles.PMTiles(url);
    let header;
    try {
      header = await archive.getHeader();
    } catch (error) {
      const text = String(error && error.message || error);
      if (/byte serving|content-length|range/i.test(text)) {
        throw new ViewerError("Q2VT_PUB_RANGE_UNSUPPORTED", "The web server does not support HTTP range requests needed for the map data.", text);
      }
      if (/magic|version|invalid/i.test(text)) {
        throw new ViewerError("Q2VT_PUB_PMTILES_INVALID", "The map data archive is damaged or not a PMTiles v3 file.", text);
      }
      throw new ViewerError("Q2VT_PUB_FETCH", "The map data could not be loaded (network, CORS or missing file).", text);
    }
    const expected = source.role === "raster" ? IMAGE_TILE_TYPE[source.tileType] : MVT_TILE_TYPE;
    if (header.tileType !== expected) {
      throw new ViewerError("Q2VT_PUB_NOT_MVT", source.role === "raster"
        ? `The raster layer archive ${source.id} does not hold ${source.tileType} images.`
        : "The map data archive does not contain vector tiles.", `tileType ${header.tileType}`);
    }
    proto.add(archive);
    archives[source.id] = { archive, header, url };
  }
  return archives;
}

// Bind the manifest's sources into the style object (in place) and resolve
// sprite/glyph URLs. Returns the style.
export function bindStyle(style, manifest, manifestUrl, styleUrl) {
  if (!style || style.version !== 8 || typeof style.sources !== "object") {
    throw new ViewerError("Q2VT_PUB_STYLE", "The map style is not a valid MapLibre style.");
  }
  // Raster sources only for the QGIS raster layers' own image archives.
  const rasterIds = new Set(manifest.sources.filter((s) => s.role === "raster").map((s) => s.id));
  for (const [id, source] of Object.entries(style.sources)) {
    if (source.type === "raster" && rasterIds.has(id)) continue;
    if (NON_VECTOR.has(source.type) || source.type === "geojson") {
      throw new ViewerError("Q2VT_PUB_RASTER_SOURCE", `Source "${id}" is not a vector tile source; this viewer only draws vector tiles.`);
    }
  }
  for (const binding of manifest.sources) {
    const source = style.sources[binding.id];
    const type = binding.role === "raster" ? "raster" : "vector";
    if (!source || source.type !== type) {
      throw new ViewerError("Q2VT_PUB_STYLE", `The style has no ${type} source "${binding.id}".`);
    }
    delete source.tiles;
    delete source.url;
    const href = checkRelative(binding.href, "source");
    if (binding.kind === "pmtiles") {
      source.url = "pmtiles://" + resolveUrl(href, manifestUrl);
    } else if (binding.kind === "xyz") {
      source.tiles = [resolveTemplate(href, manifestUrl)];
    } else {
      throw new ViewerError("Q2VT_PUB_STYLE", `Unknown source kind "${binding.kind}".`);
    }
    source.minzoom = binding.minTileZoom;
    source.maxzoom = binding.maxTileZoom;
    if (binding.bounds) source.bounds = binding.bounds;
  }
  if (typeof style.sprite === "string" && !/^[a-z]+:/i.test(style.sprite)) {
    style.sprite = resolveUrl(checkRelative(style.sprite, "sprite"), styleUrl);
  } else if (Array.isArray(style.sprite)) {
    style.sprite = style.sprite.map((s) => ({ ...s, url: /^[a-z]+:/i.test(s.url) ? s.url : resolveUrl(checkRelative(s.url, "sprite"), styleUrl) }));
  }
  if (typeof style.glyphs === "string" && !/^[a-z]+:/i.test(style.glyphs)) {
    style.glyphs = resolveTemplate(checkRelative(style.glyphs, "glyphs"), styleUrl);
  }
  return style;
}
