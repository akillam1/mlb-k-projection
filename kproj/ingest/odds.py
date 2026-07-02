"""The Odds API — FREE TIER ONLY (game totals + moneylines, ~2 credits/call).

Pitcher K props require the paid plan and are PARKED (see PARKING_LOT.md).
Game totals feed the expected-batters-faced feature; that's all we use here.
Budget guard: stops calling when monthly credits run low.
"""
from datetime import datetime, timezone

import requests

from .. import config, db, util

FULL_TO_ABBR = {
    "Arizona Diamondbacks": "ARI", "Atlanta Braves": "ATL", "Baltimore Orioles": "BAL",
    "Boston Red Sox": "BOS", "Chicago Cubs": "CHC", "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE", "Colorado Rockies": "COL",
    "Detroit Tigers": "DET", "Houston Astros": "HOU", "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD", "Miami Marlins": "MIA",
    "Milwaukee Brewers": "MIL", "Minnesota Twins": "MIN", "New York Mets": "NYM",
    "New York Yankees": "NYY", "Athletics": "ATH", "Oakland Athletics": "ATH",
    "Philadelphia Phillies": "PHI", "Pittsburgh Pirates": "PIT", "San Diego Padres": "SD",
    "San Francisco Giants": "SF", "Seattle Mariners": "SEA", "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TB", "Texas Rangers": "TEX", "Toronto Blue Jays": "TOR",
    "Washington Nationals": "WSH", "Sacramento Athletics": "ATH",
}


def budget_ok(con) -> bool:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    row = con.execute(
        "SELECT remaining FROM api_budget WHERE provider='oddsapi' AND month=?", (month,)
    ).fetchone()
    return row is None or row["remaining"] is None or row["remaining"] > config.ODDS_BUDGET_FLOOR


def _record_budget(con, headers) -> None:
    try:
        used = int(headers.get("x-requests-used", 0))
        remaining = int(headers.get("x-requests-remaining", 0))
    except (TypeError, ValueError):
        return
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    db.upsert(con, "api_budget", [{
        "provider": "oddsapi", "month": month, "used": used,
        "remaining": remaining, "updated_at": db.utcnow(),
    }])


def fetch_game_lines(con, d) -> int:
    """One snapshot of MLB totals + moneylines, matched to game_pks by team + date."""
    if not config.ODDS_API_KEY:
        return 0
    if not budget_ok(con):
        print("[odds] monthly free-tier budget low — skipping snapshot")
        return 0
    try:
        r = requests.get(
            f"{config.ODDS_API_BASE}/sports/baseball_mlb/odds",
            params={
                "apiKey": config.ODDS_API_KEY,
                "regions": "us",
                "markets": "totals,h2h",
                "oddsFormat": "american",
                "dateFormat": "iso",
            },
            timeout=config.REQUEST_TIMEOUT,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"[odds] fetch failed: {e}")
        return 0
    _record_budget(con, r.headers)

    games = {
        (g["date"], g["home_team"]): g["game_pk"]
        for g in con.execute("SELECT game_pk, date, home_team FROM games WHERE date=?", (util.iso(d),))
    }
    fetched_at = db.utcnow()
    rows = []
    for ev in r.json():
        home = FULL_TO_ABBR.get(ev.get("home_team", ""))
        ev_date = (ev.get("commence_time") or "")[:10]
        pk = games.get((ev_date, home)) or games.get((util.iso(d), home))
        if not pk:
            continue
        away = FULL_TO_ABBR.get(ev.get("away_team", ""))
        for bm in ev.get("bookmakers", []):
            rec = {
                "game_pk": pk, "book": bm["key"], "market": "gamelines",
                "line": None, "over_odds": None, "under_odds": None,
                "home_ml": None, "away_ml": None, "fetched_at": fetched_at,
            }
            for mkt in bm.get("markets", []):
                if mkt["key"] == "totals":
                    for o in mkt.get("outcomes", []):
                        if o["name"] == "Over":
                            rec["line"] = o.get("point")
                            rec["over_odds"] = o.get("price")
                        elif o["name"] == "Under":
                            rec["under_odds"] = o.get("price")
                elif mkt["key"] == "h2h":
                    for o in mkt.get("outcomes", []):
                        ab = FULL_TO_ABBR.get(o["name"])
                        if ab == home:
                            rec["home_ml"] = o.get("price")
                        elif ab == away:
                            rec["away_ml"] = o.get("price")
            rows.append(rec)
    db.upsert(con, "game_odds", rows)
    return len(rows)


def gameline_snapshot(con, game_pk: int) -> dict | None:
    """Latest game-line snapshot for site display: median total, O/U odds,
    and moneylines across books, plus book count and snapshot time."""
    rows = con.execute(
        """SELECT line, over_odds, under_odds, home_ml, away_ml, fetched_at FROM game_odds
           WHERE game_pk=? AND fetched_at=(SELECT MAX(fetched_at) FROM game_odds WHERE game_pk=?)""",
        (game_pk, game_pk),
    ).fetchall()
    if not rows:
        return None

    def med(vals):
        # lower-middle median: always a real posted number, never an average
        s = sorted(v for v in vals if v is not None)
        return s[(len(s) - 1) // 2] if s else None

    out = {
        "total": med([r["line"] for r in rows]),
        "over_odds": med([r["over_odds"] for r in rows]),
        "under_odds": med([r["under_odds"] for r in rows]),
        "home_ml": med([r["home_ml"] for r in rows]),
        "away_ml": med([r["away_ml"] for r in rows]),
        "books": len(rows),
        "fetched_at": rows[0]["fetched_at"],
    }
    if out["total"] is None and out["home_ml"] is None:
        return None
    return out


def latest_total_for_game(con, game_pk: int) -> dict | None:
    """Median totals line + favorite info across books, latest snapshot."""
    rows = con.execute(
        """SELECT line, home_ml, away_ml FROM game_odds
           WHERE game_pk=? AND fetched_at=(SELECT MAX(fetched_at) FROM game_odds WHERE game_pk=?)""",
        (game_pk, game_pk),
    ).fetchall()
    lines = sorted(r["line"] for r in rows if r["line"] is not None)
    if not lines:
        return None
    med = lines[len(lines) // 2]
    hmls = [r["home_ml"] for r in rows if r["home_ml"] is not None]
    amls = [r["away_ml"] for r in rows if r["away_ml"] is not None]
    return {
        "total": med,
        "home_ml": sorted(hmls)[len(hmls) // 2] if hmls else None,
        "away_ml": sorted(amls)[len(amls) // 2] if amls else None,
    }
