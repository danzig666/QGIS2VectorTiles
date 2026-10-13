**QWebMap 4.37.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Web map
- **"My location" button is an option, off by default**: until now every web map had a location button. It is now an option in the Publish window (Viewer tab: "My location button"), off by default; maps published before keep no button once published again.
- **Outside the map's area**: when the visitor's location is outside the area of the map, the button only shows "Your location is outside this map's area." (in the map's language) and the map stays where it is, instead of flying off to an empty place. Inside the area the map goes there and marks the location with a dot.
