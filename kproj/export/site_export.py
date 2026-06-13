"""Export compact JSON for the static GitHub Pages dashboard (roadmap §7)."""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import config, db, util

ET = ZoneInfo(config.ET_ZONE)


def _write(name: str, payload) -> None:
    config.SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.SITE_DATA_DIR / name, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"), default=float)


def _et_time(first_pitch_utc: str | None) -> str:
    if not first_pitch_utc:
        return ""
    try:
        dt = datetime.fromisoformat(first_pitch_utc.replace("Z", "+00:00"))
        return dt.astimezone(ET).strftime("%I:%M %p ET").lstrip("0")
    except ValueError:
        return ""


def export_today(con, d) -> None:
    date_s = util.iso(d) if not isinstance(d, str) else d
    rows = con.execute(
        """SELECT g.game_pk, g.date, g.home_team, g.away_team, g.first_pitch_utc, g.status,
                  g.temp_f, g.wind_mph, g.venue_name,
                  ps.team, ps.pitcher_id, ps.pitcher_name,
                  p.id AS proj_id, p.point_est, p.p10, p.p25, p.p50, p.p75, p.p90,
                  p.lineup_confidence, p.model_version, p.generated_at, p.features_json
           FROM games g
           JOIN probable_starters ps ON ps.game_pk = g.game_pk
           LEFT JOIN projections p ON p.game_pk = g.game_pk AND p.pitcher_id = ps.pitcher_id
                AND p.is_latest = 1
           WHERE g.date = ?
           ORDER BY g.first_pitch_utc, g.game_pk""",
        (date_s,),
    ).fetchall()
    starters = []
    for r in rows:
        opp_team = r["away_team"] if r["team"] == r["home_team"] else r["home_team"]
        entry = {
            "game_pk": r["game_pk"],
            "pitcher": r["pitcher_name"],
            "pitcher_id": r["pitcher_id"],
            "team": r["team"],
            "opp": opp_team,
            "home": r["team"] == r["home_team"],
            "time_et": _et_time(r["first_pitch_utc"]),
            "status": r["status"],
            "venue": r["venue_name"],
            "temp_f": r["temp_f"],
            "wind_mph": r["wind_mph"],
        }
        if r["proj_id"]:
            tier = "?"
            try:
                tier = json.loads(r["features_json"]).get("lineup_tier", "?")
            except (TypeError, ValueError):
                pass
            entry.update({
                "proj": {
                    "point": r["point_est"], "p10": r["p10"], "p25": r["p25"],
                    "p50": r["p50"], "p75": r["p75"], "p90": r["p90"],
                    "lineup_confidence": r["lineup_confidence"], "lineup_tier": tier,
                    "model_version": r["model_version"], "generated_at": r["generated_at"],
                },
                "edges": _edges_for(con, r["game_pk"], r["pitcher_id"]),
            })
        starters.append(entry)
    _write("today.json", {"date": date_s, "generated_at": db.utcnow(), "starters": starters})


def _edges_for(con, game_pk: int, pitcher_id: int) -> list:
    rows = con.execute(
        """SELECT book, line, side, odds, model_prob, vigfree_prob, ev_per_unit,
                  kelly_quarter, score
           FROM opportunities WHERE game_pk=? AND pitcher_id=? AND is_latest=1
           ORDER BY score DESC""",
        (game_pk, pitcher_id),
    ).fetchall()
    return [dict(r) for r in rows]


def _proj_metrics(con, since: str | None) -> dict:
    where = "WHERE date >= ?" if since else ""
    args = (since,) if since else ()
    r = con.execute(
        f"""SELECT COUNT(*) n, AVG(abs_error) mae, AVG(signed_error) bias,
                   AVG(in_band_10_90)*100 coverage
            FROM projection_results {where}""",
        args,
    ).fetchone()
    return {
        "n": r["n"] or 0,
        "mae": round(r["mae"], 3) if r["mae"] is not None else None,
        "bias": round(r["bias"], 3) if r["bias"] is not None else None,
        "band_coverage_pct": round(r["coverage"], 1) if r["coverage"] is not None else None,
    }


def _bet_metrics(con, since: str | None) -> dict:
    """Betting record over POSITIVE-EV picks only (what the site surfaces)."""
    where = "AND b.date >= ?" if since else ""
    args = (since,) if since else ()
    r = con.execute(
        f"""SELECT COUNT(*) n,
                   SUM(b.pnl_units) units,
                   AVG(CASE WHEN b.result='win' THEN 1.0 WHEN b.result='loss' THEN 0.0 END)*100 hit,
                   AVG(b.clv_pct) clv
            FROM bet_results b
            JOIN opportunities o ON o.id = b.opportunity_id
            WHERE o.ev_per_unit > 0 {where}""",
        args,
    ).fetchone()
    n = r["n"] or 0
    units = r["units"] or 0.0
    return {
        "n": n,
        "units": round(units, 2),
        "roi_pct": round(units / n * 100, 2) if n else None,
        "hit_pct": round(r["hit"], 1) if r["hit"] is not None else None,
        "avg_clv_pct": round(r["clv"], 2) if r["clv"] is not None else None,
        "low_sample": n < 500,
    }


