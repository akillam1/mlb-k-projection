"""Live slate state from the official MLB Stats API (free, no key).

Feeds the 'when is it safe to pick' logic: probable confirmed, lineups
posted, game status, plus scratch detection against the model's board.
"""
import json
from datetime import timedelta

from .. import config, util
from . import store


def refresh(con) -> list:
    """Fetch today+tomorrow schedule. Stores per-game state; returns probables
    [(pitcher_id, name, date, game_pk)] for the pick parser."""
    # Span the wall-clock day AND the board day (+1): between the 8 PM rollover
    # and local midnight those differ, and tonight's games still need snapshots
    # for settlement.
    today = min(util.today_et(), util.board_date())
    end = max(util.today_et(), util.board_date()) + timedelta(days=1)
    try:
        data = util.http_get(f"{config.MLB_API}/schedule", params={
            "sportId": 1,
            "startDate": util.iso(today),
            "endDate": util.iso(end),
            "hydrate": "probablePitcher,lineups",
        })
    except RuntimeError as e:
        store.source_status(con, "statsapi", ok=False, note=str(e))
        print(f"[signals] statsapi failed: {e}")
        return _probables_from_store(con)
    now = store.utcnow()
    probables = []
    for day in data.get("dates", []):
        date = day.get("date")
        for g in day.get("games", []):
            pk = g.get("gamePk")
            status = (g.get("status") or {}).get("detailedState", "")
            lineups = g.get("lineups") or {}
            probs = {}
            for side in ("home", "away"):
                team = (((g.get("teams") or {}).get(side) or {}).get("team") or {})
                pp = (((g.get("teams") or {}).get(side) or {}).get("probablePitcher") or {})
                if pp.get("id"):
                    probs[team.get("abbreviation") or team.get("name", side)] = {
                        "id": pp["id"], "name": pp.get("fullName", "")}
                    probables.append((pp["id"], pp.get("fullName", ""), date, pk))
            con.execute(
                """INSERT INTO gameday (date, game_pk, status, first_pitch_utc,
                                        home_lineup, away_lineup, probables_json, as_of)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(date, game_pk) DO UPDATE SET
                     status=excluded.status, first_pitch_utc=excluded.first_pitch_utc,
                     home_lineup=excluded.home_lineup, away_lineup=excluded.away_lineup,
                     probables_json=excluded.probables_json, as_of=excluded.as_of""",
                (date, pk, status, g.get("gameDate"),
                 1 if lineups.get("homePlayers") else 0,
                 1 if lineups.get("awayPlayers") else 0,
                 json.dumps(probs), now))
    store.source_status(con, "statsapi", ok=True, note=f"{len(probables)} probables")
    print(f"[signals] statsapi: {len(probables)} probables across today+tomorrow")
    return probables


def _probables_from_store(con) -> list:
    """Fallback to the last stored snapshot so parsing still works offline."""
    out = []
    for r in con.execute("SELECT date, game_pk, probables_json FROM gameday WHERE date>=?",
                         (util.iso(util.today_et()),)):
        for p in json.loads(r["probables_json"] or "{}").values():
            out.append((p["id"], p["name"], r["date"], r["game_pk"]))
    return out


def game_state(con, date_s: str) -> dict:
    """{game_pk: row} for a date."""
    return {r["game_pk"]: dict(r) for r in
            con.execute("SELECT * FROM gameday WHERE date=?", (date_s,))}
