// Search Web Worker: loads the publication-wide search index lazily (the
// map never waits for it) and answers queries off the UI thread.
import { rank, shardsForQuery } from "./search_core.mjs";
import { fetchShard, shardId } from "./shards.mjs";

let manifest = null;
let base = null;
const shards = new Map(); // shard id -> Promise<entries>

async function loadShard(shard) {
  const id = shardId(shard);
  if (!shards.has(id)) shards.set(id, fetchShard(base, shard).then((data) => data.entries));
  return shards.get(id);
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