def _calibration(con) -> list:
    rows = con.execute(
        """SELECT CAST(b.model_prob*20 AS INT) bucket,
                  COUNT(*) n, AVG(b.model_prob)*100 pred,
                  AVG(CASE WHEN b.result='win' THEN 1.0 WHEN b.result='loss' THEN 0.0 END)*100 actual
           FROM bet_results b
           JOIN opportunities o ON o.id = b.opportunity_id
           WHERE o.ev_per_unit > 0 AND b.result IN ('win','loss')
           GROUP BY bucket HAVING COUNT(*) >= 5 ORDER BY bucket""",
    ).fetchall()
    return [
        {"pred_pct": round(r["pred"], 1), "actual_pct": round(r["actual"], 1), "n": r["n"]}
        for r in rows
    ]


def export_performance(con) -> None:
    today = datetime.now(timezone.utc).date()
    d30 = (today - timedelta(days=30)).isoformat()
    d7 = (today - timedelta(days=7)).isoformat()
    daily = con.execute(
        """SELECT date, COUNT(*) n, AVG(abs_error) mae FROM projection_results
           WHERE date >= ? GROUP BY date ORDER BY date""",
        ((today - timedelta(days=60)).isoformat(),),
    ).fetchall()
    versions = con.execute(
        """SELECT m.version, m.trained_at, m.train_rows, m.valid_mae, m.active,
                  (SELECT COUNT(*) FROM projection_results pr WHERE pr.model_version = m.version) n_scored,
                  (SELECT AVG(pr.abs_error) FROM projection_results pr WHERE pr.model_version = m.version) live_mae
           FROM model_registry m ORDER BY m.trained_at DESC""",
    ).fetchall()
    _write("performance.json", {
        "generated_at": db.utcnow(),
        "projection": {
            "lifetime": _proj_metrics(con, None),
            "t30": _proj_metrics(con, d30),
            "t7": _proj_metrics(con, d7),
        },
        "betting": {
            "lifetime": _bet_metrics(con, None),
            "t30": _bet_metrics(con, d30),
            "t7": _bet_metrics(con, d7),
        },
        "calibration": _calibration(con),
        "daily_mae": [{"date": r["date"], "mae": round(r["mae"], 3), "n": r["n"]} for r in daily],
        "versions": [
            {
                "version": r["version"], "trained_at": r["trained_at"],
                "train_rows": r["train_rows"], "valid_mae": r["valid_mae"],
                "active": bool(r["active"]), "n_scored": r["n_scored"],
                "live_mae": round(r["live_mae"], 3) if r["live_mae"] is not None else None,
            }
            for r in versions
        ],
    })


def export_recent(con) -> None:
    today = datetime.now(timezone.utc).date()
    since = (today - timedelta(days=21)).isoformat()
    rows = con.execute(
        """SELECT pr.date, pl.name pitcher, pr.point_est, pr.actual_k, pr.signed_error,
                  p.p10, p.p90
           FROM projection_results pr
           JOIN projections p ON p.id = pr.projection_id
           LEFT JOIN players pl ON pl.mlb_id = pr.pitcher_id
           WHERE pr.date >= ? ORDER BY pr.date DESC, pr.abs_error DESC""",
        (since,),
    ).fetchall()
    bets = con.execute(
        """SELECT b.date, pl.name pitcher, b.side, b.line, b.odds, b.model_prob,
                  b.result, b.pnl_units, b.clv_pct, o.book
           FROM bet_results b
           JOIN opportunities o ON o.id = b.opportunity_id
           LEFT JOIN players pl ON pl.mlb_id = o.pitcher_id
           WHERE b.date >= ? AND o.ev_per_unit > 0 ORDER BY b.date DESC""",
        (since,),
    ).fetchall()
    _write("recent.json", {
        "results": [dict(r) for r in rows],
        "bets": [dict(r) for r in bets],
    })


def export_meta(con) -> None:
    mv = con.execute(
        "SELECT version, trained_at, valid_mae FROM model_registry WHERE active=1"
    ).fetchone()
    span = con.execute("SELECT MIN(date) lo, MAX(date) hi, COUNT(*) n FROM pitcher_game_logs").fetchone()
    budget = con.execute(
        "SELECT remaining FROM api_budget WHERE provider='oddsapi' ORDER BY month DESC LIMIT 1"
    ).fetchone()
    _write("meta.json", {
        "generated_at": db.utcnow(),
        "model_version": mv["version"] if mv else None,
        "model_trained_at": mv["trained_at"] if mv else None,
        "model_valid_mae": mv["valid_mae"] if mv else None,
        "data_from": span["lo"], "data_to": span["hi"], "game_log_rows": span["n"],
        "odds_credits_remaining": budget["remaining"] if budget else None,
    })


def export_all(con, d) -> None:
    export_today(con, d)
    export_performance(con)
    export_recent(con)
    export_meta(con)
