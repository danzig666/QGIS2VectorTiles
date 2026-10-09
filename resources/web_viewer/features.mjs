// Exact feature lookup (deep links, popups): loads only the shard that
// holds (layerId, key) from features/ — never the whole dataset, never
// geometry. Independent of the search UI.
import { featureShardKey } from "./search_core.mjs";
import { fetchShard, shardId } from "./shards.mjs";

export class FeatureLookup {
  constructor(manifest, manifestUrl) {
    this.info = manifest.featureLookup;
    this.base = this.info ? new URL(this.info.manifest, manifestUrl) : null;
    this.index = null;
    this.shards = new Map();
  }

  get available() { return !!this.info; }

  async ready() {
    if (!this.base) return null;
    if (!this.index) {
      this.index = fetch(this.base).then((r) => {
        if (!r.ok) throw new Error(`features manifest: HTTP ${r.status}`);
        return r.json();
      });
    }
    return this.index;
  }

  async get(layerId, key) {
    const index = await this.ready();
    if (!index || key === null || key === undefined) return null;
    if (!index.coverage || !(layerId in index.coverage)) return null;
    const shardKey = featureShardKey(layerId, String(key), index.prefixLength);
    const shard = index.shards.find((s) => s.key === shardKey);
    if (!shard) return null;
    const id = shardId(shard);
    if (!this.shards.has(id)) this.shards.set(id, fetchShard(this.base, shard));
    const records = await this.shards.get(id);
    return records.find((r) => r.l === layerId && r.k === String(key)) || null;
  }
}
