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


def _image(rgba):
    """QImage (ARGB32) from an (h, w, 4) uint8 RGBA array."""
    from qgis.PyQt.QtGui import QImage
    h, w = rgba.shape[:2]
    bgra = np.ascontiguousarray(rgba[..., [2, 1, 0, 3]])
    return QImage(bgra.tobytes(), w, h, w * 4, QImage.Format.Format_ARGB32).copy()


def _pixels(image):
    from qgis.PyQt.QtGui import QImage
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    ptr = image.constBits()
    ptr.setsize(image.sizeInBytes())
    bgra = np.frombuffer(ptr, dtype=np.uint8).reshape(image.height(), image.bytesPerLine())
    bgra = bgra[:, :image.width() * 4].reshape(image.height(), image.width(), 4)
    return bgra[..., [2, 1, 0, 3]].astype(float)


def test_blend_modes_become_the_same_normal_image():
    """Multiply by grey g = black with alpha 1 - g; screen = white with
    alpha g: drawn normally over any colour, the result is the blend."""
    from publishing.raster_tiles import blend_to_alpha
    grey = np.linspace(0, 255, 16).round().astype(np.uint8)
    rgba = np.zeros((1, 16, 4), np.uint8)
    rgba[0, :, 0] = rgba[0, :, 1] = rgba[0, :, 2] = grey
    rgba[0, :, 3] = 255
    below = np.array([200.0, 120.0, 40.0])  # a DEM colour
    g = grey.astype(float)[:, None] / 255
    for mode, expected in (("multiply", below * g), ("screen", 255 - (255 - below) * (1 - g))):
        image, is_grey = blend_to_alpha(_image(rgba), mode)
        px = _pixels(image)[0]
        a = px[:, 3:4] / 255
        drawn = px[:, :3] * a + below * (1 - a)
        assert is_grey and np.abs(drawn - expected).max() <= 1.0, mode
    rgba[0, :, 0] = 255  # a colour layer: approximated by its brightness
    assert blend_to_alpha(_image(rgba), "multiply")[1] is False


