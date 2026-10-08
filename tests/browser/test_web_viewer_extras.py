"""Viewer additions of 4.24: the map's own data (legal / data date, issuer,
decree) and documents, the overview map, drawing kept in the shared link and
saved as GeoJSON/KML, house numbers in the search, web basemaps from WMS.
The overview map, the 3D view and drawing are off unless chosen (4.25)."""

import json
import os
import subprocess
import sys
import urllib.request

import pytest
from qgis.core import QgsFeature, QgsField, QgsGeometry, QgsPointXY, QgsRectangle, QgsVectorLayer
from qgis.PyQt.QtCore import QVariant

from publishing.controller import export_local
from publishing.models import DocumentConfig, XyzBasemap
from publishing.preview_server import PreviewServer
from publishing.xyz import wms_template

HERE = os.path.dirname(os.path.abspath(__file__))
EXTENT = QgsRectangle(2119000, 6019000, 2123000, 6023000)
SHOTS = os.environ.get("Q2VT_SHOTS")  # a folder: screenshots for a person to look at


def _run(url, actions, tmp_path, width=1000, height=700):
    path = tmp_path / f"actions_{abs(hash(json.dumps(actions)))}.json"
    path.write_text(json.dumps(actions))
    run = subprocess.run(["node", "interact.mjs", url, str(path), str(width), str(height)],
                         capture_output=True, text=True, cwd=HERE, timeout=300)
    assert run.returncode == 0, run.stderr[-3000:]
    out = json.loads(run.stdout.strip().splitlines()[-1])
    assert not out["pageErrors"], out
    return out["results"]


def _addresses(path_crs="EPSG:3857"):
    layer = QgsVectorLayer(f"Point?crs={path_crs}", "Házszámok", "memory")
    layer.dataProvider().addAttributes([QgsField("hsz", QVariant.String), QgsField("utca", QVariant.String)])
    layer.updateFields()
    features = []
    for x, y, number, street in ((2120500, 6020500, "12", "Fő utca"), (2121500, 6021500, "3/A", "Kossuth Lajos utca"),
                                 (2130000, 6030000, "99", "Távoli utca")):  # the last is outside the extent
        feature = QgsFeature(layer.fields())
        feature.setAttributes([number, street])
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    return layer


