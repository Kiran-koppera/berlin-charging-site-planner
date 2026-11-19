"""Demand, supply, gap and coverage per planning area; competitive landscape."""

from __future__ import annotations

import numpy as np
import pandas as pd
import shapely

from . import config
from .areas import count_within, nearest_distance, to_xy


def assign_area(areas: pd.DataFrame, x, y) -> np.ndarray:
    """Planning-area index for each projected point (-1 if outside)."""
    tree = shapely.STRtree(list(areas["geom_m"]))
    pts = shapely.points(np.asarray(x), np.asarray(y))
    hit = tree.query(pts, predicate="within")
    out = np.full(len(pts), -1)
    out[hit[0]] = hit[1]
    return out


def traffic_by_area(areas: pd.DataFrame, traffic: pd.DataFrame) -> np.ndarray:
    """Vehicle-km per weekday driven inside each planning area (DTV x length inside)."""
    vkm = np.zeros(len(areas))
    tree = shapely.STRtree(list(traffic["geom_m"]))
    for i, g in enumerate(areas["geom_m"]):
        idx = tree.query(g, predicate="intersects")
        if len(idx) == 0:
            continue
        sub = traffic.iloc[idx]
        inside = shapely.intersection(np.array(list(sub["geom_m"])), g)
        vkm[i] = float((shapely.length(inside) / 1000 * sub["dtv"].to_numpy()).sum())
    return vkm


def demand_supply(areas: pd.DataFrame, points: pd.DataFrame, traffic: pd.DataFrame, bev_total: int) -> pd.DataFrame:
    """Daily public charging demand vs. what existing points can deliver, per planning area."""
    a = areas.drop(columns=["geometry"]).copy()
    bev_per_resident = bev_total / a["residents"].sum()
    a["bev_est"] = a["residents"] * bev_per_resident
    a["public_share"] = a["city_area"].map(config.PUBLIC_SHARE).fillna(min(config.PUBLIC_SHARE.values()))
    daily_kwh = config.KM_PER_YEAR * config.KWH_PER_KM / 365
    a["resident_kwh"] = a["bev_est"] * daily_kwh * a["public_share"]
    a["vkm"] = traffic_by_area(areas, traffic)
    # through traffic: BEV-km driven in the area that need a top-up there
    a["traffic_kwh"] = a["vkm"] * config.TRAFFIC_BEV_SHARE * config.KWH_PER_KM * 0.25  # assumption: 25 % of energy for
    # km driven in an area is bought there by non-residents (commuters, deliveries, taxis)
    # AC serves residents' slow charging; DC/HPC serves fast top-ups by residents and through traffic
    a["demand_ac_kwh"] = a["resident_kwh"] * (1 - config.DC_SHARE_OF_PUBLIC)
    a["demand_dc_kwh"] = a["resident_kwh"] * config.DC_SHARE_OF_PUBLIC + a["traffic_kwh"]
    a["demand_kwh"] = a["demand_ac_kwh"] + a["demand_dc_kwh"]

    px, py = to_xy(points["lon"], points["lat"])
    idx = assign_area(areas, px, py)
    p = points.assign(area=idx)
    p = p[p["area"] >= 0].copy()
    p["cap"] = np.where(p["cls"] == "AC", config.KWH_PER_DAY_AC, p["kw"] * config.KWH_PER_DAY_DC_PER_KW)
    counts = p.pivot_table(index="area", columns="cls", values="kw", aggfunc="size", fill_value=0)
    for cls in ("AC", "DC", "HPC"):
        a[f"{cls.lower()}_points"] = counts.get(cls, pd.Series(dtype=float)).reindex(range(len(a))).fillna(0).astype(int)
    a["points"] = a[["ac_points", "dc_points", "hpc_points"]].sum(axis=1)
    a["installed_kw"] = p.groupby("area")["kw"].sum().reindex(range(len(a))).fillna(0)
    is_ac = p["cls"] == "AC"
    a["capacity_ac_kwh"] = p[is_ac].groupby("area")["cap"].sum().reindex(range(len(a))).fillna(0)
    a["capacity_dc_kwh"] = p[~is_ac].groupby("area")["cap"].sum().reindex(range(len(a))).fillna(0)
    a["capacity_kwh"] = a["capacity_ac_kwh"] + a["capacity_dc_kwh"]
    # Relative gap: demand an area would get if capacity were spread like demand, minus what it has.
    # City-wide supply and demand need not match (utilisation is low today); what matters for siting
    # is where demand is under-served compared with the rest of Berlin.
    for k in ("ac", "dc"):
        ratio = a[f"capacity_{k}_kwh"].sum() / a[f"demand_{k}_kwh"].sum()
        a[f"gap_{k}_kwh"] = a[f"demand_{k}_kwh"] * ratio - a[f"capacity_{k}_kwh"]
        a[f"served_{k}"] = np.where(a[f"demand_{k}_kwh"] > 0, a[f"capacity_{k}_kwh"] / (a[f"demand_{k}_kwh"] * ratio), np.nan)
    a["gap_kwh"] = a["gap_ac_kwh"] + a["gap_dc_kwh"]
    a["points_per_1000"] = np.where(a["residents"] > 0, a["points"] / a["residents"] * 1000, np.nan)
    return a


