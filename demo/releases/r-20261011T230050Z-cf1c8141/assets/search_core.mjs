// Search/lookup core shared by the page and the search worker (pure, no DOM).
// Twins of publishing/search_index.py (normalize, words) and
// publishing/feature_index.py (fnv1a); tested with the same vectors.

export function normalize(text) {
  return String(text ?? "")
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .toLowerCase()
    .replace(/\s+/gu, " ")
    .trim();
}

const WORD_SPLIT = /[\s,;:()"'[\]]+/u;

export function words(text) {
  return normalize(text).split(WORD_SPLIT).filter(Boolean);
}

export function fnv1a(text) {
  let value = 0x811c9dc5;
  for (const byte of new TextEncoder().encode(text)) {
    value ^= byte;
    value = Math.imul(value, 0x01000193) >>> 0;
  }
  return value.toString(16).padStart(8, "0");
}

export function featureShardKey(layerId, key, length) {
  return length ? fnv1a(`${layerId}\u0000${key}`).slice(0, length) : "";
}

// Shards of a prefix-mode index to load for a query (by its longest word):
// the word's first `length` characters, or every shard starting with a
// shorter word. Returns null when too many shards would be needed (the UI
// asks for more characters; nothing is silently dropped).
export function shardsForQuery(manifest, query, maxShards = 24) {
  if (manifest.mode === "single") return manifest.shards;
  const terms = words(query);
  if (!terms.length) return [];
  const length = manifest.prefixLength;
  const forWord = (word) => (word.length >= length
    ? manifest.shards.filter((s) => s.key === word.slice(0, length))
    : manifest.shards.filter((s) => s.key.startsWith(word)));
  // The most selective word: the fewest bytes to download.
  let best = null;
  for (const word of terms) {
    const wanted = forWord(word);
    const bytes = wanted.reduce((sum, s) => sum + s.bytes, 0);
    if (!best || bytes < best.bytes) best = { wanted, bytes };
  }
  return best.wanted.length > maxShards ? null : best.wanted;
}

// Rank entries [layerIndex, key, label, terms, anchor, bounds, zoom] for a
// query. 0 exact term/label, 1 label or term prefix, 2 word prefix,
// 3 substring (allowSubstring). Deduplicated by (layer, key); stable order.
export function rank(entries, query, { allowSubstring = true, limit = 50 } = {}) {
  const q = normalize(query);
  if (!q) return { results: [], total: 0 };
  const qWords = words(query);
  const best = new Map();
  for (const entry of entries) {
    const texts = [entry[2], ...(entry[3] || [])].map(normalize);
    let score = null;
    if (texts.some((t) => t === q)) score = 0;
    else if (texts.some((t) => t.startsWith(q))) score = 1;
    else {
      const tokens = texts.flatMap((t) => t.split(WORD_SPLIT));
      if (qWords.every((w) => tokens.some((t) => t.startsWith(w)))) score = 2;
      else if (allowSubstring && texts.some((t) => t.includes(q))) score = 3;
    }
    if (score === null) continue;
    const id = `${entry[0]}\u0000${entry[1]}`;
    const previous = best.get(id);
    if (!previous || score < previous.score) best.set(id, { score, entry });
  }
  const ordered = [...best.values()].sort((a, b) => a.score - b.score
    || normalize(a.entry[2]).localeCompare(normalize(b.entry[2]))
    || a.entry[0] - b.entry[0] || String(a.entry[1]).localeCompare(String(b.entry[1])));
  return { results: ordered.slice(0, limit), total: ordered.length };
}
