"""Publish the showcase project as a web map with the plugin, the way the
Publish window does (its settings set in code).

Usage: python3 export_showcase.py <work dir>
Reads <work dir>/showcase/showcase.qgs (from build_showcase.py) and writes
the publication to <work dir>/out/swellendam."""
import os
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [os.path.join(REPO, "tests")]
if len(sys.argv) != 2:
    sys.exit(__doc__)
WORK = os.path.abspath(sys.argv[1])

from q2vt_support import register_plugin_package  # noqa: E402

register_plugin_package()
from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform,  # noqa: E402
                       QgsProcessingFeedback, QgsProject, QgsRectangle)
from q2vt_plugin.src.gui import publication_profiles as store  # noqa: E402
from q2vt_plugin.src.publishing.controller import export_local  # noqa: E402
from q2vt_plugin.src.publishing.models import LayerConfig, PopupField, PublicationProfile  # noqa: E402

project = QgsProject.instance()
project.read(os.path.join(WORK, "showcase", "showcase.qgs"))
profile = PublicationProfile(title="Swellendam", slug="swellendam", locale="en")
profile.description = "A QGIS project published with QWebMap (QGIS training data, from OpenStreetMap)."
profile.view.min_zoom, profile.view.max_zoom = 11, 17
profile.output.local_directory = os.path.join(WORK, "out")
profile.interaction.measure = True
profile.accent_color = "#0f766e"
POPUPS = {"Roads": [PopupField("name", "Street"), PopupField("highway", "Road type")],
          "Schools": [PopupField("name", "School")],
          "Restaurants": [PopupField("name", "Restaurant"), PopupField("addr:street", "Street")],
          "Buildings": [PopupField("building", "Building")],
          "Land use": [PopupField("landuse", "Land use"), PopupField("name", "Name")],
          "Places": [PopupField("name", "Place"), PopupField("place", "Kind")],
          "Rivers": [PopupField("name", "River")],
          "Protected areas": [PopupField("name", "Name")],
          "Heritage walk": [PopupField("name", "Route")]}
SEARCH = {"Roads": ["name"], "Schools": ["name"], "Restaurants": ["name"], "Places": ["name"],
          "Rivers": ["name"], "Protected areas": ["name"]}
for node in project.layerTreeRoot().findLayers():
    layer = node.layer()
    config = LayerConfig(layer.id())
    config.popup_fields = [f for f in POPUPS.get(layer.name(), []) if f.field in layer.fields().names()]
    config.search_fields = SEARCH.get(layer.name(), [])
    config.key_fields = []
    config.snap = layer.name() in ("Buildings", "Roads")
    profile.layers.append(config)
to_project = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), project.crs(), project)
extent = to_project.transformBoundingBox(QgsRectangle(20.36, -34.085, 20.585, -33.975))
profile.view.extent = [extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()]

START = time.time()


class Feedback(QgsProcessingFeedback):
    def pushInfo(self, info):
        print(f"{time.time() - START:6.1f}", info, flush=True)

    def reportError(self, error, fatalError=False):
        print("ERROR", error, flush=True)


result = export_local(project, profile, extent, Feedback())
print("DONE", result.state, result.release.release_id, result.publication_dir, flush=True)
store.save_profile(project, profile)
project.write(os.path.join(WORK, "showcase", "showcase.qgs"))
