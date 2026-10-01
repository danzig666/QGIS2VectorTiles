# Testing

Three levels, all driven by `pytest` from the repository root (`pytest.ini` configures
paths). Browser-test dependencies are development-only; nothing here is needed to run
the plugin.

| Level | Location | Needs | What it covers |
|---|---|---|---|
| Pure Python | `tests/unit` | Python ≥ 3.10, `pytest`, Pillow (pattern/atlas tests skip without it) | typed expressions, units and zoom curves, visibility intervals, diagnostics/redaction, capability registry, dependency tracking, validation, periodic hatch cells, atlas packing |
| PyQGIS integration | `tests/integration` | QGIS Python bindings + Processing, GDAL | property evaluation (falsy values, typing, units, opacity), enums/placement flags, sprite rendering failures and anchors, rule flattening (ELSE, scale intervals, project non-mutation), full exports incl. strict mode and archive inspection |
| Browser | `tests/browser` | the above + Node ≥ 18, `npm install` in `tests/browser`, Chromium | style-spec validation (pinned 26.4.4), headless render with the bundled MapLibre 6.11.2 (ES modules: `maplibre-gl.mjs` + `-shared.mjs` + `-worker.mjs`), console/map errors, missing images, per-layer rendering at zoom 13 and in overzoom (15.5); screenshots in `tests/browser/artifacts/` |

Levels whose requirements are missing are skipped automatically.

## Commands

```bash
# Pure Python only
python3 -m pytest tests/unit

# Everything (QGIS must be importable by this interpreter)
QT_QPA_PLATFORM=offscreen python3 -m pytest
```

### Ubuntu 24.04 (QGIS 3.34 from the distribution — used for the recorded baseline)

```bash
sudo apt-get install python3-qgis qgis-providers gdal-bin python3-pytest
(cd tests/browser && npm install)
# The plugin locates resources in the QGIS profile; link the checkout there once:
mkdir -p ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins
ln -s "$PWD" ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/QGIS2VectorTilesFork
QT_QPA_PLATFORM=offscreen python3.12 -m pytest
```

The profile directory is the one `QgsApplication.qgisSettingsDirPath()` reports for a
headless application (in the recorded container it was `~/.local/share/profiles/default/`).

### QGIS 3.44 / 4.x (required before advertising those versions)

Use the official QGIS images, e.g.:

```bash
docker run --rm -v "$PWD":/plugin -w /plugin qgis/qgis:release-3_44 \
  bash -c "pip install pytest && QT_QPA_PLATFORM=offscreen python3 -m pytest tests/unit tests/integration"
```

or the OSGeo4W shell on Windows (`python -m pytest tests\unit tests\integration`). These
runs have **not** been performed yet; see [BASELINE.md](BASELINE.md).

### QGIS-vs-browser comparisons

`tests/browser/test_browser_parity.py` renders small fixtures with QGIS and with the
bundled MapLibre at the same viewport and checks positional agreement of the ink.

`tools/gallery/build_gallery.py` does the same for every symbol and label style of a QGIS
style database (optionally filtered by tags) and for QML files, and writes an HTML page
sorted by mismatch:

```bash
QT_QPA_PLATFORM=offscreen python3 tools/gallery/build_gallery.py \
    --style-db symbology-style.db --tags mytag --qml styles/*.qml --out /tmp/gallery
```

The default shapes are simple (a three-segment line, a pentagon with a hole) at one zoom.
`--hard` uses demanding ones instead: a long line with a sharp zigzag, a run of 1.6 m
segments, a smooth 60-vertex wave, a hairpin and a closed loop; a two-part polygon with a
many-vertex curve, a narrow notch, an acute spike, a saw of 2 m segments and two holes.
Every cell is centred on a z14 tile corner, so tile edges cross the shapes at every zoom,
and each style is rendered at several zooms (`--zooms`, default 14.6, 16.25, 17.8; the
item score is the mean). The summary line gives the mean per geometry type.

Every run also writes `images/compare.html`, a self-contained page of the styles that
differ: QGIS and browser renders side by side, an overlay (red: ink only QGIS draws,
cyan: only the browser, grey: both), a swipe slider and a blink view, zoomable to 2x/4x
with sharp pixels — a shifted arrow or a 2 px offset is visible at a glance, where the
mismatch score of thin elements stays small. Build it for any run and filter:

```bash
python3 tools/gallery/compare_page.py /tmp/gallery --out compare.html \
    --min 0.1 --names "Measure,Csíkozás" --baseline /tmp/gallery_before
```

Behaviour is checked against the QGIS source (`src/core/symbology`,
`src/core/labeling`) before it is reproduced, then confirmed by rendering.

### Regenerating the compatibility table

```bash
python3 tools/generate_capabilities.py        # writes docs/fidelity/CAPABILITIES.md
python3 tools/generate_capabilities.py --check  # used by tests/unit/test_generated_docs.py
```

## Fixtures

`tests/q2vt_fixtures.py` builds layers programmatically (no binary project files): four
1 km zoning squares near Budapest in EPSG:3857 — one with a hole, one with a NULL zone,
meeting at tile corners — written to GeoPackage when an export needs to re-open them.

## Scaling benchmark

`tests/integration/test_scaling.py` exports a large, detailed layer (16 polygons of
~0.3 km² with 600 vertices: clipped cross pattern, random fill, map-unit dashes) and a
single 25 km² polygon with a 10 m pattern, and prints the timings. Reference (this
container, one worker): 26 s for 640 000 pattern features; 7 s for 251 000 markers of
the 25 km² polygon (previously: 34 s, and 0 markers for the large polygon).
A random fill of one point per 10 m² on the 16 polygons (458 000 points) takes 5.5 s
(previously 25 s): random points are kept as one multipoint per polygon.

Patterns above `SymbolMaterializer.MAX_PATTERN_ELEMENTS` (2 000 000 features per rule,
estimated from the layer's area or length) are drawn as textures at every zoom and
reported as `Q2VT_PATTERN_BUDGET`.


## Comparing a real project

`tools/gallery/project_compare.py` exports chosen layers of a QGIS project (or
the whole map with `--layers "*"`), renders QGIS in the project CRS at the
ground resolution of each browser zoom and the exported package in the
browser, and writes `compare.html` (side by side, overlay, swipe, blink):

    python3 tools/gallery/project_compare.py project.qgs --out /tmp/cmp \
        --layers "*" --center-layer Épületek --zooms 16,17.5,19.5 --max-zoom 18

The browser page runs the viewer's visible-polygon labels and the
"overlap if required" label fallback (`resources/ml_viewer/visible_labels.mjs`),
as the MapLibre viewer does.
