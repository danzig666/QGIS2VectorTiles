"""
The project's coordinate reference system, for the viewer's coordinate
readout: its id, name and PROJ definition (public, not sensitive). The
viewer shows coordinates in this CRS (EOV with its own transformation, any
other projected CRS through proj4js) next to WGS 84.
"""

from typing import Optional


def project_crs_info(project) -> Optional[dict]:
    """``{"authid", "name", "proj", "geographic", "units"}`` of the project CRS,
    or ``None`` when it is not valid."""
    from qgis.core import QgsUnitTypes  # pylint: disable=import-outside-toplevel
    crs = project.crs()
    if crs is None or not crs.isValid():
        return None
    proj = ""
    try:
        proj = crs.toProj()
    except (AttributeError, RuntimeError):
        proj = ""
    try:
        units = QgsUnitTypes.toAbbreviatedString(crs.mapUnits())
    except (AttributeError, RuntimeError, TypeError):
        units = ""
    return {"authid": crs.authid() or "", "name": crs.description() or crs.authid() or "",
            "proj": proj or "", "geographic": bool(crs.isGeographic()), "units": units or ""}