def _dem(path):
    """Heights rising from 100 m (west) to 300 m (east) over the extent, 10 m pixels."""
    from osgeo import gdal, osr  # pylint: disable=import-outside-toplevel
    import numpy as np  # pylint: disable=import-outside-toplevel
    size = 440
    x0, y1 = EXTENT.xMinimum() - 200, EXTENT.yMaximum() + 200
    data = np.tile(np.linspace(100, 300, size, dtype=np.float32), (size, 1))
    data[:10, :10] = -9999  # a hole
    dataset = gdal.GetDriverByName("GTiff").Create(str(path), size, size, 1, gdal.GDT_Float32)
    dataset.SetGeoTransform((x0, 10, 0, y1, 0, -10))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(3857)
    dataset.SetProjection(srs.ExportToWkt())
    band = dataset.GetRasterBand(1)
    band.SetNoDataValue(-9999)
    band.WriteArray(data)
    dataset = None
    return str(path)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "integration"))
    from test_publishing_pipeline import _parcels, _profile  # pylint: disable=import-error
    from q2vt_fixtures import reset_project
    base = tmp_path_factory.mktemp("extras")
    parcels = _parcels(str(base / "parcels.gpkg"))
    project = reset_project()
    project.addMapLayer(parcels)
    addresses = _addresses()
    project.addMapLayer(addresses)
    profile = _profile(parcels, base)
    profile.layers[0].initially_visible = True
    document = base / "HÉSZ rendelet (2025).pdf"
    document.write_bytes(b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n")
    info = profile.info
    info.issuer, info.decree = "Arló Község Önkormányzata", "12/2025. (X. 1.) önk. rendelet"
    info.legal_date, info.data_date = "2025. 10. 01.", "2025. 09."
    info.documents = [DocumentConfig("Helyi építési szabályzat", str(document))]
    interaction = profile.interaction
    interaction.overview_map = interaction.drawing = interaction.three_d = True
    interaction.address_layer_id, interaction.address_number_field = addresses.id(), "hsz"
    interaction.address_street_field = "utca"
    profile.basemap.xyz = [XyzBasemap("Ortofotó WMS", wms_template("https://wms.example.hu/ows", "ORTO"))]
    from qgis.core import QgsRasterLayer  # pylint: disable=import-outside-toplevel
    dem = QgsRasterLayer(_dem(base / "dem.tif"), "Domborzat")
    assert dem.isValid()
    project.addMapLayer(dem)
    profile.terrain.layer_id = dem.id()
    profile.layers[0].height_field = "terulet"  # 1000.5 m: test heights
    interaction.measure = True
    result = export_local(project, profile, EXTENT)
    with PreviewServer(os.path.dirname(result.publication_dir)) as server:
        yield {"server": server, "url": server.url(f"{profile.slug}/index.html"), "result": result}


def test_release_carries_the_info_documents_and_wms(site):
    release = site["result"].release.release_dir
    manifest = json.load(open(os.path.join(release, "manifest.json"), encoding="utf-8"))
    info = manifest["info"]
    assert info["legalDate"] == "2025. 10. 01." and info["issuer"].startswith("Arló")
    assert info["documents"] == [{"title": "Helyi építési szabályzat", "href": "docs/hesz-rendelet-2025.pdf",
                                  "file": "HÉSZ rendelet (2025).pdf", "size": 45}]
    assert os.path.isfile(os.path.join(release, "docs", "hesz-rendelet-2025.pdf"))
    wms = manifest["basemap"]["xyz"][0]
    assert "{bbox-epsg-3857}" in wms["tiles"][0] and "REQUEST=GetMap" in wms["tiles"][0]
    page = open(os.path.join(release, "index.html"), encoding="utf-8").read()
    assert "https://wms.example.hu" in page  # allowed by the page's security policy
    assert manifest["tools"]["draw"] is True and manifest["interaction"]["overviewMap"] is True


def test_info_stamp_documents_and_popup_link(site, tmp_path):
    results = _run(site["url"], [{"eval": """
      const stamp = document.getElementById('q2vt-stamp');
      const links = [...document.querySelectorAll('#q2vt-info .q2vt-documents a')];
      const response = await fetch(links[0].href);
      const { formatValue } = await import(new URL('assets/identify.mjs', q2vtViewer.releaseUrl).href);
      return { stamp: stamp.hidden ? null : stamp.textContent,
               terms: [...document.querySelectorAll('#q2vt-info dt')].map((n) => n.textContent),
               link: links.map((a) => [a.textContent, a.getAttribute('href').split('/').slice(-2).join('/')]),
               status: response.status, type: response.headers.get('content-type'),
               popup: formatValue('C:\\\\terv\\\\HÉSZ rendelet (2025).pdf', 'string'),
               plain: formatValue('nincs ilyen.pdf', 'string') };
    """}], tmp_path)
    out = results[0]
    assert out["stamp"] == "Hatályos: 2025. 10. 01. · Adatok állapota: 2025. 09."
    assert out["terms"] == ["Kiadó", "Rendelet", "Hatályos", "Adatok állapota"]
    assert out["link"] == [["Helyi építési szabályzat", "docs/hesz-rendelet-2025.pdf"]]
    assert out["status"] == 200 and out["type"] == "application/pdf"
    assert out["popup"]["url"].endswith("docs/hesz-rendelet-2025.pdf") and out["popup"]["text"] == "HÉSZ rendelet (2025).pdf"
    assert "url" not in out["plain"]


def test_overview_map_follows_the_view(site, tmp_path):
    actions = [{"eval": """
      const o = q2vtViewer.controls.overview;
      await new Promise((r) => (o.mini.loaded() ? r() : o.mini.once('load', r)));
      const m = q2vtViewer.map;
      m.jumpTo({ center: [19.05, 47.41], zoom: 15 });
      o.sync();
      const box = (await o.mini.getSource('q2vt_overview_view').getData()).geometry.coordinates[0];
      return { shown: !o.box.hidden, zoom: o.mini.getZoom(), mainZoom: m.getZoom(),
               box: [box[0][0] < 19.05, box[2][0] > 19.05],
               labels: o.mini.getStyle().layers.filter((l) => l.type === 'symbol').length };
    """}]
    if SHOTS:
        actions.append({"screenshot": os.path.join(SHOTS, "overview.png")})
    out = _run(site["url"], actions, tmp_path)[0]
    assert out["shown"] and abs(out["mainZoom"] - out["zoom"] - 4) < 0.01
    assert out["box"] == [True, True] and out["labels"] == 0


def test_drawing_travels_in_the_link_and_exports(site, tmp_path):
    out = _run(site["url"], [
        {"eval": """
          const d = q2vtViewer.controls.draw, m = q2vtViewer.map;
          m.jumpTo({ center: [19.05, 47.41], zoom: 15 });
          const at = (lng, lat) => ({ lngLat: { lng, lat } });
          d.start('line'); d.click(at(19.049, 47.409)); d.click(at(19.051, 47.411)); d.finish();
          d.setColor('#2563eb');
          d.start('area'); d.click(at(19.048, 47.41)); d.click(at(19.049, 47.412)); d.click(at(19.05, 47.41)); d.finish();
          d.start('text'); d.textInput.value = '<b>Új út</b>';
          // Enter typed in the text box does not end text mode (4.25 fix).
          d.textInput.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
          const textMode = d.mode;
          d.click(at(19.05, 47.41)); d.cancel();
          d.start('point'); d.click(at(19.052, 47.408)); d.cancel();
          const { toKML, toGeoJSON } = await import(new URL('assets/draw.mjs', q2vtViewer.releaseUrl).href);
          const link = q2vtViewer.permalink.links().versioned;
          return { kinds: d.features.map((f) => f.kind), link,
                   label: document.querySelector('.q2vt-draw-label').innerHTML,
                   geojson: toGeoJSON(d.features).features.map((f) => f.geometry.type),
                   kml: toKML(d.features, 'Arló').includes('<name>&lt;b&gt;Új út&lt;/b&gt;</name>'),
                   identify: q2vtViewer.drawing, textMode };
        """},
    ], tmp_path)[0]
    assert out["kinds"] == ["line", "area", "text", "point"]
    assert out["geojson"] == ["LineString", "Polygon", "Point", "Point"]
    assert out["label"] == "&lt;b&gt;Új út&lt;/b&gt;" and out["kml"] and out["identify"] is False
    assert "&d=" in out["link"] and out["textMode"] == "text"
    again = _run(out["link"], [{"eval": """
      const d = q2vtViewer.controls.draw, source = q2vtViewer.map.getSource('q2vt_draw');
      return { features: d.features.map((f) => [f.kind, f.color, f.text, f.coords.length,
                                                f.coords[0].map((v) => +v.toFixed(5))]),
               // drawn on the map as soon as the link opens (4.25 fix: was only on the next edit)
               drawn: source ? (await source.getData()).features.length : 0,
               labels: document.querySelectorAll('.q2vt-draw-label').length };
    """}], tmp_path)[0]
    assert again["drawn"] == 3 and again["labels"] == 1
    assert again["features"] == [["line", "#e11d48", "", 2, [19.049, 47.409]], ["area", "#2563eb", "", 3, [19.048, 47.41]],
                     ["text", "#2563eb", "<b>Új út</b>", 1, [19.05, 47.41]], ["point", "#2563eb", "", 1, [19.052, 47.408]]]


def test_house_numbers_in_the_search(site, tmp_path):
    out = _run(site["url"], [
        {"type": ["#q2vt-searchbox input", "fő u 12"]}, {"wait": 900},
        {"eval": """
          const items = [...document.querySelectorAll('#q2vt-results [role=option]')];
          return items.map((n) => n.textContent);
        """},
        {"type": ["#q2vt-searchbox input", "távoli"]}, {"wait": 900},
        {"eval": "return document.querySelectorAll('#q2vt-results [role=option]').length;"},
    ], tmp_path)
    assert out[0] == ["Fő utca 12Cím"], out
    assert out[1] == 0  # outside the extent: not published


def test_terrain_archive_heights(site):
    from publishing.validation import open_pmtiles  # pylint: disable=import-outside-toplevel
    from publishing.terrain import decode_rgb  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtGui import QImage  # pylint: disable=import-outside-toplevel
    release = site["result"].release.release_dir
    manifest = json.load(open(os.path.join(release, "manifest.json"), encoding="utf-8"))
    terrain = manifest["terrain"]
    assert terrain["href"] == "data/terrain.pmtiles" and terrain["encoding"] == "mapbox"
    assert terrain["minTileZoom"] == 9 and terrain["maxTileZoom"] == 14  # map from 11; 10 m DEM at 47°
    heights = []
    with open_pmtiles(os.path.join(release, "data", "terrain.pmtiles")) as archive:
        for (z, x, y), data in archive.tiles():
            if z != 14:
                continue
            image = QImage.fromData(data, "PNG")
            colour = image.pixelColor(128, 128)
            heights.append(decode_rgb(colour.red(), colour.green(), colour.blue()))
    assert heights and 95 <= min(heights) and max(heights) <= 305, heights
    layer = next(l for l in manifest["layers"] if l.get("heightField"))
    assert layer["heightField"] == "terulet"


def test_3d_view_buildings_relief_and_profile(site, tmp_path):
    actions = [{"eval": """
      const v = q2vtViewer, m = v.map, d3 = v.threeD;
      const extrusions = m.getStyle().layers.filter((l) => l.type === 'fill-extrusion');
      const before = { pitch: m.getPitch(), terrain: !!m.getTerrain(),
                       vis: extrusions.map((l) => m.getLayoutProperty(l.id, 'visibility')),
                       hillshade: m.getLayer('q2vt_hillshade') ? m.getLayer('q2vt_hillshade').type : null,
                       button: !!document.querySelector('.q2vt-3d-btn') };
      document.querySelector('.q2vt-3d-btn').click();
      await new Promise((r) => setTimeout(r, 900));
      const on = { pitch: Math.round(m.getPitch()), terrain: !!m.getTerrain(),
                   vis: extrusions.map((l) => m.getLayoutProperty(l.id, 'visibility')),
                   height: JSON.stringify(m.getPaintProperty(extrusions[0].id, 'fill-extrusion-height')) };
      v.controls.state.setIn('layers', v.manifest.layers[0].id, false);
      await new Promise((r) => setTimeout(r, 100));
      const hidden = extrusions.map((l) => m.getLayoutProperty(l.id, 'visibility'));
      v.controls.state.setIn('layers', v.manifest.layers[0].id, true);
      document.querySelector('.q2vt-3d-btn').click();
      await new Promise((r) => setTimeout(r, 900));
      const off = { pitch: Math.round(m.getPitch()), terrain: !!m.getTerrain(),
                    vis: extrusions.map((l) => m.getLayoutProperty(l.id, 'visibility')) };
      // Elevation profile of a west-east line across the extent.
      const tools = v.controls.tools, c = m.getCenter();
      tools.start('distance');
      tools.points = [[c.lng - 0.012, c.lat], [c.lng + 0.012, c.lat]];
      tools.finish();
      const shown = !tools.profileButton.hidden;
      const stats = await tools.showProfile();
      return { before, on, hidden, off, shown, stats,
               facts: document.querySelector('.q2vt-profile-facts').textContent,
               chart: !!document.querySelector('.q2vt-profile-chart path.q2vt-profile-line') };
    """}]
    out = _run(site["url"], actions, tmp_path)[0]
    assert out["before"] == {"pitch": 0, "terrain": False, "vis": ["none"] * len(out["before"]["vis"]),
                             "hillshade": "hillshade", "button": True}
    assert out["before"]["vis"]  # one extrusion per fill style layer of the layer
    assert out["on"]["pitch"] == 55 and out["on"]["terrain"] and set(out["on"]["vis"]) == {"visible"}
    assert "terulet" in out["on"]["height"]
    assert set(out["hidden"]) == {"none"}  # follows the layer switch
    assert out["off"] == {"pitch": 0, "terrain": False, "vis": out["before"]["vis"]}
    stats = out["stats"]
    assert out["shown"] and out["chart"]
    assert 110 < stats["min"] < stats["max"] < 290 and stats["up"] > 80 and stats["down"] < 5, stats


@pytest.mark.parametrize("paper,portrait,size", [("a4-landscape", False, (186, 190)), ("a3-portrait", True, (281, 300))])
def test_map_extract_print_layout(site, tmp_path, paper, portrait, size):
    mm = 96 / 25.4
    actions = [{"eval": f"""
      const v = q2vtViewer, p = v.printer;
      p.paper = '{paper}'; p.scale = 2000;
      p.prepare('map');
      await new Promise((r) => setTimeout(r, 300));
      p.fill('map');
      const box = v.map.getContainer().getBoundingClientRect();
      const head = document.querySelector('#q2vt-print-sheet .q2vt-print-head');
      const out = {{ w: box.width, h: box.height, portrait: document.body.classList.contains('q2vt-print-portrait'),
        page: document.getElementById('q2vt-print-page').textContent,
        north: !!v.map.getContainer().querySelector('.q2vt-print-north svg'),
        kicker: head.querySelector('.q2vt-print-kicker').textContent,
        info: [...head.querySelectorAll('.q2vt-print-info dd')].map((n) => n.textContent),
        scale: head.querySelector('.q2vt-print-scale').textContent, printScale: p.printScale }};
      return out;
    """}]
    if SHOTS:
        actions += [{"media": "print"}, {"wait": 600}, {"screenshot": os.path.join(SHOTS, f"print-{paper}.png")}]
    actions.append({"eval": """
      q2vtViewer.printer.restore();
      return { left: document.querySelectorAll('.q2vt-print-north').length,
               printing: document.body.classList.contains('q2vt-printing') };
    """})
    out = _run(site["url"], actions, tmp_path, 1600, 1600)
    first, last = out[0], out[-1]
    assert abs(first["w"] - size[0] * mm) < 2 and abs(first["h"] - size[1] * mm) < 2, first
    assert first["portrait"] is portrait and paper.split("-")[0].upper() in first["page"]
    assert first["north"] and first["kicker"] == "Térképkivonat" and first["printScale"] == 2000
    assert first["info"] == ["Arló Község Önkormányzata", "12/2025. (X. 1.) önk. rendelet", "2025. 10. 01.", "2025. 09."]
    assert first["scale"] == "M 1:2000" or "2000" in first["scale"].replace(" ", "").replace("\u00a0", "")
    assert last == {"left": 0, "printing": False}


def test_variant_addons_are_installed(tmp_path):
    """resources/web_viewer/addons/*.mjs (a variant edition's add-ons; none on
    main) are published, listed in the manifest and installed by the viewer.
    With the default settings the overview map, the 3D view and drawing are
    off, even for a layer with a 3D height field."""
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "integration"))
    from test_publishing_pipeline import _parcels, _profile  # pylint: disable=import-error
    from q2vt_fixtures import reset_project
    from publishing.web_builder import WEB_VIEWER  # pylint: disable=import-outside-toplevel
    folder = os.path.join(WEB_VIEWER, "addons")
    created = not os.path.isdir(folder)
    probe = os.path.join(folder, "zz_probe.mjs")
    os.makedirs(folder, exist_ok=True)
    with open(probe, "w", encoding="utf-8") as handle:
        handle.write("export function install(parts) { window.q2vtProbe = { identify: !!parts.identify, "
                     "map: !!parts.map, title: parts.manifest.title }; }\n")
    try:
        parcels = _parcels(str(tmp_path / "parcels.gpkg"))
        project = reset_project()
        project.addMapLayer(parcels)
        profile = _profile(parcels, tmp_path)
        profile.layers[0].height_field = "terulet"
        result = export_local(project, profile, EXTENT)
    finally:
        os.remove(probe)
        if created:
            os.rmdir(folder)
    manifest = json.load(open(os.path.join(result.release.release_dir, "manifest.json"), encoding="utf-8"))
    assert manifest["addons"] == ["assets/addons/zz_probe.mjs"]
    interaction = manifest["interaction"]
    assert not (interaction["overviewMap"] or interaction["threeD"] or interaction["drawing"])
    assert manifest["tools"]["draw"] is False
    with PreviewServer(os.path.dirname(result.publication_dir)) as server:
        out = _run(server.url(f"{profile.slug}/index.html"), [{"eval": """
          const v = q2vtViewer;
          return { probe: window.q2vtProbe, button: !!document.querySelector('.q2vt-3d-btn'),
                   overview: !!document.querySelector('.q2vt-overview'), draw: !!v.draw,
                   extrusions: v.map.getStyle().layers.filter((l) => l.type === 'fill-extrusion').length };
        """}], tmp_path)[0]
    assert out == {"probe": {"identify": True, "map": True, "title": "Arló teszt"}, "button": False,
                   "overview": False, "draw": False, "extrusions": 0}