def city_balance(a: pd.DataFrame, points: pd.DataFrame) -> dict:
    """City-wide demand vs. capacity and the utilisation it implies."""
    kw = float(points["kw"].sum())
    return {
        "demand_kwh_day": float(a["demand_kwh"].sum()),
        "demand_ac_kwh_day": float(a["demand_ac_kwh"].sum()),
        "demand_dc_kwh_day": float(a["demand_dc_kwh"].sum()),
        "capacity_kwh_day": float(a["capacity_kwh"].sum()),
        "installed_kw": kw,
        "implied_utilisation": float(a["demand_kwh"].sum() / (kw * 24)),
    }


def coverage(grid: pd.DataFrame, points: pd.DataFrame) -> dict:
    """Share of residents within walking distance of AC and within reach of HPC."""
    px, py = to_xy(points["lon"], points["lat"])
    out = {}
    for label, mask, radius in (
        ("ac_walk", points["cls"] == "AC", config.AC_WALK_M),
        ("any_walk", points["cls"].notna(), config.AC_WALK_M),
        ("hpc_reach", points["cls"] == "HPC", config.HPC_REACH_M),
    ):
        d = nearest_distance(grid["x"], grid["y"], px[mask.to_numpy()], py[mask.to_numpy()])
        out[label] = float((grid["residents"] * (d <= radius)).sum() / grid["residents"].sum())
        grid[f"d_{label}"] = d
    return out


def competition(points: pd.DataFrame, sites: pd.DataFrame) -> dict:
    """Operator shares by charge points, installed power and HPC points; concentration (HHI)."""
    by = points.groupby("operator").agg(points=("kw", "size"), kw=("kw", "sum"),
                                        hpc=("cls", lambda s: (s == "HPC").sum()),
                                        dc=("cls", lambda s: (s == "DC").sum()))
    by["sites"] = sites.groupby("operator").size().reindex(by.index).fillna(0).astype(int)
    by["share_points"] = by["points"] / by["points"].sum()
    by["share_kw"] = by["kw"] / by["kw"].sum()
    by["share_hpc"] = by["hpc"] / max(by["hpc"].sum(), 1)
    by = by.sort_values("kw", ascending=False)
    hhi = lambda s: float((s * 100) ** 2 @ np.ones(len(s)))
    return {
        "table": by.reset_index(),
        "hhi_points": hhi(by["share_points"]),
        "hhi_kw": hhi(by["share_kw"]),
        "hhi_hpc": hhi(by["share_hpc"]),
        "operators": int(len(by)),
    }


def history(points: pd.DataFrame) -> pd.DataFrame:
    """Charge points still in operation, by commissioning year and class (survivors only)."""
    p = points.dropna(subset=["commissioned"]).copy()
    p["year"] = p["commissioned"].dt.year
    t = p.pivot_table(index="year", columns="cls", values="kw", aggfunc="size", fill_value=0)
    t = t.reindex(columns=["AC", "DC", "HPC"], fill_value=0)
    t["kw_added"] = p.groupby("year")["kw"].sum()
    cum = t[["AC", "DC", "HPC"]].cumsum().add_suffix("_cum")
    return t.join(cum).reset_index()


def within(points: pd.DataFrame, x, y, radius: float, cls: str | None = None) -> np.ndarray:
    px, py = to_xy(points["lon"], points["lat"])
    m = np.ones(len(points), bool) if cls is None else (points["cls"] == cls).to_numpy()
    return count_within(x, y, px[m], py[m], radius)
