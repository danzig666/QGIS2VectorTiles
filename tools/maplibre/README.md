# The viewer's MapLibre GL JS

`resources/ml_viewer/maplibre-gl.mjs` and `maplibre-gl-worker.mjs` are built
here: MapLibre's readable build (`dist/maplibre-gl-dev.mjs` and
`maplibre-gl-worker-dev.mjs` of the npm package), patched so that QGIS
symbology draws on the web as QGIS draws it, then minified with esbuild.

- `package.json` / `package-lock.json` pin MapLibre and esbuild (npm checks
  the packages' checksums).
- `patches/NN_name.py`, one per fix, applied in order. Each says why, and
  lists exact text edits: `(file, old text, new text, how many times the old
  text occurs)`, file `"main"`, `"worker"` or `"both"`. Write them with
  4-space indents; the readable build's tabs are matched.
- `build.py` applies the patches and writes the bundle, with the patch names
  in its first line.

```
cd tools/maplibre && npm ci && cd ../..
python3 tools/maplibre/build.py            # build the bundle
python3 tools/maplibre/build.py --check    # is the committed bundle this build? (CI)
python3 tools/maplibre/build.py --readable # unminified bundle, for debugging
```

## Upgrading MapLibre

1. Set the new version in `package.json`, run `npm install` here (updates
   the lock file).
2. Run `build.py`. A patch whose text MapLibre changed stops the build and is
   named, with the first line of the missing text: find that code in
   `node_modules/maplibre-gl/dist/maplibre-gl-dev.mjs` (or the worker), and
   update the patch's old and new text. Since 6.13 the code formerly shared
   by the main thread and the worker is in both files: patch both (`"both"`)
   when a bucket is built in the worker and uploaded on the main thread.
3. Run the browser tests and the style gallery (`tools/gallery`) before and
   after, and compare.
4. Update the MapLibre rows of `docs/fidelity/BASELINE.md` and
   `docs/publishing/BASELINE.md`.

`tests/unit/test_label_lines.py` checks our port of MapLibre's label angle
test against the installed readable build (skipped when it is not
installed).
