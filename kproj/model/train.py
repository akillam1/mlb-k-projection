"""LightGBM Poisson point model + quantile heads (roadmap §5.4, §5.5).

Weekly full retrain on an expanding window; out-of-time validation on the most
recent ~15% of start dates. Model artifacts are plain-text LightGBM files in
models/, registry tracked in SQLite. Uses the native lgb.train API (no
scikit-learn dependency).
"""
import json
from datetime import datetime, timezone

import lightgbm as lgb
import numpy as np

from .. import config, db
from ..features.build import FEATURE_COLUMNS, training_frame
from ..features.store import FeatureStore

POINT_PARAMS = dict(
    objective="poisson", learning_rate=0.03, num_leaves=31,
    min_data_in_leaf=40, bagging_fraction=0.8, bagging_freq=1, feature_fraction=0.8,
    lambda_l1=0.1, lambda_l2=1.0, verbose=-1,
)
QUANTILE_PARAMS = dict(
    objective="quantile", learning_rate=0.04, num_leaves=31,
    min_data_in_leaf=60, bagging_fraction=0.8, bagging_freq=1, feature_fraction=0.8,
    lambda_l1=0.1, lambda_l2=1.0, verbose=-1,
)
POINT_ROUNDS, QUANTILE_ROUNDS = 2000, 1200


def _mean_poisson_deviance(y, mu):
    mu = np.clip(mu, 1e-6, None)
    y = np.asarray(y, dtype=float)
    term = np.where(y > 0, y * np.log(np.where(y > 0, y, 1) / mu), 0.0)
    return float(np.mean(2 * (term - (y - mu))))


def train(con, quick=False, progress=print) -> dict | None:
    store = FeatureStore(con)
    X, y, meta = training_frame(con, store, augment=True)
    if len(y) < (200 if quick else config.MIN_TRAIN_ROWS):
        progress(f"[train] only {len(y)} rows — need more backfill; skipping")
        return None

    order = np.argsort(meta["date"].values)
    X, y, meta = X.iloc[order].reset_index(drop=True), y[order], meta.iloc[order]
    cut = int(len(y) * 0.85)
    cut_date = meta["date"].iloc[cut]  # don't split mid-date
    cut = int(np.searchsorted(meta["date"].values, cut_date))
    X_tr, y_tr, X_va, y_va = X.iloc[:cut], y[:cut], X.iloc[cut:], y[cut:]
    progress(f"[train] {len(y_tr)} train / {len(y_va)} valid (valid from {cut_date})")

    pp, qp = dict(POINT_PARAMS), dict(QUANTILE_PARAMS)
    p_rounds, q_rounds = POINT_ROUNDS, QUANTILE_ROUNDS
    if quick:
        pp.update(min_data_in_leaf=5)
        qp.update(min_data_in_leaf=5)
        p_rounds, q_rounds = 150, 100
    dtrain = lgb.Dataset(X_tr, label=y_tr, feature_name=FEATURE_COLUMNS)
    dvalid = lgb.Dataset(X_va, label=y_va, reference=dtrain)

    def cbs():
        return [lgb.early_stopping(100, verbose=False), lgb.log_evaluation(0)]

    point = lgb.train(pp, dtrain, num_boost_round=p_rounds,
                      valid_sets=[dvalid], callbacks=cbs())
    heads = {}
    for q in config.QUANTILES:
        heads[q] = lgb.train({**qp, "alpha": q}, dtrain, num_boost_round=q_rounds,
                             valid_sets=[dvalid], callbacks=cbs())

    mu = np.clip(point.predict(X_va), 0.05, None)
    mae = float(np.mean(np.abs(y_va - mu)))
    dev = _mean_poisson_deviance(y_va, mu)
    version = "v" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    progress(f"[train] {version}: valid MAE={mae:.3f}, poisson_dev={dev:.3f}")

    config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    point.save_model(str(config.MODELS_DIR / f"{version}_point.txt"))
    for q, m in heads.items():
        m.save_model(str(config.MODELS_DIR / f"{version}_q{int(q * 100)}.txt"))

    con.execute("UPDATE model_registry SET active=0")
    db.upsert(con, "model_registry", [{
        "version": version, "trained_at": db.utcnow(),
        "train_rows": int(len(y_tr)), "valid_rows": int(len(y_va)),
        "valid_mae": round(mae, 4), "valid_poisson_dev": round(dev, 4),
        "params_json": json.dumps({"point": pp, "quantile": qp, "features": FEATURE_COLUMNS}),
        "active": 1,
    }])
    con.commit()
    return {"version": version, "mae": mae}


def load_active(con) -> dict | None:
    row = con.execute(
        "SELECT version FROM model_registry WHERE active=1 ORDER BY trained_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    version = row["version"]
    try:
        bundle = {
            "version": version,
            "point": lgb.Booster(model_file=str(config.MODELS_DIR / f"{version}_point.txt")),
            "quantiles": {
                q: lgb.Booster(model_file=str(config.MODELS_DIR / f"{version}_q{int(q * 100)}.txt"))
                for q in config.QUANTILES
            },
        }
    except lgb.basic.LightGBMError:
        return None
    return bundle
