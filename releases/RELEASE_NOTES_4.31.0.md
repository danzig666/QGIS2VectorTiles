**QWebMap 4.31.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### New
- **Publish to your own server over SSH / SFTP** (*Destination* tab → *SSH / SFTP server*). The map goes straight into the folder you give, not versioned (no release folders, no rollback). Only new and changed files are uploaded, each to a temporary name and then renamed over the old one, `index.html` last, so visitors never see half a map. Files with the same names are replaced; your other files in the folder stay, and only files QWebMap uploaded there are ever deleted. Log in with a key file, ssh-agent or a **password** (also a key's passphrase), typed for the session or saved encrypted in QGIS (*Save in QGIS…*). **Test connection** logs in, creates the folder if needed and writes and deletes a test file. Uses the computer's OpenSSH client (built into Windows 10/11, macOS and Linux; a password needs OpenSSH 8.4 or newer). Paste `user@server:/folder` into the server field to fill the fields at once.

- **Drawing the extent by clicking** (*Map* tab → *Extent* → *Draw…*): click one corner, then the opposite one, as in QGIS's own rectangle tools; no need to hold the mouse button (dragging still works). Corners snap when QGIS snapping is on, the status bar says which corner comes next, right click or Esc cancels.

### Fixes: missing features, rules and labels
- **A rule whose expression fails on one feature** (for example text in a numeric field) is no longer dropped from the web map; like QGIS, the export skips just that value.
- **Nested rule-based renderers** (rules inside rules) no longer share ids, which stopped the whole web map from loading. A repeated style layer id is always renamed and reported.
- **Features whose data-defined value does not fit a number** (text in a label rotation field) were dropped with their labels. The value is now empty, and the label is drawn unrotated, as in QGIS.
- **Labels with a data-defined X/Y that is not a number** were lost. They are now placed normally, as in QGIS.
- **Empty data-defined values** (NULL) of label sizes, colours, texts and switches use the static value, as in QGIS (a NULL size made the label disappear).
- **Labels shown in upper case** from lower-case data were missing letters on the web.
- **Polygon labels "Using perimeter"** were not shown; they now follow the outline.
- **Categories on a field whose name looks like an expression** (for example `a/b`) matched nothing.
- **A feature drawn by two renderer rules** has one label again (it had one per rule).
- **Labels with empty text** no longer draw their background.

### Fixes: drawing
- **Drawing order:** overlapping lines and polygons of different categories or rules keep QGIS's feature order, also where lines of different rules meet at their ends (up to 5,000 re-ordered features and 8 levels of overlap per layer, in layers of up to 100,000 features; beyond that the layer keeps rule order and the fidelity report says so). Overlapping point markers stack in feature order.
- **Qt brush fills** (hatched, cross, dense patterns) were drawn as a solid colour that hid everything below; they now draw their pattern.
- **Layer opacity** (*Layer Rendering*) is converted (it was ignored).
- **Polygon outline bands in millimetres** are on the correct side and are buffered rings like QGIS's (no dark wedges at corners). **A fill's screen offset** moves its outline too.
- **Shapeburst distances in millimetres** keep their width on screen; **gradient fills of small features** keep their outer colours.
- **Wide lines in map units** no longer end in a step at tile edges.
- **Closed lines** (rings) join at their first vertex like Qt: no darker square at the start of a thick, semi-transparent outline.
- **Custom dash patterns with a 0-length element** no longer leave dots in the gaps.
- **Arrow fills:** the outline is drawn at QGIS's width (it was one pixel).
- **Markers placed along lines** point along the line again in projected coordinate systems; **hairline marker outlines** are one pixel wide; point-pattern dots are crisp.
- **Font markers** with an offset in map units and a scale limit are offset again (the text sat on its line).

### Fixes: labels
- **Label frames** wrap the text like QGIS (height from the font's ascent and descent, border centred on the frame's edge); frames with a map-unit border have QGIS's border width, also when the label size is data-defined.
- **Around-point labels** are at QGIS's distance from their point, try QGIS's positions in QGIS's order, keep clear of other labels, and small polygons keep theirs.
- **Line labels allowed above or below the line** are placed beside it, not on it.
- **Horizontal and Free polygon labels** keep clear of the web map's other labels; the viewer measures each character's own width, so labels that fit in QGIS are no longer dropped.
- **DemiBold, Medium and Light fonts** use the face QGIS draws; **repeat distances in map units** grow with the map; **letter-spaced curved labels** repeated along long lines are laid out per zoom as QGIS does.

### Viewer
- **Search works without popups:** the found feature is zoomed to and marked.
- **A shared link** shows what its sender saw (it was mixed with the visitor's remembered choices, and a link pasted into an open map tab was ignored).
- The *Labels switch* setting (*Interaction → Viewer*) is honoured.
- Popups no longer end with "Feature links of this layer only work in this version of the map".

| Run | Result |
|---|---|
| Tests (full suite, each file in its own process) | 812 passed, 5 skipped |
| Publish window, publishing and SSH tests after adding the new features (SSH end to end against a local OpenSSH server) | all passed |
| SSH password login by hand against a local OpenSSH server (password and PAM keyboard-interactive; password with spaces, quotes, & and accented letters) | publish, re-publish and wrong password as expected |
