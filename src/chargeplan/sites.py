"""Score candidate host sites and pick a shortlist; backtest the score on past openings."""

from __future__ import annotations

import numpy as np
import pandas as pd
import shapely

from . import config
from .analysis import assign_area
from .areas import count_within, nearest_distance, to_xy


def _rank01(s: pd.Series) -> pd.Series:
    """Percentile rank in [0, 1]; robust to the heavy tails of traffic and demand."""
    return s.rank(pct=True, method="average").fillna(0)


def road_traffic_near(x, y, traffic: pd.DataFrame, radius: float) -> np.ndarray:
    """Highest weekday car volume (DTVw) on any counted road within `radius` of each point."""
    tree = shapely.STRtree(list(traffic["geom_m"]))
    pts = shapely.points(np.asarray(x), np.asarray(y))
    pairs = tree.query(pts, predicate="dwithin", distance=radius)
    out = np.zeros(len(pts))
    np.maximum.at(out, pairs[0], traffic["dtv"].to_numpy()[pairs[1]])
    return out


def score_candidates(pois: pd.DataFrame, areas_ds: pd.DataFrame, areas_geo: pd.DataFrame, points: pd.DataFrame,
                     traffic: pd.DataFrame) -> pd.DataFrame:
    """Score every POI. Components are percentile ranks so the weights are comparable."""
    c = pois.copy()
    c["area"] = assign_area(areas_geo, c["x"], c["y"])
    c = c[c["area"] >= 0].copy()
    a = areas_ds.reset_index(drop=True)
    c["plr_id"] = a.loc[c["area"], "plr_id"].to_numpy()
    c["plr_name"] = a.loc[c["area"], "plr_name"].to_numpy()
    c["district"] = a.loc[c["area"], "district"].to_numpy()
    # gap per km² so large, empty areas do not win by size alone; fast-charging sites look at the
    # DC gap, AC sites at the AC gap
    c["charger"] = c["kind"].map(lambda k: config.POI_CLASSES[k][1])
    is_ac = c["charger"].str.startswith("AC 11-22 kW") & ~c["charger"].str.contains("DC")
    gap_ac = (a["gap_ac_kwh"].clip(lower=0) / a["area_km2"]).to_numpy()
    gap_dc = (a["gap_dc_kwh"].clip(lower=0) / a["area_km2"]).to_numpy()
    c["area_gap_kwh"] = np.where(is_ac, a.loc[c["area"], "gap_ac_kwh"], a.loc[c["area"], "gap_dc_kwh"])
    c["gap_density"] = np.where(is_ac, gap_ac[c["area"]], gap_dc[c["area"]])
    c["traffic_dtv"] = road_traffic_near(c["x"], c["y"], traffic, config.TRAFFIC_RADIUS_M)
    px, py = to_xy(points["lon"], points["lat"])
    hpc = (points["cls"] == "HPC").to_numpy()
    fast = (points["cls"] != "AC").to_numpy()
    c["nearest_hpc_m"] = nearest_distance(c["x"], c["y"], px[hpc], py[hpc])
    c["hpc_within"] = count_within(c["x"], c["y"], px[hpc], py[hpc], config.HPC_COMPETITOR_RADIUS_M)
    c["fast_within_500"] = count_within(c["x"], c["y"], px[fast], py[fast], 500)
    c["poi_label"] = c["kind"].map(lambda k: config.POI_CLASSES[k][0])
    brand = c["brand"].where(c["brand"] != "", c["name"]).fillna("")
    own = (c["kind"] == "fuel") & brand.str.match(config.COMPETITOR_HOST_BRANDS)
    c["host_access"] = np.where(own, "Competitor-controlled forecourt", "Open to a host partnership")
    c["poi_fit"] = c["kind"].map(lambda k: config.POI_CLASSES[k][2])

    s_gap = _rank01(c["gap_density"])
    s_traffic = _rank01(c["traffic_dtv"])
    # whitespace: far from existing HPC and few competing HPC points nearby
    s_white = 0.6 * _rank01(c["nearest_hpc_m"].clip(upper=8000)) + 0.4 * (1 - _rank01(c["hpc_within"]))
    w = config.WEIGHTS
    c["s_gap"], c["s_traffic"], c["s_whitespace"], c["s_poi"] = s_gap, s_traffic, s_white, c["poi_fit"]
    c["score"] = 100 * (w["gap"] * s_gap + w["traffic"] * s_traffic + w["whitespace"] * s_white + w["poi"] * c["poi_fit"])
    # a site that already has fast chargers on the spot is not a new location
    c.loc[c["fast_within_500"] >= 4, "score"] *= 0.6
    return c.sort_values("score", ascending=False).reset_index(drop=True)


