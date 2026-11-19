"""Berlin geography: planning areas with population, traffic volumes, points of interest, grid.

Distances use a local metric projection centred on Berlin (equirectangular at 52.52°N), which
is accurate to well under 1 % across the city and needs no projection library.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import shapely
from shapely.geometry import shape
from shapely.ops import unary_union

LAT0, LON0 = 52.52, 13.405
M_PER_DEG_LAT = 110_574.0
M_PER_DEG_LON = 111_320.0 * np.cos(np.radians(LAT0))


def to_xy(lon, lat):
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    return (lon - LON0) * M_PER_DEG_LON, (lat - LAT0) * M_PER_DEG_LAT


def to_lonlat(x, y):
    return np.asarray(x) / M_PER_DEG_LON + LON0, np.asarray(y) / M_PER_DEG_LAT + LAT0


def project_geom(g):
    return shapely.transform(g, lambda c: np.column_stack(to_xy(c[:, 0], c[:, 1])))


def load_planning_areas(geojson: Path, population_xlsx: Path) -> pd.DataFrame:
    """542 LOR planning areas (Planungsräume) with residents on 31 Dec 2024."""
    feats = json.loads(Path(geojson).read_text())["features"]
    rows = []
    for f in feats:
        p = f["properties"]
        g = shape(f["geometry"])
        rows.append({
            "plr_id": p["plr_id"], "plr_name": p["plr_name"], "bzr_name": p["bzr_name"],
            "district": p["bez"].split(" - ", 1)[-1], "city_area": p["stadtraum_name"],
            "geometry": g, "geom_m": project_geom(g),
        })
    areas = pd.DataFrame(rows)
    areas["area_km2"] = areas["geom_m"].map(lambda g: g.area / 1e6)
    pop = read_population(population_xlsx)
    areas = areas.merge(pop, on="plr_id", how="left", validate="one_to_one")
    areas["residents"] = areas["residents"].fillna(0).astype(int)
    areas["density"] = areas["residents"] / areas["area_km2"]
    c = areas["geom_m"].map(lambda g: g.representative_point())
    areas["cx"], areas["cy"] = c.map(lambda p: p.x), c.map(lambda p: p.y)
    return areas


def read_population(path: Path) -> pd.DataFrame:
    import openpyxl

    ws = openpyxl.load_workbook(path, read_only=True, data_only=True).worksheets[0]
    out = []
    for r in ws.iter_rows(values_only=True):
        pid = str(r[0]).strip() if r[0] is not None else ""
        if len(pid) == 8 and pid.isdigit() and isinstance(r[2], (int, float)):
            out.append({"plr_id": pid, "residents": int(r[2])})
    return pd.DataFrame(out)


def berlin_outline(areas: pd.DataFrame):
    """Union of all planning areas, in lon/lat, slightly buffered for points on the border."""
    return unary_union(list(areas["geometry"])).buffer(0.0005)


def load_traffic(geojson: Path) -> pd.DataFrame:
    """Average weekday car traffic (DTVw 2023) per road link, as projected lines."""
    feats = json.loads(Path(geojson).read_text())["features"]
    rows = []
    for f in feats:
        p = f["properties"]
        if not f.get("geometry") or p.get("dtvw_kfz") in (None, 0):
            continue
        g = project_geom(shape(f["geometry"]))
        rows.append({"road": p.get("str_name") or "", "road_class": p.get("strklasse") or "",
                     "dtv": float(p["dtvw_kfz"]), "geom_m": g, "length_m": g.length})
    return pd.DataFrame(rows)


BRAND_REQUIRED = {"doityourself", "hardware", "furniture", "department_store", "supermarket"}
DRIVE_IN_BRANDS = re.compile(r"^(McDonald|Burger King|KFC)", re.I)


def load_pois(path: Path, classes: dict) -> pd.DataFrame:
    """OpenStreetMap points of interest that could host chargers."""
    els = json.loads(Path(path).read_text())["elements"]
    rows = []
    for e in els:
        t = e.get("tags", {})
        kind = next((t[k] for k in ("shop", "amenity", "tourism") if t.get(k) in classes), None)
        if not kind:
            continue
        lat = e.get("lat", e.get("center", {}).get("lat"))
        lon = e.get("lon", e.get("center", {}).get("lon"))
        if lat is None:
            continue
        # fast food only where people arrive by car: drive-throughs and the big drive-in chains
        # big-box retail only: small hardware, knife or furniture shops have no car park. A brand tag
        # (OBI, Hornbach, IKEA, Höffner ...) is the best signal of a large store in OpenStreetMap
        if kind in BRAND_REQUIRED and not t.get("brand"):
            continue
        if kind == "fast_food" and not (t.get("drive_through") == "yes" or DRIVE_IN_BRANDS.match(t.get("brand", ""))):
            continue
        rows.append({"osm_id": f"{e['type'][0]}{e['id']}", "kind": kind, "name": t.get("name") or t.get("brand") or "",
                     "brand": t.get("brand", ""), "street": t.get("addr:street", ""),
                     "house_no": t.get("addr:housenumber", ""), "lat": lat, "lon": lon})
    df = pd.DataFrame(rows).drop_duplicates("osm_id")
    df["x"], df["y"] = to_xy(df["lon"], df["lat"])
    return df.reset_index(drop=True)


def population_grid(areas: pd.DataFrame, cell_m: int) -> pd.DataFrame:
    """Spread each planning area's residents evenly over the grid cells inside it.

    Planning areas are small (median ~0.9 km²), so this keeps the spatial detail where people
    live without needing address-level data.
    """
    rows = []
    for a in areas.itertuples():
        if a.residents <= 0:
            continue
        g = a.geom_m
        minx, miny, maxx, maxy = g.bounds
        xs = np.arange(np.floor(minx / cell_m) * cell_m + cell_m / 2, maxx, cell_m)
        ys = np.arange(np.floor(miny / cell_m) * cell_m + cell_m / 2, maxy, cell_m)
        if len(xs) == 0 or len(ys) == 0:
            pts = [(g.representative_point().x, g.representative_point().y)]
        else:
            gx, gy = np.meshgrid(xs, ys)
            inside = shapely.contains_xy(g, gx.ravel(), gy.ravel())
            pts = list(zip(gx.ravel()[inside], gy.ravel()[inside]))
            if not pts:
                pts = [(g.representative_point().x, g.representative_point().y)]
        share = a.residents / len(pts)
        rows.extend((a.plr_id, x, y, share) for x, y in pts)
    return pd.DataFrame(rows, columns=["plr_id", "x", "y", "residents"])


def nearest_distance(src_x, src_y, dst_x, dst_y) -> np.ndarray:
    """Distance from each source point to the nearest destination point (metres)."""
    from shapely import STRtree, points

    dst = points(np.asarray(dst_x), np.asarray(dst_y))
    if len(dst) == 0:
        return np.full(len(src_x), np.inf)
    tree = STRtree(dst)
    src = points(np.asarray(src_x), np.asarray(src_y))
    idx = tree.nearest(src)
    return shapely.distance(src, dst[idx])


def count_within(src_x, src_y, dst_x, dst_y, radius: float, weights=None) -> np.ndarray:
    from shapely import STRtree, points

    dst = points(np.asarray(dst_x), np.asarray(dst_y))
    w = np.ones(len(dst)) if weights is None else np.asarray(weights, float)
    if len(dst) == 0:
        return np.zeros(len(src_x))
    tree = STRtree(dst)
    src = points(np.asarray(src_x), np.asarray(src_y))
    pairs = tree.query(src, predicate="dwithin", distance=radius)
    out = np.zeros(len(src))
    np.add.at(out, pairs[0], w[pairs[1]])
    return out
