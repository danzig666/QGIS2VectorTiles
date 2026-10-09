"""Identity, search and feature-lookup indexes (PUB-03/11, plan A10-A13):
typed compound keys, Hungarian normalization identical in Python and JS,
complete sharded search over 100k+ records with skewed prefixes, exact
lookup shards, no geometry."""

import json
import os
import shutil
import subprocess

import pytest

from publishing.feature_index import build_feature_index, fnv1a, shard_key
from publishing.identifiers import encode_compound, key_expression, validate_keys
from publishing.search_index import build_search_index, normalize, words
from publishing.shard_pack import read_shard

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CORE = os.path.join(ROOT, "resources", "web_viewer", "search_core.mjs")
VECTORS = ["Őrbottyán", "ŐRBOTTYÁN", "Szabályozás  Övezet", "Kőszeg/12-3", "  Ünnep   út ",
           "Árvíztűrő tükörfúrógép", "00123/4", "ʼDéaʼk", "égy", "İzmir"]


def node(script):
    run = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_keys():
    expression, scope = key_expression(["hrsz"])
    assert scope == "persistent" and expression == 'to_string("hrsz")'
    assert key_expression([])[1] == "export"
    assert encode_compound(["1/2", "3"]) != encode_compound(["1", "2/3"])
    assert encode_compound(["a", None]) is None
    assert validate_keys(["1", "2", "2", None, ""]) == {"null": ["2"], "duplicate": ["2"]}
    assert 'length(to_string("b"))' in key_expression(["a", "b"])[0]


def test_hungarian_normalization():
    assert normalize("ŐRBOTTYÁN") == normalize("Őrbottyán") == "orbottyan"
    assert normalize("Árvíztűrő tükörfúrógép") == "arvizturo tukorfurogep"
    assert normalize("00123/4") == "00123/4"  # leading zeros and slash kept
    assert words("Kőszeg/12-3, Ünnep út") == ["koszeg/12-3", "unnep", "ut"]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_python_and_javascript_agree():
    js = node(f"import {{ normalize, words, fnv1a }} from {json.dumps('file://' + CORE)};"
              f"const v = {json.dumps(VECTORS)};"
              "console.log(JSON.stringify(v.map((x) => [normalize(x), words(x), fnv1a(x)])));")
    assert js == [[normalize(v), words(v), fnv1a(v)] for v in VECTORS]


def _records(n):
    for i in range(n):
        hrsz = f"{i:05d}/{i % 7}"
        settlement = "Arló" if i % 10 else "Ózd"  # skewed: 90 % share the prefix "a"
        yield {"layerId": "lyr-a" if i % 3 else "lyr-b", "featureKey": hrsz, "label": hrsz,
               "terms": [hrsz, f"{settlement} {i}"], "anchor": [20.1, 48.1], "bounds": [20.1, 48.1, 20.2, 48.2],
               "suggestedZoom": 17, "attributes": {"hrsz": hrsz, "secret": "x"}}


def test_single_file_index(tmp_path):
    manifest = build_search_index(_records(1000), {"lyr-a": ["hrsz"], "lyr-b": ["hrsz"]}, str(tmp_path))
    assert manifest["mode"] == "single" and manifest["records"] == 1000
    assert manifest["coverage"]["lyr-a"]["records"] + manifest["coverage"]["lyr-b"]["records"] == 1000
    data = json.load(open(tmp_path / "index.json", encoding="utf-8"))
    assert len(data["entries"]) == 1000
    assert "secret" not in json.dumps(data)  # only label/terms/anchor/bounds


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_sharded_index_is_complete_and_searchable(tmp_path):
    """A11: 120,000 records, skewed prefixes, no truncation; every record
    is in a shard reachable from its own key."""
    n = 120000
    manifest = build_search_index(_records(n), {"lyr-a": ["hrsz"], "lyr-b": ["hrsz"]}, str(tmp_path),
                                  budget_records=50000, shard_bytes=256 * 1024)
    assert manifest["mode"] == "prefix" and manifest["records"] == n
    assert manifest["prefixLength"] >= 2  # the skewed "a"/"arlo" prefix forced deeper shards
    found = set()
    # Every shard in one pack (a city's addresses were tens of thousands of files).
    assert sorted(os.listdir(tmp_path)) == ["index.pack"]
    assert {shard["path"] for shard in manifest["shards"]} == {"index.pack"}
    for shard in manifest["shards"]:
        data = read_shard(str(tmp_path), shard)
        assert len(data["entries"]) == shard["records"]
        assert shard["bytes"] <= 256 * 1024 + 4096
        found.update((e[0], e[1]) for e in data["entries"])
    assert len(found) == n  # complete coverage, no silent cap
    with open(tmp_path / "manifest.json", "w", encoding="utf-8") as handle:
        json.dump(manifest, handle)
    # The JS worker logic picks the right shard and ranks exact matches first.
    result = node(f"""
      import {{ shardsForQuery, rank }} from {json.dumps('file://' + CORE)};
      import {{ readFileSync }} from 'node:fs';
      import {{ gunzipSync }} from 'node:zlib';
      const dir = {json.dumps(str(tmp_path))};
      const read = (s) => JSON.parse(gunzipSync(readFileSync(dir + '/' + s.path)
        .subarray(s.offset, s.offset + s.length)).toString('utf8'));
      const manifest = JSON.parse(readFileSync(dir + '/manifest.json', 'utf8'));
      const out = {{}};
      for (const q of ['00042/0', 'ozd 1230', 'Ózd 119990', 'a']) {{
        const shards = shardsForQuery(manifest, q);
        if (shards === null) {{ out[q] = 'more'; continue; }}
        const entries = shards.flatMap((s) => read(s).entries);
        const r = rank(entries, q, {{ allowSubstring: false, limit: 5 }});
        out[q] = r.results.map((x) => x.entry[1]);
      }}
      console.log(JSON.stringify(out));""")
    assert result["00042/0"][0] == "00042/0"
    assert result["ozd 1230"][0] == "01230/5"
    assert result["Ózd 119990"] == [f"119990/{119990 % 7}"]
    assert result["a"] == "more"  # asks for more characters instead of truncating