def test_viewer_pure_helpers_of_4_25(tmp_path):
    """Node: 3D copies skip transparent and hatch fills and stacked symbol
    layers; a drawing too long for a link is refused; the zone code key."""
    script = tmp_path / "helpers.mjs"
    viewer = os.path.join(os.path.dirname(os.path.dirname(HERE)), "resources", "web_viewer")
    script.write_text(f"""
      const {{ extrusionLayers }} = await import({json.dumps(os.path.join(viewer, "threed.mjs"))});
      const {{ encodeDrawing, MAX_LINK, COLORS }} = await import({json.dumps(os.path.join(viewer, "draw.mjs"))});
      const {{ zoneKey }} = await import({json.dumps(os.path.join(viewer, "parcel_report.mjs"))});
      const fill = (id, paint, extra = {{}}) => ({{ id, type: "fill", source: "s", "source-layer": "l", paint, ...extra }});
      const style = {{ layers: [
        fill("solid", {{ "fill-color": "#ff0000" }}), fill("solid2", {{ "fill-color": "#00ff00" }}),
        fill("clear", {{ "fill-color": "rgba(0, 0, 0, 0)" }}), fill("hatch", {{ "fill-pattern": "x" }}),
        fill("far", {{ "fill-color": "#0000ff" }}, {{ minzoom: 16 }}), fill("other", {{ "fill-color": "#123456" }}) ] }};
      const manifest = {{ layers: [{{ id: "b", heightField: "h", geometry: "polygon" }}],
        components: [{{ layerId: "b", styleLayerIds: ["clear", "hatch", "solid", "solid2", "far"] }},
                     {{ layerId: "b", styleLayerIds: ["other"] }}] }};
      const many = Array.from({{ length: 150 }}, (_, i) => ({{ kind: "text", coords: [[19 + i / 1000, 47]],
        color: COLORS[0], text: "Hosszú megjegyzés a tervezett útról ".repeat(3) }}));
      console.log(JSON.stringify({{ ids: extrusionLayers(style, manifest).map((l) => l.metadata["q2vt:3d-of"]),
        long: encodeDrawing(many).length > MAX_LINK, keys: [zoneKey(12), zoneKey(" Lke-1 "), zoneKey(null)] }}));
    """)
    run = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout.strip().splitlines()[-1])
    assert out["ids"] == ["solid", "far", "other"]
    assert out["long"] and out["keys"] == ["12", "Lke-1", ""]
