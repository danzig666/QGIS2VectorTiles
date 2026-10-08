"""hu-hesz edition: the zone regulations (HÉSZ övezeti előírások table, found
by the preset) show in a zone's popup and in the parcel report, the decree
reference as a link."""

import json
import os
import subprocess
import sys

import pytest
from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry, QgsRectangle

from publishing.controller import export_local
from publishing.models import LayerConfig, PublicationProfile
from publishing.preview_server import PreviewServer
from publishing.provenance import layer_logical_id

HERE = os.path.dirname(os.path.abspath(__file__))
DECREE = "https://njt.hu/rendelet/12-2025-arlo#6"


def _run(url, actions, tmp_path, width=1280, height=820):
    path = tmp_path / f"actions_{abs(hash(json.dumps(actions)))}.json"
    path.write_text(json.dumps(actions))
    run = subprocess.run(["node", "interact.mjs", url, str(path), str(width), str(height)],
                         capture_output=True, text=True, cwd=HERE, timeout=300)
    assert run.returncode == 0, run.stderr[-3000:]
    out = json.loads(run.stdout.strip().splitlines()[-1])
    assert not out["pageErrors"], out
    return out["results"]


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "integration"))
    from test_publishing_parcel_report import EOV, X, Y, box, layer  # pylint: disable=import-error
    from q2vt_fixtures import reset_project
    from publishing.presets import hu_hesz
    base = tmp_path_factory.mktemp("zonerules")
    project = reset_project()
    parcels = layer("Polygon", "Földrészletek", ["hrsz"], [(("100/1",), box(0, 0, 100, 40))], base / "parcels.gpkg")
    zoning = layer("Polygon", "Szabályozás színek", ["szab_ov"],
                   [(("Lke-1",), box(0, 0, 70, 40)), (("Gksz",), box(70, -60, 150, 40))])
    table = layer("None", "HÉSZ övezeti előírások", ["szab_ov", "beep_szaz", "max_mag", "hivatkozas"],
                  [(("Lke-1", "30", "7,5", DECREE), QgsGeometry()), (("Gksz", "50", "12", DECREE), QgsGeometry())])
    # The full texts (4.26): only for Gksz.
    texts = layer("None", "HÉSZ övezeti előírások szövege", ["szab_ov", "eloiras_html"],
                  [(("Gksz", "<h3>Az övezet saját előírásai</h3><p>(2) A Gksz övezetben raktár is lehet.</p>"),
                    QgsGeometry())])
    for item in (parcels, zoning, table, texts):
        project.addMapLayer(item)
    profile = PublicationProfile(title="Övezetek", slug="ovezetek", locale="hu")
    profile.view.min_zoom, profile.view.max_zoom = 14, 15
    profile.output.local_directory = str(base / "out")
    profile.layers = [LayerConfig(parcels.id()), LayerConfig(zoning.id())]
    notes = hu_hesz.apply(project, profile)
    assert any("Övezeti előírások" in note for note in notes), notes
    to_web = QgsCoordinateTransform(QgsCoordinateReferenceSystem(EOV), QgsCoordinateReferenceSystem("EPSG:3857"),
                                    project.transformContext())
    extent = to_web.transformBoundingBox(QgsRectangle(X - 50, Y - 100, X + 200, Y + 100))
    result = export_local(project, profile, extent)
    publication = result.publication_dir
    to_wgs = QgsCoordinateTransform(QgsCoordinateReferenceSystem(EOV), QgsCoordinateReferenceSystem("EPSG:4326"),
                                    project.transformContext())
    zone_point = to_wgs.transform(X + 110, Y - 30)  # Gksz, outside the parcel
    parcel_point = to_wgs.transform(X + 30, Y + 20)
    with PreviewServer(os.path.dirname(publication)) as server:
        yield {"url": server.url(f"{os.path.basename(publication)}/index.html"), "profile": profile,
               "zoning": layer_logical_id(zoning.id()), "zone": [zone_point.x(), zone_point.y()],
               "parcel": [parcel_point.x(), parcel_point.y()]}


def test_preset_found_the_regulation_table(site):
    info = site["profile"].parcel_info
    assert info.regulation_code_field == "szab_ov"
    assert [(f.field, f.alias, f.type) for f in info.regulation_fields] == [
        ("beep_szaz", "Legnagyobb beépítettség (%)", "string"), ("max_mag", "Legnagyobb épületmagasság (m)", "string"),
        ("hivatkozas", "HÉSZ hivatkozás", "url")]
    zoning = next(c for c in site["profile"].layers if c.popup_fields)
    assert zoning.popup_fields[0].field == "szab_ov"  # the popup knows its zone code


def test_zone_popup_and_parcel_report_list_the_regulations(site, tmp_path):
    out = _run(site["url"], [
        {"eval": "q2vtViewer.map.jumpTo({ center: %s, zoom: 18 }); return 1;" % json.dumps(site["zone"])},
        {"idle": True},
        {"clickLngLat": site["zone"]}, {"wait": 900},
        {"eval": """
          const popup = document.querySelector('.maplibregl-popup .q2vt-zone-rules');
          const full = popup && popup.querySelector('details.q2vt-rich-block');
          if (full) { full.open = true; await new Promise((r) => setTimeout(r, 600)); }
          return popup && { title: popup.querySelector('h4').textContent,
            full: full && [full.querySelector('summary').textContent, full.querySelector('.q2vt-rich p').textContent],
            rows: [...popup.querySelectorAll('tr')].map((r) => [r.querySelector('th').textContent, r.querySelector('td').textContent]),
            link: popup.querySelector('a') && popup.querySelector('a').href };
        """},
        {"eval": "document.querySelector('.maplibregl-popup-close-button').click(); "
                  "q2vtViewer.map.jumpTo({ center: %s }); return 1;" % json.dumps(site["parcel"])}, {"idle": True},
        {"clickLngLat": site["parcel"]}, {"wait": 900},
        {"eval": """
          const pane = document.getElementById('q2vt-pane-parcel');
          return { links: [...pane.querySelectorAll('.q2vt-pr-zone a')].map((a) => a.href),
                   facts: [...pane.querySelectorAll('.q2vt-pr-zone dt')].map((d) => d.textContent) };
        """},
    ], tmp_path)
    popup, report = out[1], out[3]
    assert popup["title"] == "A(z) Gksz övezet előírásai"
    assert popup["rows"] == [["Legnagyobb beépítettség (%)", "50"], ["Legnagyobb épületmagasság (m)", "12"],
                             ["HÉSZ hivatkozás", DECREE]]
    assert popup["link"] == DECREE
    assert popup["full"] == ["A(z) Gksz övezet teljes előírásai", "(2) A Gksz övezetben raktár is lehet."]
    assert report["links"] == [DECREE, DECREE] and "Legnagyobb beépítettség (%)" in report["facts"], report
