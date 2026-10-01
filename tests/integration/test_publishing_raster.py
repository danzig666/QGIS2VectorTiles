"""QGIS raster layers in a publication: rendered by QGIS into their own
raster PMTiles archive (PNG/JPEG/WebP), drawn at their layer-tree position
under the vector layers above them and under all labels; vector layers stay
MVT; empty rasters are dropped; the style contract only accepts the
release's own image archives."""

import json
import os
import sys

import numpy as np
import pytest
from osgeo import gdal, osr
from qgis.core import QgsProject, QgsRasterLayer, QgsRectangle

from publishing.controller import export_local
from publishing.errors import PublishingError
from publishing.models import LayerConfig
from publishing.provenance import layer_logical_id
from publishing.validation import open_pmtiles, validate_pmtiles, vector_only_violations
from q2vt_fixtures import reset_project

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_publishing_pipeline import _parcels, _profile  # noqa: E402  pylint: disable=wrong-import-position

SCHEMA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                      "schemas", "publishing", "manifest-v1.schema.json")


def _validate_manifest(manifest):
    jsonschema = pytest.importorskip("jsonschema")
    with open(SCHEMA, encoding="utf-8") as handle:
        jsonschema.validate(manifest, json.load(handle))


EXTENT = QgsRectangle(2119000, 6019000, 2123000, 6023000)


def make_raster(path, origin=(2119000, 6023000), size=400, pixel=10.0, alpha=False):
    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(str(path), size, size, 4 if alpha else 3, gdal.GDT_Byte)
    ds.SetGeoTransform([origin[0], pixel, 0, origin[1], 0, -pixel])
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(3857)
    ds.SetProjection(srs.ExportToWkt())
    yy, xx = np.mgrid[0:size, 0:size]
    bands = [xx % 256, yy % 256, (xx + yy) % 256]
    if alpha:
        bands.append(np.where(xx < size // 2, 255, 0))
    for index, band in enumerate(bands, 1):
        ds.GetRasterBand(index).WriteArray(band.astype(np.uint8))
    ds = None
    return str(path)


def _project(tmp_path, raster_above=False, **raster_kwargs):
    project = reset_project()
    parcels = _parcels(str(tmp_path / "parcels.gpkg"))
    ortho = QgsRasterLayer(make_raster(tmp_path / "ortho.tif", **raster_kwargs), "Ortofotó")
    assert ortho.isValid()
    root = project.layerTreeRoot()
    project.addMapLayer(parcels, False)
    project.addMapLayer(ortho, False)
    if raster_above:
        root.addLayer(ortho)
        root.addLayer(parcels)
    else:
        root.addLayer(parcels)
        root.addLayer(ortho)
    profile = _profile(parcels, tmp_path)
    profile.layers.append(LayerConfig(ortho.id(), raster_format="webp", raster_max_zoom=15))
    return project, profile, parcels, ortho


def test_raster_layer_gets_its_own_image_archive_under_the_vectors(tmp_path):
    project, profile, parcels, ortho = _project(tmp_path)
    result = export_local(project, profile, EXTENT)
    rel = result.release.release_dir
    manifest = json.load(open(os.path.join(rel, "manifest.json"), encoding="utf-8"))
    _validate_manifest(manifest)
    style = json.load(open(os.path.join(rel, "style.json"), encoding="utf-8"))
    lid = layer_logical_id(ortho.id())
    raster = [s for s in manifest["sources"] if s.get("role") == "raster"]
    assert manifest["sources"][0]["tileType"] == "mvt" and len(raster) == 1
    assert raster[0]["tileType"] == "webp" and raster[0]["href"] == f"data/raster-{lid}.pmtiles"
    summary = validate_pmtiles(os.path.join(rel, raster[0]["href"]), sample=0, kind="image")
    assert summary["tileType"] == "webp" and summary["maxZoom"] == 15
    with open_pmtiles(os.path.join(rel, raster[0]["href"])) as archive:
        assert archive.metadata()["format"] == "webp"
    # The vector map is still MVT only and the raster sits under the parcels.
    validate_pmtiles(os.path.join(rel, "data", "map.pmtiles"), sample=0)
    ids = [layer["id"] for layer in style["layers"]]
    raster_index = ids.index(f"q2vt-raster-{lid}")
    parcel_layers = [c for c in manifest["components"] if c["layerId"] == layer_logical_id(parcels.id())]
    first_parcel = min(ids.index(i) for c in parcel_layers for i in c["styleLayerIds"] if i in ids)
    assert raster_index < first_parcel
    entry = next(layer for layer in manifest["layers"] if layer["id"] == lid)
    assert entry["geometry"] == "raster" and entry["componentIds"] == [f"c-raster-{lid}"]
    assert not vector_only_violations(style, raster_sources={raster[0]["id"]})
    assert vector_only_violations(style)  # without the release's own archives: refused
    # The project is untouched.
    assert project.layerTreeRoot().findLayer(ortho.id()).isVisible()


def test_raster_above_vectors_stays_under_labels(tmp_path):
    project, profile, parcels, ortho = _project(tmp_path, raster_above=True, alpha=True)
    profile.layers[-1].raster_format = "png"
    result = export_local(project, profile, EXTENT)
    rel = result.release.release_dir
    manifest = json.load(open(os.path.join(rel, "manifest.json"), encoding="utf-8"))
    _validate_manifest(manifest)
    style = json.load(open(os.path.join(rel, "style.json"), encoding="utf-8"))
    ids = [layer["id"] for layer in style["layers"]]
    lid = layer_logical_id(ortho.id())
    raster_index = ids.index(f"q2vt-raster-{lid}")
    labels = [i for c in manifest["components"] if c["role"] == "label" for i in c["styleLayerIds"] if i in ids]
    geometry = [i for c in manifest["components"] if c["role"] == "geometry" for i in c["styleLayerIds"] if i in ids]
    assert labels and all(ids.index(i) > raster_index for i in labels)
    assert all(ids.index(i) < raster_index for i in geometry)
    # Half of the image is transparent: those tiles are not stored.
    source = next(s for s in manifest["sources"] if s.get("role") == "raster")
    with open_pmtiles(os.path.join(rel, source["href"])) as archive:
        stored = list(archive.entries())
    from publishing.raster_tiles import count_tiles
    full = count_tiles((2119000, 6019000, 2123000, 6023000), profile.view.min_zoom, 15)
    assert 0 < len(stored) < full


def test_raster_outside_the_extent_is_dropped_and_runaway_sizes_refused(tmp_path):
    project, profile, parcels, ortho = _project(tmp_path, origin=(3000000, 7000000))
    result = export_local(project, profile, EXTENT)
    manifest = json.load(open(os.path.join(result.release.release_dir, "manifest.json"), encoding="utf-8"))
    assert all(s.get("role") != "raster" for s in manifest["sources"])
    assert layer_logical_id(ortho.id()) not in {layer["id"] for layer in manifest["layers"]}
    assert any("draws nothing" in w for w in result.warnings)
    (tmp_path / "big").mkdir()
    project, profile, parcels, ortho = _project(tmp_path / "big", size=20, pixel=2000)
    profile.layers[-1].raster_max_zoom = 22
    with pytest.raises(PublishingError, match="tiles"):
        export_local(project, profile, QgsRectangle(2000000, 5900000, 2200000, 6100000))
