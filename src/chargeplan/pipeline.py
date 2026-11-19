"""End-to-end run: raw public data -> analysis tables, shortlist, forecast, workbook, site data."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
import shapely

from . import analysis, areas, config, excel, forecast, ml, register, sites


def read_bev_stock(raw: Path) -> tuple[dict, int]:
    """Berlin BEV stock on 1 January of each year, and the latest car stock (KBA FZ1.2)."""
    import openpyxl

    bev, cars = {}, 0
    for f in sorted(raw.glob("fz1_*.xlsx")):
        year = int(f.stem.split("_")[1])
        ws = openpyxl.load_workbook(f, read_only=True, data_only=True)["FZ1.2"]
        for r in ws.iter_rows(values_only=True):
            if isinstance(r[1], str) and r[1].strip().upper() == "BERLIN INSGESAMT":
                bev[year] = int(r[10])
                cars = int(r[4])
                break
    return bev, cars


def _round(o, nd=4):
    if isinstance(o, float):
        return None if o != o else round(o, nd)
    if isinstance(o, dict):
        return {k: _round(v, nd) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_round(v, nd) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return _round(float(o), nd)
    return o


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_round(obj), ensure_ascii=False, separators=(",", ":")))


def run(raw: Path = config.RAW, out: Path = config.OUT) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    data = out / "site" / "data"

    # ---- inputs -------------------------------------------------------------------------
    geo = areas.load_planning_areas(raw / "lor_plr_2021.geojson", raw / "mss2025_plr.xlsx")
    outline = areas.berlin_outline(geo)
    raw_reg = register.read_register(raw / "ladesaeulenregister.csv")
    checks = register.quality_checks(raw_reg, outline)
    devices = register.clean(raw_reg, outline)
    points = register.points_table(devices)
    site_tbl = register.sites(devices)
    traffic = areas.load_traffic(raw / "traffic_kfz_2023.geojson")
    pois = areas.load_pois(raw / "osm_pois.json", config.POI_CLASSES)
    bev_obs, car_stock = read_bev_stock(raw)
    bev_now = bev_obs[max(bev_obs)]

    # ---- analysis -----------------------------------------------------------------------
    ds = analysis.demand_supply(geo, points, traffic, bev_now)
    balance = analysis.city_balance(ds, points)
    grid = areas.population_grid(geo, config.GRID_M)
    cov = analysis.coverage(grid, points)
    comp = analysis.competition(points, site_tbl)
    hist = analysis.history(points)
    cands = sites.score_candidates(pois, ds, geo, points, traffic)
    # backtest + competitor-entry model: score candidates on the network as it stood earlier
    def frame(cutoff: str) -> pd.DataFrame:
        before = points[points["commissioned"] < pd.Timestamp(cutoff)]
        bev = bev_obs.get(pd.Timestamp(cutoff).year, bev_now)
        return sites.score_candidates(pois, analysis.demand_supply(geo, before, traffic, bev), geo, before, traffic)

    f23, f25 = frame("2023-01-01"), frame("2025-01-01")
    y23 = ml.labels(f23, points, "2023-01-01", "2025-01-01")
    y25 = ml.labels(f25, points, "2025-01-01", "2027-01-01")
    bt = sites.backtest(f25, y25)
    bt.update(cutoff="2025-01-01", hpc_points_after=int(((points["commissioned"] >= "2025-01-01") & (points["cls"] == "HPC")).sum()))
    _, ml_report = ml.validate(f23, y23, f25, y25)
    entry = ml.fit(f25, y25)  # refit on the latest period, apply to today's network
    cands["entry_prob"] = entry.predict(cands)
    ml_report["final_coefficients"] = entry.coefficients()
    hpc_list = sites.shortlist(cands, 25, charger="HPC")
    dc_list = sites.shortlist(cands, 15, charger="DC 50-150 kW")
    for t in (hpc_list, dc_list):
        t["reason"] = t.apply(sites.reason, axis=1)
        hi = t["entry_prob"] >= cands["entry_prob"].quantile(0.75)
        t["priority"] = np.where(hi, "Secure first: competitor entry likely", "Strong site, lower competitive pressure")

    # business case per shortlisted HPC site (Python twin of the workbook, Base scenario)
    base = excel.params("Base")
    hpc_list["util_mult"] = excel.score_to_mult(hpc_list["score"]).round(2)
    hpc_list["npv_base"] = [excel.model(base, m).npv for m in hpc_list["util_mult"]]
    hpc_list["irr_base"] = [excel.model(base, m).irr for m in hpc_list["util_mult"]]
    tornado, grid_npv = excel.sensitivity_tables()
    business = {
        "capex": excel.capex(base),
        "scenarios": {n: {"npv": excel.model(excel.params(n)).npv, "irr": excel.model(excel.params(n)).irr,
                          "payback": excel.model(excel.params(n)).payback,
                          "breakeven_mult": excel.breakeven_mult(excel.params(n))} for n in excel.SCEN},
        "tornado": tornado.values.tolist(),
        "grid": {"rows": list(grid_npv.index), "cols": list(grid_npv.columns), "values": grid_npv.values.tolist()},
        "sites_positive": int((hpc_list["npv_base"] > 0).sum()), "sites": int(len(hpc_list)),
        "shortlist_npv": float(hpc_list["npv_base"].sum()),
        "assumptions": [{"key": a[0], "label": a[1], "values": a[2:5], "unit": a[5], "basis": a[7]} for a in excel.ASSUMPTIONS],
    }

    # points in operation on 1 January (survivors in today's register)
    cum = hist.set_index("year")[["AC_cum", "DC_cum", "HPC_cum"]].sum(axis=1)
    points_obs = {y: int(cum.loc[: y - 1].iloc[-1]) for y in bev_obs if (cum.index < y).any()}
    fc = forecast.run_scenarios(bev_obs, points_obs, car_stock, int(len(points)))
    fc["recent_build_pace"] = forecast.recent_build_pace(hist)

    # ---- district summary ---------------------------------------------------------------
    dist = ds.groupby("district").agg(residents=("residents", "sum"), bev_est=("bev_est", "sum"),
                                      points=("points", "sum"), hpc=("hpc_points", "sum"),
                                      demand=("demand_kwh", "sum"), capacity=("capacity_kwh", "sum"),
                                      gap_ac=("gap_ac_kwh", "sum"), gap_dc=("gap_dc_kwh", "sum"))
    dist["points_per_1000"] = dist["points"] / dist["residents"] * 1000
    dist["bev_per_point"] = dist["bev_est"] / dist["points"]
    grid_d = grid.merge(ds[["plr_id", "district"]], on="plr_id")
    for col in ("d_ac_walk", "d_hpc_reach"):
        lim = config.AC_WALK_M if col == "d_ac_walk" else config.HPC_REACH_M
        dist["cov_" + col[2:]] = grid_d.assign(c=grid_d["residents"] * (grid_d[col] <= lim)).groupby("district")["c"].sum() \
            / grid_d.groupby("district")["residents"].sum()
    dist = dist.sort_values("gap_dc", ascending=False).reset_index()

    allego = comp["table"].query("operator == 'Allego'")
    summary = {
        "register_date": config.REGISTER_DATE,
        "devices_raw": int(len(raw_reg)), "devices_used": int(len(devices)), "sites": int(len(site_tbl)),
        "points": int(len(points)), "points_by_class": points["cls"].value_counts().to_dict(),
        "installed_kw": float(points["kw"].sum()), "operators": comp["operators"],
        "residents": int(geo["residents"].sum()), "planning_areas": int(len(geo)),
        "bev_stock": bev_obs, "car_stock": car_stock, "bev_per_point": bev_now / len(points),
        "coverage": cov, "balance": balance,
        "hhi": {k: comp[k] for k in ("hhi_points", "hhi_kw", "hhi_hpc")},
        "candidates": int(len(cands)), "backtest": bt, "entry_model": ml_report, "forecast": fc, "business": business,
        "allego": allego.iloc[0].to_dict() if len(allego) else None,
        "quality": [asdict(c) for c in checks],
    }

    # ---- outputs ------------------------------------------------------------------------
    keep = ["plr_id", "plr_name", "bzr_name", "district", "city_area", "residents", "area_km2", "density", "bev_est",
            "public_share", "vkm", "demand_ac_kwh", "demand_dc_kwh", "demand_kwh", "ac_points", "dc_points", "hpc_points",
            "points", "installed_kw", "capacity_ac_kwh", "capacity_dc_kwh", "gap_ac_kwh", "gap_dc_kwh", "served_ac",
            "served_dc", "points_per_1000"]
    ds[keep].to_csv(out / "planning_areas.csv", index=False)
    cols = ["rank", "name", "poi_label", "charger", "street", "house_no", "district", "plr_name", "lat", "lon", "score",
            "s_gap", "s_traffic", "s_whitespace", "s_poi", "traffic_dtv", "nearest_hpc_m", "hpc_within", "area_gap_kwh",
            "host_access", "entry_prob", "priority", "reason", "osm_id"]
    hpc_cols = cols[:-1] + ["util_mult", "npv_base", "irr_base", "osm_id"]
    hpc_list[hpc_cols].to_csv(out / "shortlist_hpc.csv", index=False)
    dc_list[cols].to_csv(out / "shortlist_dc.csv", index=False)
    comp["table"].to_csv(out / "operators.csv", index=False)
    dist.to_csv(out / "districts.csv", index=False)

    # site data
    ds["gap_dc_km2"] = ds["gap_dc_kwh"] / ds["area_km2"]
    ds["gap_ac_km2"] = ds["gap_ac_kwh"] / ds["area_km2"]
    simp = geo["geometry"].map(lambda g: shapely.set_precision(g.simplify(0.00025), 0.00001))
    feats = []
    for g, r in zip(simp, ds[keep + ["gap_dc_km2", "gap_ac_km2"]].to_dict("records")):
        feats.append({"type": "Feature", "geometry": shapely.geometry.mapping(g),
                      "properties": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()}})
    write_json(data / "areas.geojson", {"type": "FeatureCollection", "features": feats})
    pts = points.assign(lat=points["lat"].round(5), lon=points["lon"].round(5))
    write_json(data / "points.json", {"cols": ["lat", "lon", "kw", "cls", "op"],
                                      "rows": pts[["lat", "lon", "kw", "cls", "operator"]].values.tolist()})
    write_json(data / "shortlist.json", {"hpc": hpc_list[hpc_cols].to_dict("records"), "dc": dc_list[cols].to_dict("records")})
    write_json(data / "operators.json", comp["table"].head(25).to_dict("records"))
    write_json(data / "districts.json", dist.to_dict("records"))
    write_json(data / "history.json", hist.to_dict("records"))
    write_json(data / "summary.json", summary)

    xlsx = out / "site" / "downloads" / "hpc-business-case.xlsx"
    xlsx.parent.mkdir(parents=True, exist_ok=True)
    excel.build_workbook(xlsx, hpc_list, summary)
    for f in ("shortlist_hpc.csv", "planning_areas.csv", "operators.csv"):
        shutil.copy(out / f, xlsx.parent / f)
    web = config.ROOT / "site"
    if web.exists():
        for f in web.iterdir():
            if f.is_file():
                shutil.copy(f, out / "site" / f.name)
    return summary
