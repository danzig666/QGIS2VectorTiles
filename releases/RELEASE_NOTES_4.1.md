**QGIS2VectorTiles 4.1 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. This fork focuses on making the web map look like the QGIS map.

Install it in QGIS with *Plugins → Manage and Install Plugins → Install from ZIP* and choose `QGIS2VectorTilesFork-4.1.zip`.

### Now installs next to the official plugin
- The plugin is now called **QGIS2VectorTiles (fork)**. It installs into its own `QGIS2VectorTilesFork` folder and has its own Processing provider, so it no longer replaces the official QGIS2VectorTiles plugin.
- The plugin finds its viewer and server files in its own folder. It used to look in the official plugin's folder.
- If you installed 4.0: that version replaced the official plugin. Remove it once in *Manage and Install Plugins*, then reinstall the official plugin if you want both.

### Changes since 4.0

**Labels**
- The wrap character starts a new line, as in QGIS.
- Labels with HTML formatting are drawn formatted instead of showing the tags. `<sub>`/`<sup>` are drawn at 2/3 size, as in QGIS, and `<small>`, `<br>` and character entities are handled too.
- Label buffers (halos) now have the QGIS width. They used to be twice as wide, and buffers sized in % made white boxes.
- Multi-line labels keep their left/centre/right alignment. They used to always be centred.
- Line labels are drawn above or below the line when QGIS is set to do so.
- A line label without a repeat distance is drawn once, at the middle of the line, along it. It used to be repeated, or placed on the wrong segment.

**Symbols**
- Lines offset in map units follow QGIS at sharp corners. They used to cross themselves and bunch up.
- Wide pattern strokes, such as thick stripes, are cut exactly at the polygon edge. A data-defined stroke colour no longer turns them into icons drawn whole.
- Geometry generators that draw lines, such as dimension lines built with `segments_to_lines()`, are exported like the lines they draw:
  - marker positions, arrows, ticks and offsets are exact;
  - expressions that read the generated geometry, such as a segment's length for a label, get the value of each generated part.
- End markers with their own rotation, such as dimension arrows, sit exactly on the offset line at every zoom.

**Tools (not in the zip)**
- `tools/gallery/compare_page.py` builds a self-contained comparison page:
  - side by side, overlay (red: only QGIS draws it, cyan: only the browser), swipe and blink views;
  - 1×/2×/4× zoom with sharp pixels.

  Every gallery run also writes this page as `images/compare.html`.
