**QGIS2VectorTiles 4.7.4 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### The object storage keys travel with the settings file
**Export settings to a file…** now also writes the R2 / S3 keys of the destination: the Access Key ID and the Secret Access Key. They come from the keys pasted in the window, or from the saved QGIS configuration, which may ask for the QGIS master password once.

**The file holds the secret in plain text, at the owner's request.** Keep it private: whoever has the file can write to the bucket. A local-only destination exports no keys.

**Import settings from a file…** puts the keys back:
- At once as the keys of this session, so publishing works right away.
- The plugin also saves them encrypted in the QGIS authentication database and selects that configuration, so they stay after a restart. QGIS may ask for, or ask you to set, its master password. If you cancel, the keys stay valid for the open window only.

The profile saved in the project still never contains the keys.

| Run | Result |
|---|---|
| `pytest tests/integration/test_publish_dialog_extent.py` | 6 passed (new: an R2 destination's keys are written to the file, outside the profile; imported into another project they are the keys in use) |
| Publish window and profile suites | all passed |
