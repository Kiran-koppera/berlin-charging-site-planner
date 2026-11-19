"""Competitor-entry model: how likely is it that some operator opens HPC next to a site soon?

Revealed preference: operators' own build decisions show which locations they find attractive.
A regularised logistic regression (plain NumPy) learns from the network as it stood on
1 Jan 2023 which candidate sites received a new HPC point within 300 m in 2023-2024, and is then
tested out-of-time: features from the network on 1 Jan 2025, labels = openings in 2025-2026.

Use: a site with high opportunity *and* high entry probability is one to secure first; a
competitor is likely to take it otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import config
from .areas import count_within, to_xy
from .sites import auc

HIT_RADIUS_M = 300


def features(c: pd.DataFrame) -> pd.DataFrame:
    f = pd.DataFrame({
        "log_traffic": np.log1p(c["traffic_dtv"]),
        "log_dist_hpc": np.log1p(c["nearest_hpc_m"].clip(upper=10_000)),
        "log_hpc_1500m": np.log1p(c["hpc_within"]),
        "log_fast_500m": np.log1p(c["fast_within_500"]),
        "log_gap": np.log1p(c["gap_density"]),
    }, index=c.index)
    for k in sorted(config.POI_CLASSES):
        f["host_" + k] = (c["kind"] == k).astype(float)
    return f


def labels(c: pd.DataFrame, points: pd.DataFrame, start: str, end: str) -> np.ndarray:
    new = points[(points["commissioned"] >= pd.Timestamp(start)) & (points["commissioned"] < pd.Timestamp(end))
                 & (points["cls"] == "HPC")]
    x, y = to_xy(new["lon"], new["lat"])
    return (count_within(c["x"], c["y"], x, y, HIT_RADIUS_M) > 0).astype(float)


@dataclass
class EntryModel:
    columns: list
    mu: pd.Series
    sd: pd.Series
    w: np.ndarray

    def predict(self, c: pd.DataFrame) -> np.ndarray:
        z = ((features(c)[self.columns] - self.mu) / self.sd).to_numpy()
        with np.errstate(all="ignore"):
            return 1 / (1 + np.exp(-(self.w[0] + z @ self.w[1:])))

    def coefficients(self) -> dict:
        return {"intercept": float(self.w[0]), **{k: float(v) for k, v in zip(self.columns, self.w[1:])}}


def fit(c: pd.DataFrame, y: np.ndarray, l2: float = 1.0, iters: int = 4000, lr: float = 0.5) -> EntryModel:
    f = features(c)
    mu, sd = f.mean(), f.std().replace(0, 1)
    z = ((f - mu) / sd).to_numpy()
    a = np.c_[np.ones(len(z)), z]
    w = np.zeros(a.shape[1])
    with np.errstate(all="ignore"):
        for _ in range(iters):
            p = 1 / (1 + np.exp(-(a @ w)))
            g = a.T @ (p - y) / len(a) + l2 * np.r_[0, w[1:]] / len(a)
            w -= lr * g
    return EntryModel(list(f.columns), mu, sd, w)


def validate(train: pd.DataFrame, y_train: np.ndarray, test: pd.DataFrame, y_test: np.ndarray) -> tuple[EntryModel, dict]:
    m = fit(train, y_train)
    p_tr, p_te = m.predict(train), m.predict(test)
    top = p_te >= np.quantile(p_te, 0.8)
    return m, {
        "train_auc": auc(p_tr, y_train), "test_auc": auc(p_te, y_test),
        "train_positive_rate": float(y_train.mean()), "test_positive_rate": float(y_test.mean()),
        "test_hit_rate_top20": float(y_test[top].mean()), "test_hit_rate_rest": float(y_test[~top].mean()),
        "coefficients": m.coefficients(),
    }
