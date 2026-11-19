"""Load the Bundesnetzagentur charging register for Berlin, run quality checks, build sites.

The register is a hand-maintained export (semicolon CSV, Latin-1, ten lines of notes before the
header, up to six charge points per row). Rows are *charging devices* (Ladeeinrichtungen); one
location often has several devices, so devices are grouped into *sites* by coordinates.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from . import config

HEADER_MARKER = "Ladeeinrichtungs-ID"
BERLIN_PLZ = (10115, 14199)


def _num(s: str) -> float:
    s = (s or "").strip().replace(".", "").replace(",", ".") if "," in (s or "") else (s or "").strip()
    try:
        return float(s)
    except ValueError:
        return np.nan


def read_register(path: Path, state: str = "Berlin") -> pd.DataFrame:
    """Return one row per charging device in `state`, with charge points unpacked."""
    with open(path, encoding="latin-1", newline="") as f:
        rows = list(csv.reader(f, delimiter=";"))
    start = next(i for i, r in enumerate(rows) if r and r[0].strip() == HEADER_MARKER)
    header = rows[start]
    col = {name: i for i, name in enumerate(header)}
    out = []
    for r in rows[start + 1 :]:
        if len(r) < 17 or r[col["Bundesland"]] != state:
            continue
        points = []
        for k in range(1, 7):
            plug = r[col[f"Steckertypen{k}"]].strip()
            if not plug:
                continue
            points.append((plug, _num(r[col[f"Nennleistung Stecker{k}"]])))
        out.append(
            {
                "device_id": r[col["Ladeeinrichtungs-ID"]].strip(),
                "operator_raw": r[col["Betreiber"]].strip(),
                "status": r[col["Status"]].strip(),
                "device_type": r[col["Art der Ladeeinrichtung"]].strip(),
                "n_points": int(_num(r[col["Anzahl Ladepunkte"]]) or 0),
                "device_kw": _num(r[col["Nennleistung Ladeeinrichtung [kW]"]]),
                "commissioned": pd.to_datetime(r[col["Inbetriebnahmedatum"]], format="%d.%m.%Y", errors="coerce"),
                "street": r[col["Straße"]].strip(),
                "house_no": r[col["Hausnummer"]].strip(),
                "plz": r[col["Postleitzahl"]].strip(),
                "city": r[col["Ort"]].strip(),
                "lat": _num(r[col["Breitengrad"]]),
                "lon": _num(r[col["Längengrad"]]),
                "parking_info": r[col["Informationen zum Parkraum"]].strip(),
                "points": points,
            }
        )
    df = pd.DataFrame(out)
    df["max_point_kw"] = df["points"].map(lambda p: max((kw for _, kw in p if kw == kw), default=np.nan))
    df["has_dc"] = df["points"].map(lambda p: any(plug.startswith("DC") or "DC " in plug for plug, _ in p))
    df["n_listed_points"] = df["points"].map(len)
    df["operator"] = df["operator_raw"].map(normalise_operator)
    return df


_CUT = re.compile(
    r"\s+(?:deutsche Gesellschaft|Gesellschaft|GmbH|mbH|AG|SE|KG|UG|e\.\s?V\.|Deutschland|Germany|Europe|Europa|Dienstleistung|"
    r"Charging Solutions|Smarter Living|Operations|Stiftung|Group Retail|-?\s?Niederlassung)\b.*$",
    re.I,
)
# Brands that register several legal entities in Berlin
_BRANDS = ["E.ON Drive", "Berliner Stadtwerke", "EnBW", "Wirelane", "GSG Gewerbehöfe", "Mercedes", "ALDI", "Lidl",
           "Kaufland", "IKEA", "Tesla", "Qwello", "ubitricity", "Q-Park", "TotalEnergies", "Vattenfall"]


def normalise_operator(name: str) -> str:
    """Group legal entities of one brand: 'E.ON Drive Germany GmbH' and 'E.ON Drive
    Infrastructure GmbH' are one competitor; legal forms and country suffixes are dropped."""
    n = re.sub(r"\s+", " ", name).strip()
    for b in _BRANDS:
        if n.lower().startswith(b.lower()):
            return "Mercedes-Benz" if b == "Mercedes" else b
    short = _CUT.sub("", n).strip(" ,-&")
    return short or n


# --------------------------------------------------------------------------------------------
# Quality checks
# --------------------------------------------------------------------------------------------
@dataclass
class Check:
    check_id: str
    title: str
    severity: str  # error = wrong for the analysis; warning = needs a decision; info = noted
    count: int
    action: str
    examples: list


