"""
datasets.py

The exported datasets (one GeoPackage per rule group; thousands of them in a
big project) read and pruned with SQLite directly. Opening each one as a QGIS
layer costs 10-20 ms and as an OGR dataset 2-3 ms; SQLite needs well under
one. Whatever SQLite cannot do here (not a one-table GeoPackage, an old SQLite
without DROP COLUMN, a constraint on the column) is left to the caller's
QGIS / OGR way, so the result is the same either way.
"""

import os
import pathlib
import sqlite3
from contextlib import closing
from typing import Callable, Iterable, List, NamedTuple, Optional


class DatasetInfo(NamedTuple):
    table: str
    count: int
    fields: List[str]  # the attribute fields, as OGR lists them (no fid, no geometry)


def _read_only(path: str) -> str:
    return pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro"


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def gpkg_info(path: str) -> Optional[DatasetInfo]:
    """Table, feature count and attribute fields of a one-table GeoPackage;
    None when the file is not one (or cannot be read)."""
    try:
        with closing(sqlite3.connect(_read_only(path), uri=True)) as conn:
            tables = conn.execute("SELECT table_name FROM gpkg_contents").fetchall()
            if len(tables) != 1:
                return None
            table = tables[0][0]
            geometry = {row[0].lower() for row in conn.execute(
                "SELECT column_name FROM gpkg_geometry_columns WHERE lower(table_name) = lower(?)",
                (table,))}
            columns = conn.execute(f"PRAGMA table_info({_quoted(table)})").fetchall()
            if not columns:
                return None
            fields = [c[1] for c in columns if not c[5] and c[1].lower() not in geometry]
            count = None
            try:  # OGR keeps the count there (what QGIS / OGR report)
                row = conn.execute("SELECT feature_count FROM gpkg_ogr_contents "
                                   "WHERE lower(table_name) = lower(?)", (table,)).fetchone()
                count = row[0] if row else None
            except sqlite3.Error:
                count = None
            if count is None or count < 0:
                count = conn.execute(f"SELECT COUNT(*) FROM {_quoted(table)}").fetchone()[0]
            return DatasetInfo(table, int(count), fields)
    except (sqlite3.Error, OSError, ValueError):
        return None


def drop_fields(path: str, table: str, names: Iterable[str]) -> bool:
    """Drop attribute fields of a GeoPackage table in one transaction (SQLite
    3.35+ ALTER TABLE DROP COLUMN, as OGR does); False when SQLite cannot, and
    the file is then unchanged."""
    names = list(names)
    if not names:
        return True
    if sqlite3.sqlite_version_info < (3, 35, 5):
        return False
    try:
        with closing(sqlite3.connect(path, isolation_level=None)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for name in names:
                    conn.execute(f"ALTER TABLE {_quoted(table)} DROP COLUMN {_quoted(name)}")
                has_columns = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'gpkg_data_columns'"
                ).fetchone()
                if has_columns:  # OGR's own record of the columns (aliases, domains)
                    conn.executemany(
                        "DELETE FROM gpkg_data_columns WHERE lower(table_name) = lower(?) "
                        "AND lower(column_name) = lower(?)", [(table, name) for name in names])
                conn.execute("COMMIT")
            except sqlite3.Error:
                conn.execute("ROLLBACK")
                return False
        return True
    except sqlite3.Error:
        return False


class ExportedDataset:
    """A rule group's dataset as the export hands it on: its file, name and
    feature count, known without opening it as a QGIS layer. Anything else a
    QgsVectorLayer offers opens it then (``opener``)."""

    def __init__(self, path: str, name: str, count: int, opener: Callable[[], object]):
        self._path = path
        self._name = name
        self._count = count
        self._opener = opener
        self._layer = None

    def source(self) -> str:
        return self._path

    def name(self) -> str:
        return self._name

    def featureCount(self) -> int:  # noqa: N802 - QgsVectorLayer's name
        return self._count

    def isValid(self) -> bool:  # noqa: N802 - QgsVectorLayer's name
        return True

    def __getattr__(self, attr):
        if attr.startswith("_"):
            raise AttributeError(attr)
        if self._layer is None:
            self._layer = self._opener()
        return getattr(self._layer, attr)

    def __repr__(self) -> str:
        return f"<ExportedDataset {self._name}: {self._count} features>"
