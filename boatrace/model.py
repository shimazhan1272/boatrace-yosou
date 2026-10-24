"""1着確率モデル（LightGBM）と、着順組合せ確率の計算。

モデルは2種類:
  base : 出走表のみで予測（朝の時点で全レース出せる）
  full : 出走表 + 直前情報（展示タイム・進入コース・気象）で予測
"""
import json
from itertools import permutations

import lightgbm as lgb
import numpy as np
import pandas as pd

from .config import MODEL_DIR
from .features import BASE_FEATURES, CATEGORICAL, PREVIEW_FEATURES

FEATURES = {"base": BASE_FEATURES, "full": PREVIEW_FEATURES}

PARAMS = {
    "objective": "binary",
    "learning_rate": 0.03,
    "num_leaves": 63,
    "min_data_in_leaf": 100,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "verbose": -1,
}


def _dataset(df, kind):
    X = df[FEATURES[kind]].astype(float)
    y = (df["place"] == 1).astype(int)
    return lgb.Dataset(X, y, categorical_feature=CATEGORICAL, free_raw_data=False)


def train(df: pd.DataFrame, kind: str, valid: pd.DataFrame | None = None, rounds: int = 2000):
    train_set = _dataset(df, kind)
    callbacks = [lgb.log_evaluation(200)]
    valid_sets = [train_set]
    if valid is not None:
        valid_sets.append(_dataset(valid, kind))
        callbacks.append(lgb.early_stopping(100, verbose=False))
    booster = lgb.train(PARAMS, train_set, rounds, valid_sets=valid_sets, callbacks=callbacks)
    return booster


def save(booster, kind: str, meta: dict):
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    booster.save_model(MODEL_DIR / f"{kind}.txt")
    (MODEL_DIR / f"{kind}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def load(kind: str):
    return lgb.Booster(model_file=str(MODEL_DIR / f"{kind}.txt"))


def win_probs(booster, df: pd.DataFrame, kind: str) -> pd.Series:
    """レース内で合計1になるよう正規化した1着確率。"""
    p = pd.Series(booster.predict(df[FEATURES[kind]].astype(float)), index=df.index)
    return p / p.groupby(df["race_id"]).transform("sum")


def trifecta_probs(p: dict[int, float]) -> list[tuple[str, float]]:
    """Harville モデルで3連単全通りの確率を計算し、高い順に返す。"""
    out = []
    for a, b, c in permutations(p, 3):
        pa, pb, pc = p[a], p[b], p[c]
        denom1 = 1 - pa
        denom2 = 1 - pa - pb
        if denom1 <= 0 or denom2 <= 0:
            continue
        out.append((f"{a}-{b}-{c}", pa * pb / denom1 * pc / denom2))
    out.sort(key=lambda t: -t[1])
    return out


def exacta_probs(p: dict[int, float]) -> list[tuple[str, float]]:
    out = [(f"{a}-{b}", p[a] * p[b] / (1 - p[a])) for a, b in permutations(p, 2) if p[a] < 1]
    out.sort(key=lambda t: -t[1])
    return out


def place_probs(tri: list[tuple[str, float]]) -> dict[int, tuple[float, float]]:
    """3連単確率から各艇の 2着以内・3着以内 確率を集計する。"""
    top2: dict[int, float] = {}
    top3: dict[int, float] = {}
    for combo, q in tri:
        a, b, c = map(int, combo.split("-"))
        for i, boat in enumerate((a, b, c)):
            top3[boat] = top3.get(boat, 0) + q
            if i < 2:
                top2[boat] = top2.get(boat, 0) + q
    return {k: (top2.get(k, 0.0), top3[k]) for k in top3}


def log_loss_per_race(df: pd.DataFrame, prob_col: str = "p") -> float:
    win = df[df["place"] == 1]
    return float(-np.log(win[prob_col].clip(1e-6)).mean())
