"""Assemble feature vectors for training and live projection."""
import json
from datetime import date

import numpy as np
import pandas as pd

from .. import config
from .store import FeatureStore

FEATURE_COLUMNS = [
    "blended_k_pct", "k_pct_ewma", "csw_ewma", "swstr_ewma", "kbb_pct_ewma",
    "prior_k_pct", "stab_weight", "fb_velo_delta", "bf_avg3", "days_rest",
    "opp_k_pct", "lineup_confidence", "park_k_factor", "ump_factor",
    "temp_f", "wind_mph", "is_home", "run_env", "month",
]


def _date_of(s: str) -> date:
    return date(int(s[:4]), int(s[5:7]), int(s[8:10]))


def _weather_fill(temp, wind, roof_type):
    if roof_type == "dome":
        return config.DOME_TEMP_F, 0.0
    t = float(temp) if temp is not None else config.DOME_TEMP_F
    w = float(wind) if wind is not None else 6.0
    if roof_type == "retractable":  # roof may be shut; attenuate toward indoor
        t = 0.5 * t + 0.5 * config.DOME_TEMP_F
        w *= 0.5
    return t, w


def actual_batters_faced(con, game_pk: int, opp_team: str, hand: str) -> list[int]:
    rows = con.execute(
        """SELECT batter_id FROM batter_game_vs_hand
           WHERE game_pk=? AND team=? AND vs_hand=? ORDER BY pa DESC, batter_id LIMIT 9""",
        (game_pk, opp_team, hand),
    ).fetchall()
    return [r["batter_id"] for r in rows]


def confirmed_lineup(con, game_pk: int, team: str) -> list[int]:
    rows = con.execute(
        """SELECT batter_id FROM lineups WHERE game_pk=? AND team=? AND source='confirmed'
           ORDER BY batting_order""",
        (game_pk, team),
    ).fetchall()
    return [r["batter_id"] for r in rows]


def pitcher_hand(con, pid: int) -> str:
    row = con.execute("SELECT throws FROM players WHERE mlb_id=?", (pid,)).fetchone()
    return (row["throws"] if row and row["throws"] in ("L", "R") else "R")


def training_frame(con, store: FeatureStore, augment=True, seed=42):
    """X, y, meta for every historical start. Features are strictly as-of (no leakage)."""
    rng = np.random.default_rng(seed)
    starts = con.execute(
        """SELECT l.pitcher_id, l.game_pk, l.date, l.team, l.opp, l.is_home, l.k, l.p_throws,
                  g.venue_name, g.ump_name, g.temp_f, g.wind_mph, g.home_team, g.away_team
           FROM pitcher_game_logs l JOIN games g ON g.game_pk = l.game_pk
           WHERE l.started=1 ORDER BY l.date""",
    ).fetchall()
    X_rows, y, meta = [], [], []
    for s in starts:
        d = _date_of(s["date"])
        hand = s["p_throws"] if s["p_throws"] in ("L", "R") else pitcher_hand(con, s["pitcher_id"])
        pf = store.pitcher_features(s["pitcher_id"], d)

        ids = actual_batters_faced(con, s["game_pk"], s["opp"], hand)
        u = rng.random() if augment else 1.0
        if ids and u >= config.TRAIN_AUGMENT_DEGRADED_LINEUP:
            opp_k, conf, _ = store.opponent_features(s["opp"], hand, d, actual_ids=ids)
        elif u >= config.TRAIN_AUGMENT_DEGRADED_LINEUP / 2 or not ids:
            opp_k, conf = store.team_aggregate_k_pct(s["opp"], hand, d), 0.3
        else:
            common = store.common_lineup(s["opp"], hand, d)
            if common:
                opp_k, conf = store.lineup_k_pct(common, hand, d), 0.6
            else:
                opp_k, conf = store.team_aggregate_k_pct(s["opp"], hand, d), 0.3

        park = store.park_features(s["venue_name"])
        temp, wind = _weather_fill(s["temp_f"], s["wind_mph"], park["roof_type"])
        ump = store.ump_factor(s["ump_name"]) if rng.random() > 0.3 else 1.0  # dropout

        X_rows.append([
            pf["blended_k_pct"], pf["k_pct_ewma"], pf["csw_ewma"], pf["swstr_ewma"],
            pf["kbb_pct_ewma"], pf["prior_k_pct"], pf["stab_weight"], pf["fb_velo_delta"],
            pf["bf_avg3"], pf["days_rest"], opp_k, conf, park["park_k_factor"], ump,
            temp, wind, int(s["is_home"]), store.run_environment(s["home_team"], s["away_team"], d),
            d.month,
        ])
        y.append(int(s["k"]))
        meta.append((s["game_pk"], s["pitcher_id"], s["date"]))
    X = pd.DataFrame(X_rows, columns=FEATURE_COLUMNS)
    meta_df = pd.DataFrame(meta, columns=["game_pk", "pitcher_id", "date"])
    return X, np.array(y), meta_df


def live_feature_row(con, store: FeatureStore, game, pitcher_id: int, team: str) -> dict:
    """Feature vector + metadata for one upcoming start."""
    d = _date_of(game["date"])
    is_home = 1 if game["home_team"] == team else 0
    opp = game["away_team"] if is_home else game["home_team"]
    hand = pitcher_hand(con, pitcher_id)
    pf = store.pitcher_features(pitcher_id, d)

    confirmed = confirmed_lineup(con, game["game_pk"], opp)
    opp_k, conf, tier = store.opponent_features(opp, hand, d, confirmed_ids=confirmed or None)

    park = store.park_features(game["venue_name"])
    temp, wind = _weather_fill(game["temp_f"], game["wind_mph"], park["roof_type"])

    from ..ingest.odds import latest_total_for_game
    vegas = latest_total_for_game(con, game["game_pk"])
    run_env = float(vegas["total"]) if vegas and vegas.get("total") else \
        store.run_environment(game["home_team"], game["away_team"], d)

    feats = {
        "blended_k_pct": pf["blended_k_pct"], "k_pct_ewma": pf["k_pct_ewma"],
        "csw_ewma": pf["csw_ewma"], "swstr_ewma": pf["swstr_ewma"],
        "kbb_pct_ewma": pf["kbb_pct_ewma"], "prior_k_pct": pf["prior_k_pct"],
        "stab_weight": pf["stab_weight"], "fb_velo_delta": pf["fb_velo_delta"],
        "bf_avg3": pf["bf_avg3"], "days_rest": pf["days_rest"],
        "opp_k_pct": opp_k, "lineup_confidence": conf,
        "park_k_factor": park["park_k_factor"],
        "ump_factor": store.ump_factor(game["ump_name"]),
        "temp_f": temp, "wind_mph": wind, "is_home": is_home,
        "run_env": run_env, "month": d.month,
    }
    return {
        "features": feats,
        "vector": [feats[c] for c in FEATURE_COLUMNS],
        "lineup_confidence": conf,
        "lineup_tier": tier,
        "opp": opp,
        "hand": hand,
        "features_json": json.dumps({**feats, "lineup_tier": tier}, default=float),
    }