def test_feature_index_shards(tmp_path):
    manifest = build_feature_index(_records(9000), {"lyr-a": {"popup": ["hrsz"]}, "lyr-b": {"popup": []}},
                                   str(tmp_path), target=1000)
    assert manifest["mode"] == "hash" and manifest["prefixLength"] == 1 and manifest["records"] == 9000
    key = "00123/4"
    shard = next(s for s in manifest["shards"] if s["key"] == shard_key("lyr-b", "00123/4", 1)) \
        if shard_key("lyr-b", key, 1) else None
    assert sorted(os.listdir(tmp_path)) == ["features.pack"]
    data = read_shard(str(tmp_path), shard)
    record = next(r for r in data if r["k"] == "00123/4" and r["l"] == "lyr-b")
    assert record["a"] == {}  # lyr-b exposes no popup fields
    assert "secret" not in json.dumps(data)
    assert set(record) == {"l", "k", "t", "a", "p", "b", "z"}  # no geometry


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_the_viewer_reads_packed_shards_by_range(tmp_path):
    """A city's search index was tens of thousands of shard files; they are
    one pack now. shards.mjs reads a shard with one range request, and from
    the whole pack when a server ignores Range; the shards of one pack are
    told apart (they share its path)."""
    import functools  # pylint: disable=import-outside-toplevel
    import http.server  # pylint: disable=import-outside-toplevel
    import threading  # pylint: disable=import-outside-toplevel
    from publishing.preview_server import PreviewServer  # pylint: disable=import-outside-toplevel
    manifest = build_search_index(_records(20000), {"lyr-a": ["hrsz"], "lyr-b": ["hrsz"]}, str(tmp_path),
                                  budget_records=5000, shard_bytes=64 * 1024)
    assert manifest["mode"] == "prefix" and sorted(os.listdir(tmp_path)) == ["index.pack"]
    shards = manifest["shards"][:3] + manifest["shards"][-2:]
    expected = [read_shard(str(tmp_path), s)["entries"][0][1] for s in shards]
    script = f"""
      import {{ fetchShard, shardId }} from {json.dumps('file://' + os.path.join(ROOT, 'resources', 'web_viewer', 'shards.mjs'))};
      const shards = {json.dumps(shards)};
      const base = process.argv[1];
      const ids = new Set(shards.map(shardId));
      const first = [];
      for (const s of shards) first.push((await fetchShard(base, s)).entries[0][1]);
      console.log(JSON.stringify({{ ids: ids.size, first }}));"""

    def run(base):
        result = subprocess.run(["node", "--input-type=module", "-e", script, base],
                                capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    with PreviewServer(str(tmp_path)) as server:  # byte ranges (206)
        assert run(server.url("")) == {"ids": len(shards), "first": expected}
    # A plain static server that ignores Range (200, the whole file).
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    plain = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=plain.serve_forever, daemon=True).start()
    try:
        assert run(f"http://127.0.0.1:{plain.server_address[1]}/")["first"] == expected
    finally:
        plain.shutdown()
