**QWebMap 4.13.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Google Street View in the web map
Turn on *Street View (Google)* in the *Interaction* tab of the Publish Web Map window and paste your Google API key. Visitors get a Street View button under the zoom buttons:

1. Press it: **blue lines** show where Street View exists, so nobody taps in vain.
2. **Tap what you want to see** (a house, a corner): the nearest panorama opens, already **looking at that spot**.
3. Drag the picture to look around. The viewpoint and its direction are on the map; drag it to step elsewhere, or tap another place.

A tap where there is no Street View nearby says so at once. On phones the panorama takes the top of the screen with the map below; it can be enlarged to full screen.

**Your API key** is visible to anyone in the published page; that cannot be avoided. In Google Cloud:
- enable the **Maps JavaScript API** and the **Map Tiles API** (the blue lines);
- restrict the key to your site's address (**HTTP referrers**, e.g. `https://map.example.com/*`) and to these two APIs.

Google bills usage beyond its monthly free quota. Google's code loads only when a visitor presses the button.

| Run | Result |
|---|---|
| `pytest tests/browser/test_web_viewer_streetview.py` (new, Google stubbed) | 5 passed: coverage lines, the panorama looks toward the tapped point, the map cone follows the panorama, the "no Street View here" message, closing gives the map back, measuring and Street View exclude each other, phone layout |
| Publish dialog, profile, web builder, parcel and viewer tests | 59 passed |
