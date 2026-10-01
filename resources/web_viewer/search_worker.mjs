// Search Web Worker: loads the publication-wide search index lazily (the
// map never waits for it) and answers queries off the UI thread.
import { rank, shardsForQuery } from "./search_core.mjs";

let manifest = null;
let base = null;
const shards = new Map(); // path -> Promise<entries>

async function loadShard(shard) {
  if (!shards.has(shard.path)) {
    shards.set(shard.path, fetch(new URL(shard.path, base)).then((r) => {
      if (!r.ok) throw new Error(`${shard.path}: HTTP ${r.status}`);
      return r.json();
    }).then((data) => data.entries));
  }
  return shards.get(shard.path);
}

self.onmessage = async (event) => {
  const message = event.data;
  try {
    if (message.type === "init") {
      base = new URL(message.manifestUrl);
      const response = await fetch(base);
      if (!response.ok) throw new Error(`search manifest: HTTP ${response.status}`);
      manifest = await response.json();
      if (manifest.schemaVersion !== 1 || manifest.kind !== "search") throw new Error("bad search manifest");
      if (manifest.mode === "single") loadShard(manifest.shards[0]);  // warm up
      self.postMessage({ type: "ready", records: manifest.records, layers: manifest.layers, mode: manifest.mode });
      return;
    }
    if (message.type === "query") {
      if (!manifest) throw new Error("search not ready");
      const wanted = shardsForQuery(manifest, message.query);
      if (wanted === null) {
        self.postMessage({ type: "results", id: message.id, results: [], total: 0, needMore: true });
        return;
      }
      const lists = await Promise.all(wanted.map(loadShard));
      const { results, total } = rank(lists.flat(), message.query, {
        allowSubstring: manifest.mode === "single", limit: message.limit || 50 });
      self.postMessage({
        type: "results", id: message.id, total,
        results: results.map(({ entry, score }) => ({
          layerId: manifest.layers[entry[0]], key: entry[1], label: entry[2], terms: entry[3],
          anchor: entry[4], bounds: entry[5], zoom: entry[6], score })),
      });
    }
  } catch (error) {
    self.postMessage({ type: "error", id: message.id, message: String(error && error.message || error) });
  }
};