def shortlist(cands: pd.DataFrame, n: int = 30, spacing: float | None = None, charger: str | None = None,
              partner_only: bool = True) -> pd.DataFrame:
    """Greedy pick of the best candidates at least `spacing` metres apart."""
    spacing = spacing or config.SITE_MIN_SPACING_M
    pool = cands if charger is None else cands[cands["charger"] == charger]
    if partner_only:
        pool = pool[pool["host_access"] == "Open to a host partnership"]
    chosen: list[int] = []
    xs, ys = [], []
    for i, r in pool.iterrows():
        if xs and np.min(np.hypot(np.array(xs) - r["x"], np.array(ys) - r["y"])) < spacing:
            continue
        chosen.append(i)
        xs.append(r["x"])
        ys.append(r["y"])
        if len(chosen) == n:
            break
    out = pool.loc[chosen].reset_index(drop=True)
    out.insert(0, "rank", range(1, len(out) + 1))
    return out


def reason(r) -> str:
    """One-line plain-language reason for a shortlisted site."""
    bits = []
    if r["s_gap"] >= 0.75:
        bits.append("large charging gap in the area")
    if r["traffic_dtv"] >= 20_000:
        bits.append(f"{r['traffic_dtv'] / 1000:.0f}k cars/weekday on the adjacent road")
    if r["nearest_hpc_m"] >= 2_000:
        bits.append(f"nearest HPC {r['nearest_hpc_m'] / 1000:.1f} km away")
    elif r["hpc_within"] == 0:
        bits.append("no HPC within 1.5 km")
    bits.append(f"{r['poi_label'].lower()} (dwell fits {r['charger']})")
    return "; ".join(bits)


# --------------------------------------------------------------------------------------------
# Backtest
# --------------------------------------------------------------------------------------------
def auc(scores: np.ndarray, labels: np.ndarray) -> float:
    """Area under the ROC curve via the rank-sum statistic."""
    scores, labels = np.asarray(scores, float), np.asarray(labels, bool)
    n1, n0 = labels.sum(), (~labels).sum()
    if n1 == 0 or n0 == 0:
        return float("nan")
    ranks = pd.Series(scores).rank(method="average").to_numpy()
    return float((ranks[labels].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def backtest(cands: pd.DataFrame, hit: np.ndarray) -> dict:
    """How well did the opportunity score, scored on the network of an earlier date, anticipate
    where operators actually opened HPC afterwards? Also reports each component on its own."""
    hit = np.asarray(hit, bool)
    top20 = (cands["score"] >= cands["score"].quantile(0.8)).to_numpy()
    comps = {"score": "score", "gap": "s_gap", "traffic": "s_traffic", "whitespace": "s_whitespace", "host_type": "s_poi",
             "distance_to_hpc": "nearest_hpc_m"}
    by_host = (pd.DataFrame({"host": cands["poi_label"], "hit": hit}).groupby("host")["hit"].agg(["mean", "sum", "size"])
               .sort_values("mean", ascending=False))
    return {
        "candidates": int(len(cands)), "candidates_hit": int(hit.sum()),
        "auc": {k: auc(cands[v], hit) for k, v in comps.items()},
        "hit_rate_top20": float(hit[top20].mean()), "hit_rate_rest": float(hit[~top20].mean()),
        "hit_rate_by_host": {k: {"rate": float(r["mean"]), "hits": int(r["sum"]), "n": int(r["size"])}
                             for k, r in by_host.iterrows()},
    }
