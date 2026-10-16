**QWebMap 4.42.1**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)


### Fixed
Found by a full review of the code:
- **Browser Back button:** each history entry records how many things were open, so Back over several steps, Forward onto a closed popup and links pasted into the address bar all behave; nothing is left half-closed.
- **Outdated results:** a slow feature lookup, parcel report, search, basemap switch or Street View coverage load no longer shows its result after the visitor has moved on.
- **Drop shadows** (QGIS effect) were offset in the wrong direction; **label shadows** now follow zoom-dependent offsets, and point labels with several candidate positions are reported instead of getting a misplaced shadow.
- Animated frames keep the layer opacity; diagram rules no longer disturb the render order; layers with no symbols (null renderer) are not reported; the text wrap width follows the font.
- **Publish window:** publishing requires a review of the current settings (a change after the review asks for a new one); closing the project while the window is open no longer writes settings into the next project; closing during a running task is handled.
- **Local preview** answers only requests addressed to localhost and sends no CORS headers.
- **Exports:** cancelled or failed tiling runs remove their temporary folders and close their pipes; files of crashed parallel workers are removed; the tile size estimate samples by feature count.
- **Uploads:** an S3/R2 upload that hits a conflict checks whether the object is already there; batch deletes report errors.
- Local flat exports validate their file list before removing anything; HTML regulation texts with unusual markup are parsed safely.
