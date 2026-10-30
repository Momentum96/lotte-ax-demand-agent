"""Hierarchical reconciliation (bottom-up and MinT-OLS) for the hs6 -> partner hierarchy."""
from __future__ import annotations

import numpy as np
import pandas as pd


def summing_matrix(hierarchy: dict[str, list[str]]) -> tuple[np.ndarray, list[str], list[str]]:
    bottoms = [c for kids in hierarchy.values() for c in kids]
    parents = list(hierarchy)
    S = np.zeros((len(parents) + len(bottoms), len(bottoms)))
    for i, p in enumerate(parents):
        for c in hierarchy[p]:
            S[i, bottoms.index(c)] = 1.0
    S[len(parents):, :] = np.eye(len(bottoms))
    return S, parents + bottoms, bottoms


def reconcile_mint_ols(fc: pd.DataFrame, hierarchy: dict[str, list[str]], value_col: str = "yhat") -> pd.DataFrame:
    """fc: rows for one origin with unique_id, h, ds, yhat (all nodes). Returns reconciled copy."""
    S, order, bottoms = summing_matrix(hierarchy)
    P = np.linalg.pinv(S.T @ S) @ S.T          # MinT with identity covariance == OLS
    G = S @ P
    out = fc.copy()
    for h, g in fc.groupby("h"):
        vec = g.set_index("unique_id")[value_col].reindex(order).to_numpy()
        if np.isnan(vec).any():
            continue
        rec = np.clip(G @ vec, 0, None)
        m = dict(zip(order, rec))
        idx = out["h"] == h
        out.loc[idx, value_col] = out.loc[idx, "unique_id"].map(m).values
    return out


def reconcile_bottom_up(fc: pd.DataFrame, hierarchy: dict[str, list[str]], value_col: str = "yhat") -> pd.DataFrame:
    out = fc.copy()
    for parent, kids in hierarchy.items():
        for h, g in fc[fc["unique_id"].isin(kids)].groupby("h"):
            total = g[value_col].sum()
            out.loc[(out["unique_id"] == parent) & (out["h"] == h), value_col] = total
    return out
