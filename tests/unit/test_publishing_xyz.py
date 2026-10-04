"""Web basemaps (XYZ tile addresses): profile round trip and validation, tile
URL templates ({s}, {-y}) and the origins the page may load from."""

import json

import pytest

from publishing.models import PublicationProfile, XyzBasemap
from publishing.profile import dumps, load_profile, validate
from publishing.xyz import manifest_entries, origins, tile_urls


def _profile(**basemap):
    profile = PublicationProfile(title="Teszt", slug="teszt", locale="hu")
    for key, value in basemap.items():
        setattr(profile.basemap, key, value)
    return profile


def test_round_trip_and_initial_web_basemap():
    profile = _profile(xyz=[XyzBasemap("OSM", "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                                       "© OpenStreetMap contributors", 0, 19)], initial="xyz-1")
    assert validate(profile) == []
    data = json.loads(dumps(profile))
    assert data["basemap"]["xyz"][0] == {"title": "OSM", "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                                         "attribution": "© OpenStreetMap contributors", "minZoom": 0, "maxZoom": 19}
    again = load_profile(dumps(profile))
    assert again.basemap.xyz[0] == profile.basemap.xyz[0] and again.basemap.initial == "xyz-1"


@pytest.mark.parametrize("url, problem", [
    ("http://tile.example.com/{z}/{x}/{y}.png", "https://"),
    ("https://tile.example.com/{z}/{x}.png", "{y}"),
    ("https://mt{n}.example.com/{z}/{x}/{y}.png", "server name"),
])
def test_bad_addresses_are_refused(url, problem):
    errors = validate(_profile(xyz=[XyzBasemap("X", url)]))
    assert any(problem in e for e in errors), errors


def test_titles_must_differ_and_initial_must_exist():
    entry = XyzBasemap("Same", "https://a.example.com/{z}/{x}/{y}.png")
    assert any("twice" in e for e in validate(_profile(xyz=[entry, entry])))
    assert any("basemap.initial" in e for e in validate(_profile(xyz=[entry], initial="xyz-2")))


def test_templates_and_origins():
    tiles, scheme = tile_urls("https://{s}.tile.example.com/{z}/{x}/{-y}.png")
    assert scheme == "tms" and tiles == [f"https://{s}.tile.example.com/{{z}}/{{x}}/{{y}}.png" for s in "abc"]
    assert origins(tiles) == {f"https://{s}.tile.example.com" for s in "abc"}
    entries = manifest_entries([XyzBasemap("A", "https://a.example.com:8443/{z}/{x}/{y}.png", "© A", 3, 18)])
    assert entries == [{"id": "xyz-1", "title": "A", "tiles": ["https://a.example.com:8443/{z}/{x}/{y}.png"],
                        "scheme": "xyz", "attribution": "© A", "minzoom": 3, "maxzoom": 18, "tileSize": 256}]
    assert origins(entries[0]["tiles"]) == {"https://a.example.com:8443"}
