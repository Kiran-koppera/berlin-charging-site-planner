import numpy as np
import pytest

from chargeplan import analysis, areas, config, register, sites


@pytest.fixture
def world(raw_dir, geo):
    dev = register.clean(register.read_register(raw_dir / "ladesaeulenregister.csv"), areas.berlin_outline(geo))
    pts = register.points_table(dev)
    traffic = areas.load_traffic(raw_dir / "traffic_kfz_2023.geojson")
    pois = areas.load_pois(raw_dir / "osm_pois.json", config.POI_CLASSES)
    return geo, pts, traffic, pois, dev


def test_projection_round_trip_and_scale():
    x, y = areas.to_xy([13.405, 13.505], [52.52, 52.52])
    assert abs(x[1] - x[0] - 6_775) < 30  # 0.1° of longitude at 52.5°N is about 6.78 km
    lon, lat = areas.to_lonlat(x, y)
    assert np.allclose(lon, [13.405, 13.505]) and np.allclose(lat, [52.52, 52.52])


def test_population_and_grid(geo):
    assert list(geo["residents"]) == [20000, 10000]
    grid = areas.population_grid(geo, 250)
    assert abs(grid["residents"].sum() - 30000) < 1e-6
    assert grid.groupby("plr_id")["residents"].sum().round().to_dict() == {"01100101": 20000, "02200202": 10000}


def test_pois_filter_small_shops_and_non_drive_in(world):
    pois = world[3]
    names = set(pois["name"])
    assert "Kleiner Laden" not in names  # hardware shop without a brand
    assert "Imbiss" not in names  # fast food without drive-through or chain brand
    assert {"McDonald's", "OBI", "REWE"} <= set(pois["name"]) | set(pois["brand"])


def test_demand_supply_balances_relative_gap(world):
    geo, pts, traffic, _, _ = world
    ds = analysis.demand_supply(geo, pts, traffic, bev_total=3000)
    assert abs(ds["bev_est"].sum() - 3000) < 1e-6
    # inner city relies more on public charging
    assert ds.loc[0, "public_share"] > ds.loc[1, "public_share"]
    # relative gaps sum to zero by construction
    assert abs(ds["gap_ac_kwh"].sum()) < 1e-6 and abs(ds["gap_dc_kwh"].sum()) < 1e-6
    assert ds["points"].sum() == len(pts)
    assert ds["vkm"].sum() == pytest.approx(40000 * 2.71 + 8000 * 2.22, rel=0.03)


def test_coverage_and_competition(world):
    geo, pts, _, _, dev = world
    grid = areas.population_grid(geo, 250)
    cov = analysis.coverage(grid, pts)
    assert 0 < cov["ac_walk"] <= cov["any_walk"] <= 1
    assert cov["hpc_reach"] > 0.9  # two HPC sites cover the 2.7 km wide test area
    comp = analysis.competition(pts, register.sites(dev))
    t = comp["table"].set_index("operator")
    assert t.loc["Allego", "hpc"] == 2 and t.loc["E.ON Drive", "points"] == 3
    assert t["share_points"].sum() == pytest.approx(1)
    assert 0 < comp["hhi_points"] <= 10_000


def test_scoring_and_shortlist(world):
    geo, pts, traffic, pois, _ = world
    ds = analysis.demand_supply(geo, pts, traffic, 3000)
    c = sites.score_candidates(pois, ds, geo, pts, traffic)
    assert c["score"].between(0, 100).all() and c["score"].is_monotonic_decreasing
    aral = c[c["name"] == "Aral"].iloc[0]
    assert aral["host_access"] == "Competitor-controlled forecourt"
    assert aral["traffic_dtv"] == 40000
    sl = sites.shortlist(c, 10, spacing=500)
    assert "Aral" not in set(sl["name"]) and list(sl["rank"]) == list(range(1, len(sl) + 1))
    xy = sl[["x", "y"]].to_numpy()
    d = np.hypot(*(xy[:, None, :] - xy[None, :, :]).transpose(2, 0, 1))
    assert (d[np.triu_indices(len(sl), 1)] >= 500).all()
    assert isinstance(sites.reason(sl.iloc[0]), str)


def test_auc():
    assert sites.auc([1, 2, 3, 4], [0, 0, 1, 1]) == 1.0
    assert sites.auc([4, 3, 2, 1], [0, 0, 1, 1]) == 0.0
    assert sites.auc([1, 1, 1, 1], [0, 1, 0, 1]) == 0.5
    assert np.isnan(sites.auc([1, 2], [0, 0]))


def test_reads_official_population_layout(tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.title = "Titel"
    ws = wb.create_sheet("T2")
    ws.append(["2  Einwohnerinnen und Einwohner in Berlin am 30. Juni 2026 nach LOR-Planungsräumen"])
    ws.append([None, None, None, None, "Mitte"])
    ws.append(["01", "10", "01", "01", 3531, 132])
    ws.append(["01", "10", "01", "02", 2091, 91])
    ws.append([None, None, None, None, 5622])  # district total row
    wb.save(tmp_path / "p.xlsx")
    pop = areas.read_population(tmp_path / "p.xlsx")
    assert pop.to_dict("records") == [{"plr_id": "01100101", "residents": 3531}, {"plr_id": "01100102", "residents": 2091}]
