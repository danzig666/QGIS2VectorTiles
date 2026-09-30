# Testing

Three levels, all driven by `pytest` from the repository root (`pytest.ini` configures
paths). Browser-test dependencies are development-only; nothing here is needed to run
the plugin.

| Level | Location | Needs | What it covers |
|---|---|---|---|
| Pure Python | `tests/unit` | Python ≥ 3.10, `pytest`, Pillow (pattern/atlas tests skip without it) | typed expressions, units and zoom curves, visibility intervals, diagnostics/redaction, capability registry, dependency tracking, validation, periodic hatch cells, atlas packing |
| PyQGIS integration | `tests/integration` | QGIS Python bindings + Processing, GDAL | property evaluation (falsy values, typing, units, opacity), enums/placement flags, sprite rendering failures and anchors, rule flattening (ELSE, scale intervals, project non-mutation), full exports incl. strict mode and archive inspection |
| Browser | `tests/browser` | the above + Node ≥ 18, `npm install` in `tests/browser`, Chromium | style-spec validation (pinned 24.3.1), headless render with the bundled MapLibre 5.11.0, console/map errors, missing images, per-layer rendering at zoom 13 and in overzoom (15.5); screenshots in `tests/browser/artifacts/` |

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
ln -s "$PWD" ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/QGIS2VectorTiles
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

### Regenerating the compatibility table

```bash
python3 tools/generate_capabilities.py        # writes docs/fidelity/CAPABILITIES.md
python3 tools/generate_capabilities.py --check  # used by tests/unit/test_generated_docs.py
```

## Fixtures

`tests/q2vt_fixtures.py` builds layers programmatically (no binary project files): four
1 km zoning squares near Budapest in EPSG:3857 — one with a hole, one with a NULL zone,
meeting at tile corners — written to GeoPackage when an export needs to re-open them.
