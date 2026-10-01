"""Publication profiles (PUB-02): round trip, migration, validation, no
secrets, disclosure review fingerprint, JSON Schema agreement."""

import json
import os

import pytest

from publishing.errors import PublishingError
from publishing.models import (FilterField, LayerConfig, PopupField, PublicationProfile, slugify)
from publishing.profile import (disclosure_fingerprint, dumps, load_profile, needs_review,
                                normalize_prefix, publication_prefix)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCHEMAS = os.path.join(ROOT, "schemas", "publishing")


def _profile():
    profile = PublicationProfile(title="Arló településrendezési terv", slug="arlo-terv")
    profile.layers = [
        LayerConfig("parcels_1", popup_fields=[PopupField("hrsz", "Hrsz.")],
                    search_fields=["hrsz"], key_fields=["hrsz"],
                    filter_fields=[FilterField("zone", "values")]),
        LayerConfig("zones_2", initially_visible=False),
        LayerConfig("private_3", included=False, initially_visible=False),
    ]
    return profile


def test_round_trip_is_stable():
    profile = _profile()
    text = dumps(profile)
    again = load_profile(text)
    assert dumps(again) == text
    assert again.layers[0].popup_fields[0].alias == "Hrsz."
    assert again.included_layer_ids() == ["parcels_1", "zones_2"]  # hidden but included
    assert json.loads(text)["layers"][1]["initiallyVisible"] is False


def test_defaults_publish_pmtiles_vector_only():
    profile = PublicationProfile()
    assert profile.output.archive == "pmtiles" and profile.vector_only is True
    assert profile.destination.kind == "local" and profile.locale == "hu"


def test_profiles_never_hold_secrets():
    data = _profile().to_dict()
    data["destination"]["secretAccessKey"] = "abc"
    with pytest.raises(PublishingError, match="secret"):
        load_profile(data)
    data = _profile().to_dict()
    data["destination"]["credentialRef"] = "a1b2c3d"  # a reference is fine
    assert load_profile(data).destination.credential_ref == "a1b2c3d"


@pytest.mark.parametrize("change, message", [
    (lambda d: d.update(slug="Nem jó!"), "slug"),
    (lambda d: d.update(vectorOnly=False), "vectorOnly"),
    (lambda d: d["output"].update(archive="png"), "archive"),
    (lambda d: d["layers"][0].update(opacity=2), "opacity"),
    (lambda d: d["layers"][2].update(initiallyVisible=True), "excluded"),
    (lambda d: d.update(unknownKey=1), "unknown"),
    (lambda d: d["destination"].update(kind="r2"), "bucket"),
    (lambda d: d["view"].update(minZoom=12, maxZoom=3), "minZoom"),
    (lambda d: d["layers"][0]["popupFields"][0].update(type="html"), "type"),
])
def test_malformed_profiles_fail_clearly(change, message):
    data = _profile().to_dict()
    change(data)
    with pytest.raises(PublishingError) as error:
        load_profile(data)
    assert error.value.code == "Q2VT_PUB_PROFILE_INVALID" and message in error.value.message


def test_schema_versions():
    data = _profile().to_dict()
    data["schemaVersion"] = 2
    with pytest.raises(PublishingError) as error:
        load_profile(data)
    assert error.value.code == "Q2VT_PUB_SCHEMA_UNSUPPORTED"
    del data["schemaVersion"]
    with pytest.raises(PublishingError):
        load_profile(data)


def test_web_destinations_need_https_and_pmtiles():
    data = _profile().to_dict()
    data["destination"].update(kind="r2", accountId="acc", bucket="maps",
                               publicBaseUrl="http://maps.example.com")
    with pytest.raises(PublishingError, match="https"):
        load_profile(data)
    data["destination"]["publicBaseUrl"] = "https://maps.example.com"
    data["output"]["archive"] = "mbtiles"
    with pytest.raises(PublishingError, match="PMTiles"):
        load_profile(data)
    data["output"]["archive"] = "both"
    assert load_profile(data).destination.kind == "r2"


def test_disclosure_changes_require_review():
    profile = _profile()
    assert needs_review(profile)
    profile.approval.fingerprint = disclosure_fingerprint(profile)
    assert not needs_review(profile)
    profile.title = "Új cím"  # not a disclosure change
    profile.layers[1].initially_visible = True
    assert not needs_review(profile)
    profile.layers[0].popup_fields.append(PopupField("owner"))
    assert needs_review(profile)


