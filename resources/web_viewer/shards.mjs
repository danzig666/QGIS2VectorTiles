// One shard of an index (search, feature lookup, parcel report): its own
// JSON file, or a gzip member of the index's pack file (shard_pack.py) read
// with one HTTP range request, as the map's PMTiles tiles are. A server that
// ignores Range sends the whole pack: it is kept and sliced.

const wholePacks = new Map(); // pack URL -> Promise<Uint8Array>

// A shard's identity (the shards of a pack share its path).
export function shardId(shard) {
  return shard.offset === undefined || shard.offset === null ? shard.path : `${shard.path}#${shard.offset}`;
}

async function packBytes(url, shard) {
  const start = shard.offset;
  const end = shard.offset + shard.length - 1;
  if (!wholePacks.has(url.href)) {
    const response = await fetch(url, { headers: { Range: `bytes=${start}-${end}` } });
    if (response.status === 206) return new Uint8Array(await response.arrayBuffer());
    if (!response.ok) throw new Error(`${shard.path}: HTTP ${response.status}`);
    wholePacks.set(url.href, response.arrayBuffer().then((buffer) => new Uint8Array(buffer)));
  }
  return (await wholePacks.get(url.href)).subarray(start, end + 1);
}

async function gunzipText(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Response(stream).text();
}

export async function fetchShard(base, shard) {
  const url = new URL(shard.path, base);
  if (shard.offset === undefined || shard.offset === null) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${shard.path}: HTTP ${response.status}`);
    return response.json();
  }
  const bytes = await packBytes(url, shard);
  if (bytes.length !== shard.length) throw new Error(`${shard.path}: short read at ${shard.offset}`);
  const text = shard.encoding === "gzip" ? await gunzipText(bytes) : new TextDecoder().decode(bytes);
  return JSON.parse(text);
}
