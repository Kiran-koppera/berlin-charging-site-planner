import numpy as np
import pandas as pd
import pytest

from chargeplan import excel, forecast, ml


def test_forecast_reproduces_history_and_grows():
    bev = {2023: 24934, 2024: 35815, 2025: 41988, 2026: 48888}
    pts = {2023: 2136, 2024: 3965, 2025: 5362, 2026: 6695}
    fc = forecast.run_scenarios(bev, pts, 1_222_641, 7829)
    s = fc["scenarios"]
    assert all(v["fit_rmse_log"] < 0.1 for v in s.values())
    assert s["slow"]["bev"][2030] < s["base"]["bev"][2030] < s["fast"]["bev"][2030]
    for v in s.values():
        b = list(v["bev"].values())
        assert all(x < y for x, y in zip(b, b[1:]))
        assert v["bev"][2030] < v["params"]["k"]


def test_simulation_respects_capacity():
    p = forecast.Params(r=2.0, k=1000, elasticity=0.5, build_years=1, bev_per_point=5, a_ref=0.1)
    df = forecast.simulate(p, 100, 10, 2020, 2040)
    assert df["bev"].max() <= 1000 + 1e-6
    assert df["bev"].iloc[-1] == pytest.approx(1000, rel=0.01)
    assert df["points"].iloc[-1] == pytest.approx(200, rel=0.05)


def test_entry_model_learns_a_planted_signal(rng):
    n = 2000
    c = pd.DataFrame({"traffic_dtv": rng.lognormal(10, 0.6, n), "nearest_hpc_m": rng.uniform(100, 5000, n),
                      "hpc_within": rng.poisson(2, n), "fast_within_500": rng.poisson(1, n),
                      "gap_density": rng.lognormal(4, 1, n), "kind": rng.choice(["fuel", "supermarket", "hotel"], n)})
    logit = -3 + 1.5 * (np.log1p(c["traffic_dtv"]) - 10) + 1.0 * (c["kind"] == "fuel")
    y = (rng.uniform(size=n) < 1 / (1 + np.exp(-logit))).astype(float)
    m, rep = ml.validate(c.iloc[:1000], y[:1000], c.iloc[1000:], y[1000:].to_numpy() if hasattr(y, "to_numpy") else y[1000:])
    assert rep["test_auc"] > 0.7
    coef = m.coefficients()
    assert coef["log_traffic"] > 0.3 and coef["host_fuel"] > coef["host_hotel"]


def test_python_model_basics():
    p = excel.params("Base")
    r = excel.model(p)
    assert excel.capex(p) == 4 * 60_000 + 140_000 + 90_000 + 20_000
    assert r.fcf[0] == -excel.capex(p) and len(r.fcf) == excel.YEARS + 1
    assert r.utilisation.max() <= p["ucap"] + 1e-12
    assert excel.model(excel.params("Pessimistic")).npv < r.npv < excel.model(excel.params("Optimistic")).npv
    m = excel.breakeven_mult(p)
    assert abs(excel.model(p, m).npv) < 1e-6
    cf = np.array([-100.0, 60, 60])
    assert excel.irr(cf) == pytest.approx(0.1307, abs=1e-4)


def test_score_to_multiplier_range():
    m = excel.score_to_mult(pd.Series([90, 80, 70, 60]))
    assert m.max() == pytest.approx(1.2) and m.min() == pytest.approx(0.8) and m.is_monotonic_decreasing
