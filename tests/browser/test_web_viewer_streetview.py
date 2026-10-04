"""Google Street View in the viewer (Google stubbed: no network, no key).
The button switches the mode on: the coverage overlay (Map Tiles API
session) is added, a tap opens the nearest panorama looking toward the
tapped point, the viewpoint cone follows the panorama, a tap with no
panorama says so, the parcel report stays closed meanwhile, and closing
gives the map back. Phones: the panel sheet closes and the panorama takes
the top of the screen with the viewpoint below it."""

import json
import os
import sys

import pytest

from publishing.preview_server import PreviewServer
from publishing.models import PublicationProfile
from publishing.web_builder import _interaction, _render_index, WEB_VIEWER

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_web_viewer_parcel import _run  # noqa: E402  pylint: disable=wrong-import-position

# window.google.maps with the calls the viewer makes; panoramas sit at
# window.__svPano (none when it is null), fetch answers createSession.
STUB = """
const calls = (window.__sv = { requests: [], panoramas: [], session: null });
window.__svPano = null;
class LatLng { constructor(a, b) { this.a = a; this.b = b; } lat() { return this.a; } lng() { return this.b; } }
class Panorama {
  constructor(node, options) { Object.assign(this, { node, options, pov: options.pov, listeners: {} });
    this.position = null; node.textContent = 'PANORAMA'; calls.panoramas.push(this); }
  addListener(name, fn) { this.listeners[name] = fn; }
  getPov() { return this.pov; }
  setPov(pov) { this.pov = pov; this.listeners.pov_changed?.(); }
  setPano(pano) { this.options.pano = pano; }
  setVisible() {}
  getPosition() { return this.position; }
}
window.google = { maps: {
  StreetViewService: class { getPanorama(request) { calls.requests.push(request);
    const p = window.__svPano;
    if (!p || request.radius < p.radius) return Promise.reject(new Error('ZERO_RESULTS'));
    return Promise.resolve({ data: { location: { pano: p.id, latLng: new LatLng(p.lat, p.lng) } } }); } },
  StreetViewPanorama: Panorama,
  StreetViewPreference: { NEAREST: 'nearest' }, StreetViewSource: { OUTDOOR: 'outdoor' },
  event: { trigger() {} } } };
const realFetch = window.fetch.bind(window);
window.fetch = (url, options) => {
  if (String(url).startsWith('https://tile.googleapis.com/v1/createSession')) {
    calls.session = { url: String(url), body: JSON.parse(options.body) };
    if (window.__svRefuse) return Promise.resolve(new Response(JSON.stringify({ error: { code: 403,
      message: 'This API key is not authorized to use this service or API.' } }), { status: 403 }));
    return Promise.resolve(new Response(JSON.stringify({ session: 'S1' }), { status: 200 }));
  }
  return realFetch(url, options);
};
return 1;
"""

STATE = """const sv = q2vtViewer.streetViewControl, map = q2vtViewer.map;
  const panel = document.querySelector('.q2vt-sv-panel'), hint = document.querySelector('.q2vt-sv-hint');
  const box = (n) => { if (!n) return null; const r = n.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; };
  const marker = document.querySelector('.q2vt-sv-marker');
  const src = map.getSource('q2vt_streetview_coverage');
  return { pressed: document.querySelector('.q2vt-sv-button')?.getAttribute('aria-pressed'),
    active: sv.active, hint: hint?.textContent || null, alert: !!hint?.classList.contains('q2vt-sv-alert'),
    coverage: map.getLayer('q2vt_streetview_coverage') ? map.getLayoutProperty('q2vt_streetview_coverage', 'visibility') || 'visible' : null,
    tiles: src ? src.tiles : null, session: window.__sv.session,
    panoramas: window.__sv.panoramas.length, pano: window.__sv.panoramas.at(-1)?.options.pano || null,
    heading: sv.heading, rotation: sv.marker ? sv.marker.getRotation() : null,
    marker: box(marker), panel: box(panel), panelOpen: !document.getElementById('q2vt-panel').hidden,
    radii: window.__sv.requests.map((r) => r.radius),
    parcel: document.querySelector('#q2vt-pane-parcel h2')?.textContent || null,
    height: innerHeight, width: innerWidth };"""


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "integration"))
    from test_publishing_parcel_report import build_site  # pylint: disable=import-error
    built = build_site(tmp_path_factory.mktemp("streetview"), street_view=True, google_api_key=" TESTKEY ")
    publication = os.path.dirname(os.path.dirname(built["rel"]))
    with PreviewServer(os.path.dirname(publication)) as server:
        built["url"] = server.url(f"{os.path.basename(publication)}/index.html")
        yield built


def _anchor(site):
    return site["records"]["100/1"]["p"][0]["x"]


def _start(site):
    return [{"eval": "q2vtViewer.map.jumpTo({ center: %s, zoom: 18 }); return 1;" % json.dumps(_anchor(site))},
            {"idle": True}, {"eval": STUB},
            {"eval": "document.querySelector('.q2vt-sv-button').click(); await new Promise((r) => setTimeout(r, 300)); return 1;"}]


def _pano_at(lng, lat, pano="P1", radius=0):
    return {"eval": "window.__svPano = %s; return 1;" % json.dumps(
        {"id": pano, "lng": lng, "lat": lat, "radius": radius})}


def test_page_and_manifest_allow_google(site):
    interaction = site["manifest"]["interaction"]
    assert interaction["streetView"] is True and interaction["googleApiKey"] == "TESTKEY"
    with open(os.path.join(site["rel"], "index.html"), encoding="utf-8") as handle:
        page = handle.read()
    assert "script-src 'self' https://*.googleapis.com" in page and "frame-src https://*.google.com" in page
    assert '<meta name="referrer" content="strict-origin-when-cross-origin">' in page