def test_hillshade_multiply_over_a_dem_is_published_as_shading(tmp_path):
    """The TerrainForge case: a grey hillshade in multiply mode over a colour
    DEM (also multiply, lowest, on a white map). The hillshade tiles become
    black with alpha 1 - grey; the DEM is drawn normally (the same over
    white); a vector layer's blend mode is reported, not silently dropped."""
    from qgis.PyQt.QtGui import QImage, QPainter
    project, profile, parcels, dem = _project(tmp_path)
    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(str(tmp_path / "hillshade.tif"), 400, 400, 1, gdal.GDT_Byte)
    ds.SetGeoTransform([2119000, 10.0, 0, 6023000, 0, -10.0])
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(3857)
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).WriteArray((np.mgrid[0:400, 0:400][1] * 255 // 399).astype(np.uint8))
    ds = None
    shade = QgsRasterLayer(str(tmp_path / "hillshade.tif"), "Hillshade")
    assert shade.isValid() and shade.renderer().type() == "singlebandgray"
    multiply = QPainter.CompositionMode.CompositionMode_Multiply
    shade.setBlendMode(multiply)
    dem.setBlendMode(multiply)
    parcels.setBlendMode(multiply)
    project.addMapLayer(shade, False)
    project.layerTreeRoot().insertLayer(1, shade)  # parcels, hillshade, DEM (bottom)
    profile.layers.append(LayerConfig(shade.id(), raster_format="png", raster_max_zoom=13))
    profile.layers[-2].raster_format = "png"
    result = export_local(project, profile, EXTENT)
    rel = result.release.release_dir
    manifest = json.load(open(os.path.join(rel, "manifest.json"), encoding="utf-8"))
    href = {s["id"]: s["href"] for s in manifest["sources"] if s.get("role") == "raster"}
    shade_href = next(h for h in href.values() if layer_logical_id(shade.id()) in h)
    dem_href = next(h for h in href.values() if layer_logical_id(dem.id()) in h)
    with open_pmtiles(os.path.join(rel, shade_href)) as archive:
        _, data = next(iter(archive.tiles()))
    px = _pixels(QImage.fromData(data))
    assert px[..., :3].max() == 0 and 0 < px[..., 3].max() <= 255  # black shading only
    with open_pmtiles(os.path.join(rel, dem_href)) as archive:
        _, data = next(iter(archive.tiles()))
    px = _pixels(QImage.fromData(data))
    visible = px[px[..., 3] > 0]
    assert len(visible) and visible[:, 3].min() == 255 and visible[:, :3].max() > 0  # the DEM's own colours
    assert any("Földrészletek" in w and "multiply" in w for w in result.warnings)
    assert not any("Hillshade" in w or "Ortofotó" in w for w in result.warnings)


def test_unchanged_raster_layers_are_reused_from_the_export_cache(tmp_path, monkeypatch):
    """Rendering rasters is slow and they rarely change: a second export with
    the same raster, style and settings reuses the archive; a style or image
    setting change renders it again."""
    from q2vt_plugin.src.publishing import raster_tiles  # pylint: disable=import-error
    rendered = []
    original = raster_tiles.render_layer

    def counting(*args, **kwargs):
        rendered.append(args[1].id())
        return original(*args, **kwargs)
    monkeypatch.setattr(raster_tiles, "render_layer", counting)
    project, profile, parcels, ortho = _project(tmp_path)
    profile.output.reuse_unchanged = True
    lid = layer_logical_id(ortho.id())

    def export():
        rel = export_local(project, profile, EXTENT).release.release_dir
        with open(os.path.join(rel, "data", f"raster-{lid}.pmtiles"), "rb") as handle:
            return handle.read()
    first = export()
    second = export()
    assert len(rendered) == 1 and first == second  # reused, same archive
    ortho.renderer().setOpacity(0.5)  # style changed
    export()
    assert len(rendered) == 2
    profile.layer(ortho.id()).raster_quality = 60  # image setting changed
    export()
    assert len(rendered) == 3


def test_raster_tiles_are_the_same_with_any_number_of_threads(tmp_path):
    """Metatiles are rendered and encoded in parallel, then written in a
    fixed order: the archive is byte for byte the one a single thread makes
    (so the export cache and unchanged-file uploads keep working)."""
    from q2vt_plugin.src.publishing import raster_tiles  # pylint: disable=import-error
    project, profile, parcels, ortho = _project(tmp_path)
    config = profile.layer(ortho.id())
    archives = []
    for threads in (1, 4):
        plan = raster_tiles.plan_layer(project, ortho, config, profile, EXTENT)
        out = str(tmp_path / f"r{threads}.pmtiles")
        raster_tiles.render_layer(project, ortho, config, plan, out, metatile=2, threads=threads)
        with open(out, "rb") as handle:
            archives.append(handle.read())
    assert archives[0] == archives[1] and len(archives[0]) > 1000


def test_webp_falls_back_to_png_where_qgis_cannot_write_it(tmp_path, monkeypatch):
    """WebP is the default image format; a QGIS without a WebP writer makes
    PNG tiles (also transparent) with a warning instead of failing."""
    from q2vt_plugin.src.publishing import raster_tiles  # pylint: disable=import-error
    from q2vt_plugin.src.publishing.vendor.pmtiles.tile import TileType  # pylint: disable=import-error
    assert LayerConfig("x").raster_format == "webp"
    project, profile, parcels, ortho = _project(tmp_path)
    config = profile.layer(ortho.id())

    class NoWebp:  # pylint: disable=too-few-public-methods
        @staticmethod
        def supportedImageFormats():
            return [b"png", b"jpeg"]
    monkeypatch.setattr(raster_tiles, "QImageWriter", NoWebp)
    plan = raster_tiles.plan_layer(project, ortho, config, profile, EXTENT)
    out = str(tmp_path / "fallback.pmtiles")
    raster_tiles.render_layer(project, ortho, config, plan, out)
    with open_pmtiles(out) as archive:
        assert archive.header["tile_type"] == TileType.PNG and archive.metadata()["format"] == "png"
    assert any("cannot write WebP" in warning for warning in plan.warnings)


def test_matching_the_image_resolution_sets_the_maximum_zoom(tmp_path):
    """Ground resolution of the image (one pixel measured on the ellipsoid:
    10 Web Mercator m at 47.5° N are 6.76 m) and of the tiles; matching picks
    the lowest zoom as sharp as the image, one less with 512 px tiles."""
    import math
    from q2vt_plugin.src.publishing import raster_tiles  # pylint: disable=import-error
    assert raster_tiles.native_zoom(0.4, 47.97) == 18 and raster_tiles.native_zoom(0.4, 47.97, True) == 17
    assert raster_tiles.ground_resolution(18, 47.97) == pytest.approx(0.4, rel=0.01)
    project, profile, parcels, ortho = _project(tmp_path)
    config = profile.layer(ortho.id())
    config.raster_max_zoom = None
    plan = raster_tiles.plan_layer(project, ortho, config, profile, EXTENT)
    assert plan.native_m == pytest.approx(10 * math.cos(math.radians(plan.latitude)), rel=0.01)
    assert plan.max_zoom == profile.view.max_zoom == 13
    config.raster_match_native = True
    plan = raster_tiles.plan_layer(project, ortho, config, profile, EXTENT)
    assert plan.max_zoom == 14 and plan.resolution(14) <= plan.native_m * 1.04 < plan.resolution(13)
    config.raster_hidpi = True
    assert raster_tiles.plan_layer(project, ortho, config, profile, EXTENT).max_zoom == 13
