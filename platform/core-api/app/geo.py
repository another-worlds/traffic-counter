"""Geometry boundary helpers.

The data contract speaks **GeoJSON** (RFC 7946) at the API edge; the database stores
**native PostGIS geometry** (WGS84 / SRID 4326). These helpers convert between the two
and compute geodesic lengths, so geometry round-trips losslessly:

    GeoJSON dict  --geojson_to_geom-->  WKBElement (native, in DB)
    WKBElement    --geom_to_geojson-->  GeoJSON dict
"""
from __future__ import annotations

from typing import Any

from geoalchemy2.shape import from_shape, to_shape
from pyproj import Geod
from shapely.geometry import mapping, shape

_GEOD = Geod(ellps="WGS84")
SRID = 4326


def geojson_to_geom(geojson: dict[str, Any], srid: int = SRID):
    """GeoJSON geometry dict -> GeoAlchemy2 WKBElement (validated via shapely)."""
    return from_shape(shape(geojson), srid=srid)


def geom_to_geojson(geom) -> dict[str, Any] | None:
    """Native geometry (WKBElement from a query) -> GeoJSON geometry dict."""
    if geom is None:
        return None
    return mapping(to_shape(geom))


def point_xy(geom) -> tuple[float, float]:
    """(lon, lat) of a native POINT geometry."""
    shp = to_shape(geom)
    return float(shp.x), float(shp.y)


def line_length_m(coords: list[list[float]]) -> float:
    """Geodesic length (metres) of a [[lon,lat], ...] line on the WGS84 ellipsoid."""
    if not coords or len(coords) < 2:
        return 0.0
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    total = 0.0
    for i in range(len(coords) - 1):
        _, _, dist = _GEOD.inv(lons[i], lats[i], lons[i + 1], lats[i + 1])
        total += dist
    return float(total)


def straight_line(p1: tuple[float, float], p2: tuple[float, float]) -> dict[str, Any]:
    """A 2-point GeoJSON LineString between (lon,lat) endpoints."""
    return {"type": "LineString", "coordinates": [[p1[0], p1[1]], [p2[0], p2[1]]]}