def test_prefixes_and_slugs():
    assert normalize_prefix("/maps//arlo/") == "maps/arlo"
    with pytest.raises(PublishingError):
        normalize_prefix("maps/../other")
    with pytest.raises(PublishingError):
        normalize_prefix("térkép")
    profile = _profile()
    assert publication_prefix(profile) == "maps/arlo-terv"
    profile.destination.prefix = "public/arlo"
    assert publication_prefix(profile) == "public/arlo"
    assert slugify("Arló Településrendezési Terv – 2026") == "arlo-telepulesrendezesi-terv-2026"
    assert slugify("!!!") == "map"


def test_profile_matches_its_json_schema():
    jsonschema = pytest.importorskip("jsonschema")
    with open(os.path.join(SCHEMAS, "profile-v1.schema.json"), encoding="utf-8") as handle:
        schema = json.load(handle)
    jsonschema.validate(_profile().to_dict(), schema)
    bad = _profile().to_dict()
    bad["vectorOnly"] = False
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


def test_all_schemas_are_valid_draft_2020_12():
    jsonschema = pytest.importorskip("jsonschema")
    for name in os.listdir(SCHEMAS):
        with open(os.path.join(SCHEMAS, name), encoding="utf-8") as handle:
            jsonschema.Draft202012Validator.check_schema(json.load(handle))


def test_plain_http_only_for_local_test_servers():
    data = _profile().to_dict()
    data["destination"].update(kind="s3", endpoint="http://127.0.0.1:9000", bucket="maps",
                               publicBaseUrl="http://localhost:9001")
    assert load_profile(data).destination.endpoint == "http://127.0.0.1:9000"
    data["destination"]["publicBaseUrl"] = "http://maps.example.com"
    with pytest.raises(PublishingError, match="https"):
        load_profile(data)


def test_locked_layers_groups_themes_basemap_and_raster_settings():
    from publishing.models import GroupConfig
    profile = _profile()
    profile.layers[0].toggleable = False
    profile.layers[0].raster_format, profile.layers[0].raster_max_zoom = "webp", 18
    profile.groups = [GroupConfig(["Alaptérkép"], toggleable=False), GroupConfig(["Szabályozás", "Övezet"])]
    profile.themes.names, profile.themes.initial = ["Terv", "Alaptérkép"], "Terv"
    profile.basemap.kind, profile.basemap.flavors, profile.basemap.initial = "protomaps", ["light", "dark"], "dark"
    profile.accent_color = "#0f766e"
    again = load_profile(dumps(profile))
    assert dumps(again) == dumps(profile)
    assert again.group(["Alaptérkép"]).toggleable is False and again.layers[0].raster_format == "webp"
    assert again.basemap.flavors == ["light", "dark"] and again.themes.initial == "Terv"
    jsonschema = pytest.importorskip("jsonschema")
    with open(os.path.join(SCHEMAS, "profile-v1.schema.json"), encoding="utf-8") as handle:
        jsonschema.validate(profile.to_dict(), json.load(handle))


@pytest.mark.parametrize("change, message", [
    (lambda d: d["layers"][1].update(toggleable=False), "switched off"),
    (lambda d: d.update(groups=[{"path": ["A"], "toggleable": False, "initiallyVisible": False}]), "switched off"),
    (lambda d: d.update(groups=[{"path": ["A"]}, {"path": ["A"]}]), "duplicate"),
    (lambda d: d["layers"][0].update(rasterFormat="tiff"), "rasterFormat"),
    (lambda d: d["layers"][0].update(rasterMinZoom=15, rasterMaxZoom=12), "rasterMinZoom"),
    (lambda d: d["themes"].update(names=["A"], initial="B"), "themes.initial"),
    (lambda d: d["basemap"].update(kind="protomaps", flavors=["neon"]), "basemap.flavors"),
    (lambda d: d["basemap"].update(kind="protomaps", initial="dark"), "basemap.initial"),
    (lambda d: d["basemap"].update(kind="protomaps", source="http://example.com/x.pmtiles"), "basemap.source"),
    (lambda d: d.update(accentColor="blue"), "accentColor"),
])
def test_new_settings_fail_clearly(change, message):
    data = _profile().to_dict()
    data["layers"][1]["initiallyVisible"] = False
    change(data)
    with pytest.raises(PublishingError) as error:
        load_profile(data)
    assert message in error.value.message
