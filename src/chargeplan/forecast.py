"""A small system-dynamics model of Berlin's EV market and public charging network.

Causal loops (see docs/system-dynamics.md for the diagram):

  R1  charging availability:  more public points -> more people buy a BEV -> more BEVs
      -> operators see demand -> more public points                         (reinforcing)
  B1  market saturation:      more BEVs -> fewer car owners left to switch -> slower adoption
                                                                             (balancing)
  B2  build-out gap:          the further the network is behind target, the faster it grows;
                              the gap closes as points are built              (balancing)

Stocks: BEV stock B and public charge points C. Flows: adoption dB/dt and construction dC/dt.
Integrated with Euler steps of a quarter year, like a Vensim model with TIME STEP = 0.25.
The adoption rate r is calibrated to the KBA registrations 2023-2026; K, elasticity and build
time vary by scenario.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import config

DT = 0.25


@dataclass
class Params:
    r: float  # intrinsic adoption rate per year
    k: float  # carrying capacity (BEVs)
    elasticity: float  # response of adoption to charging availability
    build_years: float  # time to close the gap between target and actual network
    bev_per_point: float  # target BEVs per public charge point
    a_ref: float  # availability (points per BEV) in the start year


def simulate(p: Params, b0: float, c0: float, start: int, end: int) -> pd.DataFrame:
    t = np.arange(start, end + DT / 2, DT)
    b, c = np.empty(len(t)), np.empty(len(t))
    b[0], c[0] = b0, c0
    for i in range(1, len(t)):
        avail = (c[i - 1] / b[i - 1]) / p.a_ref
        adoption = p.r * b[i - 1] * (1 - b[i - 1] / p.k) * avail ** p.elasticity
        target = b[i - 1] / p.bev_per_point
        build = max(target - c[i - 1], 0) / p.build_years
        b[i] = b[i - 1] + DT * adoption
        c[i] = c[i - 1] + DT * build
    df = pd.DataFrame({"t": t, "bev": b, "points": c})
    df["bev_per_point"] = df["bev"] / df["points"]
    return df


def yearly(df: pd.DataFrame) -> pd.DataFrame:
    y = df[np.isclose(df["t"] % 1, 0)].copy()
    y["year"] = y["t"].round().astype(int)
    return y.drop(columns="t").set_index("year")


def calibrate(bev_obs: dict, points_obs: dict, k: float, elasticity: float, bev_per_point: float):
    """Fit adoption rate r and build time to the observed BEV stock and public points
    (grid search, least squares on log values of both series)."""
    years = sorted(y for y in bev_obs if y in points_obs)
    start, end = years[0], years[-1]
    b0, c0 = bev_obs[start], points_obs[start]
    best = None
    for r in np.linspace(0.05, 1.2, 116):
        for build in (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0):
            p = Params(r, k, elasticity, build, bev_per_point, a_ref=c0 / b0)
            y = yearly(simulate(p, b0, c0, start, end))
            err = sum((np.log(y.loc[t, "bev"]) - np.log(bev_obs[t])) ** 2
                      + (np.log(y.loc[t, "points"]) - np.log(points_obs[t])) ** 2 for t in years)
            if best is None or err < best[0]:
                best = (err, p)
    return best[1], float(np.sqrt(best[0] / (2 * len(years))))


def run_scenarios(bev_obs: dict, points_obs: dict, car_stock: int, points_now: int) -> dict:
    """Calibrate each scenario on 2023-2026, project to FORECAST_END; compare with today's network."""
    start = config.FORECAST_START
    # operators build toward today's ratio of BEVs per public point (it has fallen every year,
    # so using the latest value is the conservative choice)
    latest = max(y for y in bev_obs if y in points_obs)
    bev_per_point = bev_obs[latest] / points_obs[latest]
    out = {"observed": {"bev": bev_obs, "points": points_obs}, "bev_per_point_target": bev_per_point,
           "points_now": points_now, "scenarios": {}}
    end = config.FORECAST_END
    for name, s in config.SCENARIOS.items():
        p, rmse = calibrate(bev_obs, points_obs, s["k_share"] * car_stock, s["elasticity"], bev_per_point)
        y = yearly(simulate(p, bev_obs[start], points_obs[start], start, end))
        need = y.loc[end, "points"] - points_now
        out["scenarios"][name] = {
            "params": {"r": round(p.r, 3), "k": int(p.k), "elasticity": p.elasticity, "build_years": p.build_years},
            "fit_rmse_log": round(rmse, 4),
            "bev": {int(k): int(v) for k, v in y["bev"].items()},
            "points": {int(k): int(v) for k, v in y["points"].items()},
            "points_to_add": int(need),
            "points_per_year_needed": int(need / (end - latest + 1)),
        }
    return out


def recent_build_pace(history: pd.DataFrame, years=(2024, 2025)) -> float:
    """Average public points added per full year recently (survivors in today's register)."""
    h = history.set_index("year")
    return float(h.loc[list(years), ["AC", "DC", "HPC"]].sum(axis=1).mean())