def quality_checks(df: pd.DataFrame, berlin_shape=None) -> list[Check]:
    """Checks that decide whether a device can be used for location analysis."""
    checks: list[Check] = []

    def add(cid, title, sev, mask, action, cols=("device_id", "operator_raw", "street", "plz", "city")):
        sub = df[mask]
        ex = sub[list(cols)].head(3).astype(str).to_dict("records")
        checks.append(Check(cid, title, sev, int(mask.sum()), action, ex))

    add("Q01", "Missing or unparsable coordinates", "error", df["lat"].isna() | df["lon"].isna(), "Excluded from maps")
    if berlin_shape is not None:
        from shapely import contains_xy

        inside = contains_xy(berlin_shape, df["lon"].fillna(0).to_numpy(), df["lat"].fillna(0).to_numpy())
        add("Q02", "Coordinates outside the Berlin boundary", "error", ~inside & df["lat"].notna(),
            "Excluded; operator should correct the location")
    plz = pd.to_numeric(df["plz"], errors="coerce")
    add("Q03", "Postcode outside Berlin's range (10115-14199)", "warning",
        plz.isna() | (plz < BERLIN_PLZ[0]) | (plz > BERLIN_PLZ[1]), "Kept (coordinates win); flag to operator")
    city_ok = df["city"].str.lower().str.startswith("berlin")
    add("Q04", "City field is not 'Berlin'", "warning", ~city_ok, "Kept if coordinates are inside Berlin")
    add("Q05", "Fast-charging device with no DC plug", "warning",
        (df["device_type"] == "Schnellladeeinrichtung") & ~df["has_dc"], "Classified by plug power, not by label",
        cols=("device_id", "operator_raw", "device_type", "device_kw"))
    add("Q06", "Charge point listed above device power", "warning", df["max_point_kw"] > df["device_kw"] + 0.5,
        "Point power capped at device power", cols=("device_id", "operator_raw", "device_kw", "max_point_kw"))
    add("Q07", "Number of charge points differs from plugs listed", "info", df["n_points"] != df["n_listed_points"],
        "Used listed points (each has a power value)", cols=("device_id", "operator_raw", "n_points", "n_listed_points"))
    add("Q08", "Commissioning date missing or in the future", "warning",
        df["commissioned"].isna() | (df["commissioned"] > pd.Timestamp(config.REGISTER_DATE)), "Excluded from history",
        cols=("device_id", "operator_raw", "commissioned"))
    add("Q09", "Duplicate device id", "error", df["device_id"].duplicated(keep=False), "Kept the first row")
    same_spot = df.duplicated(["lat", "lon", "operator_raw"], keep=False)
    add("Q10", "Several devices at identical coordinates (same operator)", "info", same_spot,
        "Grouped into one site", cols=("device_id", "operator_raw", "street", "house_no"))
    add("Q11", "Parking access not stated", "info", df["parking_info"] == "", "Treated as public")
    add("Q12", "Power implausible (< 3 kW or > 1,000 kW)", "error", (df["device_kw"] < 3) | (df["device_kw"] > 1000),
        "Excluded", cols=("device_id", "operator_raw", "device_kw"))
    return checks


def clean(df: pd.DataFrame, berlin_shape=None) -> pd.DataFrame:
    """Apply the decisions from the quality checks; return usable devices."""
    d = df.drop_duplicates("device_id").copy()
    d = d[d["lat"].notna() & d["lon"].notna()]
    d = d[(d["device_kw"] >= 3) & (d["device_kw"] <= 1000)]
    if berlin_shape is not None:
        from shapely import contains_xy

        d = d[contains_xy(berlin_shape, d["lon"].to_numpy(), d["lat"].to_numpy())]
    d["point_kw"] = d.apply(lambda r: [min(kw, r["device_kw"]) if kw == kw else r["device_kw"] for _, kw in r["points"]]
                            or [r["device_kw"]] * max(r["n_points"], 1), axis=1)
    d["n_pts"] = d["point_kw"].map(len)
    d["ac_points"] = d["point_kw"].map(lambda p: sum(1 for k in p if k < config.DC_KW))
    d["dc_points"] = d["point_kw"].map(lambda p: sum(1 for k in p if config.DC_KW <= k < config.HPC_KW))
    d["hpc_points"] = d["point_kw"].map(lambda p: sum(1 for k in p if k >= config.HPC_KW))
    d["total_kw"] = d["point_kw"].map(sum)
    return d.reset_index(drop=True)


def points_table(devices: pd.DataFrame) -> pd.DataFrame:
    """One row per charge point (for coverage and history)."""
    rows = []
    for r in devices.itertuples():
        for kw in r.point_kw:
            cls = "HPC" if kw >= config.HPC_KW else "DC" if kw >= config.DC_KW else "AC"
            rows.append((r.device_id, r.operator, r.lat, r.lon, kw, cls, r.commissioned))
    return pd.DataFrame(rows, columns=["device_id", "operator", "lat", "lon", "kw", "cls", "commissioned"])


def sites(devices: pd.DataFrame, precision: int = 4) -> pd.DataFrame:
    """Group devices of one operator within ~10 m (4 decimals) into sites."""
    d = devices.copy()
    d["site_key"] = d["operator"] + "|" + d["lat"].round(precision).astype(str) + "|" + d["lon"].round(precision).astype(str)
    g = d.groupby("site_key").agg(
        operator=("operator", "first"), lat=("lat", "mean"), lon=("lon", "mean"),
        street=("street", "first"), house_no=("house_no", "first"),
        devices=("device_id", "count"), points=("n_pts", "sum"), ac=("ac_points", "sum"), dc=("dc_points", "sum"),
        hpc=("hpc_points", "sum"), kw=("total_kw", "sum"), max_kw=("max_point_kw", "max"), first=("commissioned", "min"),
    )
    return g.reset_index(drop=True)
