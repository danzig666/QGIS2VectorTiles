"""
Stable error codes of the publishing workflow (``Q2VT_PUB_*``).

Messages are user-facing; ``detail`` may hold private context (paths) and is
redacted before it reaches any public file.
"""

from typing import Optional

CODES = {
    "Q2VT_PUB_NOT_MVT": "The tile archive does not contain Mapbox Vector Tiles.",
    "Q2VT_PUB_RASTER_SOURCE": "The style uses a raster/image source; vector-only publication refuses it.",
    "Q2VT_PUB_TERRAIN": "The elevation layer (terrain) cannot be used.",
    "Q2VT_PUB_MBTILES_INVALID": "The MBTiles archive is malformed.",
    "Q2VT_PUB_EMPTY_ARCHIVE": "The tile archive contains no tiles.",
    "Q2VT_PUB_PMTILES_INVALID": "The PMTiles archive failed validation.",
    "Q2VT_PUB_TRANSPORT_MISMATCH": "PMTiles and MBTiles tiles differ.",
    "Q2VT_PUB_CANCELLED": "The operation was cancelled.",
    "Q2VT_PUB_DISK": "A file could not be written (disk full, permissions or a locked file).",
    "Q2VT_PUB_PROFILE_INVALID": "The publication profile is invalid.",
    "Q2VT_PUB_SCHEMA_UNSUPPORTED": "The file uses an unsupported schema version.",
    "Q2VT_PUB_PATH_UNSAFE": "A path escapes its publication folder or is not allowed.",
    "Q2VT_PUB_FORBIDDEN_FILE": "A private or source-data file would be published.",
    "Q2VT_PUB_FIELD_DISCLOSURE": "A field that was not approved would be published.",
    "Q2VT_PUB_SECRET_LEAK": "A credential or private connection string was found in the output.",
    "Q2VT_PUB_BUNDLE_INVALID": "The web bundle failed validation.",
    "Q2VT_PUB_RANGE_UNSUPPORTED": "The host does not serve HTTP byte ranges correctly.",
    "Q2VT_PUB_CORS": "The host does not allow cross-origin reads from the viewer origin.",
    "Q2VT_PUB_MIME": "The host serves a file with the wrong content type or encoding.",
    "Q2VT_PUB_PUBLIC_VERIFY": "The public URL does not serve the uploaded release.",
    "Q2VT_PUB_CREDENTIALS": "Credentials are missing, locked or rejected.",
    "Q2VT_PUB_UPLOAD": "Uploading the release failed.",
    "Q2VT_PUB_ACTIVATION_CONFLICT": "Another publisher activated a release first.",
    "Q2VT_PUB_ACTIVATION": "The release could not be activated.",
    "Q2VT_PUB_DESTINATION": "The destination is not configured correctly.",
    "Q2VT_PUB_SEARCH_BUDGET": "The search index exceeds its configured budget.",
    "Q2VT_PUB_IDENTITY": "Feature keys are missing, NULL or not unique.",
    "Q2VT_PUB_DEPENDENCY": "A required library is not available.",
    "Q2VT_PUB_BASEMAP": "The vector basemap could not be prepared.",
}


class PublishingError(Exception):
    """A publishing failure with a stable code."""

    def __init__(self, code: str, message: Optional[str] = None, detail: str = ""):
        if code not in CODES:
            raise KeyError(code)
        self.code = code
        self.message = message or CODES[code]
        self.detail = detail
        super().__init__(f"{code}: {self.message}")


class Cancelled(PublishingError):
    """Raised when the user cancels; partial outputs are cleaned up."""

    def __init__(self, message: str = ""):
        super().__init__("Q2VT_PUB_CANCELLED", message or None)
