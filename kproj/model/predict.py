"""Inference: project today's probable starters; smooth CDF for P(over/under).

CDF: monotone PCHIP through the five quantile heads with anchored tails
(roadmap §6.2). Whole-number lines handle push probability explicitly.
"""
import numpy as np
from scipy.interpolate import PchipInterpolator

from .. import config, db
from ..features.build import live_feature_row
from ..features.store import FeatureStore
from .train import load_active


def predict_distribution(models: dict, vector: list) -> dict:
    X = np.array([vector], dtype=float)
    point = float(np.clip(models["point"].predict(X)[0], 0.05, None))
    qs = {}
    for q, booster in models["quantiles"].items():
        qs[q] = float(np.clip(booster.predict(X)[0], 0.0, None))
    # enforce monotone quantiles
    vals = [qs[q] for q in config.QUANTILES]
    vals = list(np.maximum.accumulate(vals))
    return {"point": point, **{f"p{int(q * 100)}": v for q, v in zip(config.QUANTILES, vals)}}


def _cdf(dist: dict) -> PchipInterpolator:
    xs = [dist[f"p{int(q * 100)}"] for q in config.QUANTILES]
    ys = list(config.QUANTILES)
    spread = max(xs[-1] - xs[0], 1.0)
    lo = max(xs[0] - 0.8 * spread, -0.49)
    hi = max(xs[-1] + 1.2 * spread, xs[-1] + 2.0)
    pts_x, pts_y = [lo], [0.003]
    for x, yy in zip(xs, ys):
        # strictly increasing x for PCHIP
        if x <= pts_x[-1]:
            x = pts_x[-1] + 1e-4
        pts_x.append(x)
        pts_y.append(yy)
    pts_x.append(max(hi, pts_x[-1] + 1e-3))
    pts_y.append(0.997)
    return PchipInterpolator(pts_x, pts_y, extrapolate=False)


def probs_for_line(dist: dict, line: float) -> dict:
    """P(over), P(under), P(push) for a K line given the projected distribution."""
    F = _cdf(dist)

    def cdf(x: float) -> float:
        lo_x, hi_x = F.x[0], F.x[-1]
        if x <= lo_x:
            return 0.0
        if x >= hi_x:
            return 1.0
        return float(np.clip(F(x), 0.0, 1.0))

    if abs(line - round(line)) > 1e-9:  # half line: no push
        p_under = cdf(line)
        return {"over": 1 - p_under, "under": p_under, "push": 0.0}
    p_under = cdf(line - 0.5)
    p_over = 1 - cdf(line + 0.5)
    return {"over": p_over, "under": p_under, "push": max(0.0, 1 - p_over - p_under)}


def slate_games(con, d) -> list:
    return con.execute(
        """SELECT g.*, ps.team AS p_team, ps.pitcher_id, ps.pitcher_name
           FROM games g JOIN probable_starters ps ON ps.game_pk = g.game_pk
           WHERE g.date = ? AND g.status NOT LIKE 'Final%' AND g.status NOT LIKE 'Cancel%'
           ORDER BY g.first_pitch_utc""",
        (d if isinstance(d, str) else d.strftime("%Y-%m-%d"),),
    ).fetchall()


def project_date(con, d, progress=print) -> int:
    models = load_active(con)
    if not models:
        progress("[project] no active model — run retrain first")
        return 0
    store = FeatureStore(con)
    rows = slate_games(con, d)
    n = 0
    for r in rows:
        game = dict(r)
        try:
            lf = live_feature_row(con, store, game, r["pitcher_id"], r["p_team"])
        except Exception as e:  # noqa: BLE001 — one bad starter shouldn't kill the slate
            progress(f"[project] {r['pitcher_name']}: {e}")
            continue
        dist = predict_distribution(models, lf["vector"])
        con.execute(
            "UPDATE projections SET is_latest=0 WHERE game_pk=? AND pitcher_id=?",
            (r["game_pk"], r["pitcher_id"]),
        )
        con.execute(
            """INSERT INTO projections
               (game_pk, pitcher_id, model_version, generated_at, point_est,
                p10, p25, p50, p75, p90, lineup_confidence, features_json, is_latest)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)""",
            (
                r["game_pk"], r["pitcher_id"], models["version"], db.utcnow(),
                round(dist["point"], 3), round(dist["p10"], 2), round(dist["p25"], 2),
                round(dist["p50"], 2), round(dist["p75"], 2), round(dist["p90"], 2),
                lf["lineup_confidence"], lf["features_json"],
            ),
        )
        n += 1
    con.commit()
    progress(f"[project] wrote {n} projections for {d}")
    return n