def test_key_is_published_only_with_street_view():
    profile = PublicationProfile(title="T", slug="t")
    profile.interaction.google_api_key = "SECRET"
    assert "googleApiKey" not in _interaction(profile) and _interaction(profile)["streetView"] is False
    profile.interaction.street_view, profile.interaction.google_api_key = True, ""
    assert _interaction(profile)["streetView"] is False
    plain = _render_index(os.path.join(WEB_VIEWER, "index.html"), "T")
    assert "google" not in plain and '<meta name="referrer" content="no-referrer">' in plain


def test_tap_opens_the_panorama_facing_the_tapped_point(site, tmp_path):
    lng, lat = _anchor(site)
    results = _run(site["url"], _start(site) + [
        {"eval": STATE},
        # The panorama 25 m south of the tap: it looks north.
        _pano_at(lng, lat - 0.000225), {"clickLngLat": [lng, lat]}, {"wait": 500}, {"eval": STATE},
        # Turning the panorama turns the cone on the map.
        {"eval": "window.__sv.panoramas[0].setPov({ heading: 120, pitch: 0 }); return 1;"}, {"eval": STATE},
        # A panorama east of the next tap, 80 m out: found on the second radius, looks west.
        _pano_at(lng + 0.0009, lat, "P2", 50), {"clickLngLat": [lng, lat]}, {"wait": 500}, {"eval": STATE},
        # Nowhere near: said at once, the panorama stays.
        {"eval": "window.__svPano = null; window.__sv.requests.length = 0; return 1;"},
        {"clickLngLat": [lng, lat]}, {"wait": 400}, {"eval": STATE},
        # Closing gives the map back: the next tap opens the parcel report.
        {"eval": "document.querySelector('.q2vt-sv-panel button[aria-label=\"Utcakép bezárása\"]').click(); return 1;"},
        {"clickLngLat": [lng, lat]}, {"wait": 800}, {"eval": STATE},
    ], tmp_path)
    on, tapped, turned, moved, none, closed = results[3], results[5], results[7], results[9], results[11], results[13]
    assert on["pressed"] == "true" and on["coverage"] == "visible" and on["hint"].startswith("Koppintson arra")
    assert on["session"]["url"].endswith("key=TESTKEY")
    assert on["session"]["body"]["layerTypes"] == ["layerStreetview"] and on["session"]["body"]["overlay"] is True
    assert "session=S1" in on["tiles"][0] and "key=TESTKEY" in on["tiles"][0]
    assert tapped["panoramas"] == 1 and tapped["pano"] == "P1" and tapped["panel"] and tapped["marker"]
    assert min(tapped["heading"], 360 - tapped["heading"]) < 1 and tapped["parcel"] is None
    assert turned["rotation"] == 120
    assert moved["panoramas"] == 1 and moved["pano"] == "P2" and abs(moved["heading"] - 270) < 1
    assert moved["radii"][-2:] == [30, 80]
    assert none["alert"] and none["hint"].startswith("Itt a közelben nincs") and none["radii"] == [30, 80, 200]
    assert none["pano"] == "P2" and none["panel"]
    assert closed["pressed"] == "false" and closed["coverage"] == "none" and closed["panel"] is None
    assert closed["marker"] is None and closed["hint"] is None and closed["parcel"] == "Hrsz. 100/1"


def test_measuring_ends_street_view(site, tmp_path):
    results = _run(site["url"], _start(site) + [
        {"eval": "q2vtViewer.controls.tools.start('distance'); return 1;"}, {"eval": STATE},
    ], tmp_path)
    assert results[-1]["active"] is False and results[-1]["pressed"] == "false"


def test_phone_layout(site, tmp_path):
    lng, lat = _anchor(site)
    results = _run(site["url"], [{"eval": "document.getElementById('q2vt-menu')?.click(); return 1;"}]
                   + _start(site) + [
        {"eval": STATE}, _pano_at(lng, lat - 0.0002), {"clickLngLat": [lng, lat]}, {"wait": 800}, {"eval": STATE},
        {"eval": "document.querySelector('.q2vt-sv-panel .q2vt-icon-btn').click(); return 1;"}, {"eval": STATE},
    ], tmp_path, width=390, height=800)
    on, tapped, full = results[4], results[6], results[8]
    assert on["panelOpen"] is False  # the layer sheet makes room
    left, top, right, bottom = tapped["panel"]
    assert top <= 1 and left <= 1 and right >= 389 and 0.35 * 800 < bottom < 0.6 * 800
    assert tapped["marker"][1] > bottom  # the viewpoint stays in sight below the panorama
    assert full["panel"][3] >= 799  # full screen



def test_refused_coverage_says_why(site, tmp_path):
    """Map Tiles API refused (not enabled for the key): the visitor reads why the
    blue lines are missing; a tap still opens the nearest panorama."""
    lng, lat = _anchor(site)
    start = _start(site)
    start.insert(3, {"eval": "window.__svRefuse = true; return 1;"})
    results = _run(site["url"], start + [
        {"eval": STATE},
        _pano_at(lng, lat - 0.0002), {"clickLngLat": [lng, lat]}, {"wait": 500}, {"eval": STATE},
    ], tmp_path)
    refused, tapped = results[-3], results[-1]
    assert refused["coverage"] is None and refused["alert"]
    assert refused["hint"].startswith("A kék utcakép-vonalak nem jeleníthetők meg")
    assert "Map Tiles API" in refused["hint"]
    assert tapped["panoramas"] == 1 and tapped["pano"] == "P1"
