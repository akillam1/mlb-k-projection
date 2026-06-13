"""MLB Stats API ingestion: schedule, probables, lineups, boxscores, umpires, players.

All free. Undocumented rate limits — be polite (sleep between calls).
"""
import time

from .. import config, db, util

SLEEP = 0.15

ABBR_CANON = {"AZ": "ARI", "OAK": "ATH"}  # statsapi drift → our canonical abbrs


def canon(abbr: str) -> str:
    return ABBR_CANON.get(abbr or "", abbr or "")


def fetch_schedule(con, start, end) -> list[dict]:
    """Upsert games + probable starters + confirmed lineups for [start, end]. Returns game dicts."""
    data = util.http_get(
        f"{config.MLB_API}/schedule",
        params={
            "sportId": 1,
            "startDate": util.iso(start),
            "endDate": util.iso(end),
            "hydrate": "probablePitcher,lineups,team",
        },
    )
    games, probables, lineups = [], [], []
    now = db.utcnow()
    for day in data.get("dates", []):
        for g in day.get("games", []):
            if g.get("gameType") != "R":  # regular season only
                continue
            pk = g["gamePk"]
            home = g["teams"]["home"]
            away = g["teams"]["away"]
            home_abbr = canon(home["team"].get("abbreviation", ""))
            away_abbr = canon(away["team"].get("abbreviation", ""))
            games.append(
                {
                    "game_pk": pk,
                    "date": g.get("officialDate") or day["date"],
                    "season": int(g.get("season", day["date"][:4])),
                    "home_team": home_abbr,
                    "away_team": away_abbr,
                    "venue_id": (g.get("venue") or {}).get("id"),
                    "venue_name": (g.get("venue") or {}).get("name"),
                    "first_pitch_utc": g.get("gameDate"),
                    "status": (g.get("status") or {}).get("detailedState", ""),
                }
            )
            for side, abbr in (("home", home_abbr), ("away", away_abbr)):
                pp = g["teams"][side].get("probablePitcher")
                if pp:
                    probables.append(
                        {
                            "game_pk": pk,
                            "team": abbr,
                            "pitcher_id": pp["id"],
                            "pitcher_name": pp.get("fullName", ""),
                            "as_of": now,
                        }
                    )
            lu = g.get("lineups") or {}
            for side, abbr in (("homePlayers", home_abbr), ("awayPlayers", away_abbr)):
                for order, player in enumerate(lu.get(side) or [], start=1):
                    if order > 9:
                        break
                    lineups.append(
                        {
                            "game_pk": pk,
                            "team": abbr,
                            "batting_order": order,
                            "batter_id": player["id"],
                            "source": "confirmed",
                            "as_of": now,
                        }
                    )
    # Preserve weather/ump/score columns on re-upsert of games
    for row in games:
        existing = con.execute(
            "SELECT ump_name, temp_f, wind_mph, humidity, precip_prob, home_score, away_score "
            "FROM games WHERE game_pk=?",
            (row["game_pk"],),
        ).fetchone()
        if existing:
            row.update({k: existing[k] for k in existing.keys()})
    db.upsert(con, "games", games)
    db.upsert(con, "probable_starters", probables)
    db.upsert(con, "lineups", lineups)
    return games


def fetch_boxscore(con, game_pk: int) -> bool:
    """Pitcher core lines + home-plate ump for one final game."""
    try:
        box = util.http_get(f"{config.MLB_API}/game/{game_pk}/boxscore")
    except RuntimeError:
        db.mark_done(con, "boxscore", str(game_pk), status="error", detail="fetch failed")
        return False
    game = con.execute("SELECT * FROM games WHERE game_pk=?", (game_pk,)).fetchone()
    if not game:
        return False
    ump = ""
    for off in box.get("officials") or []:
        if off.get("officialType") == "Home Plate":
            ump = (off.get("official") or {}).get("fullName", "")
    hs = (((box["teams"]["home"].get("teamStats") or {}).get("batting") or {}).get("runs"))
    as_ = (((box["teams"]["away"].get("teamStats") or {}).get("batting") or {}).get("runs"))
    con.execute(
        "UPDATE games SET ump_name=?, home_score=?, away_score=? WHERE game_pk=?",
        (ump, hs, as_, game_pk),
    )

    rows, player_meta = [], []
    for side, opp_side in (("home", "away"), ("away", "home")):
        team_abbr = canon((box["teams"][side].get("team") or {}).get("abbreviation", ""))
        opp_abbr = canon((box["teams"][opp_side].get("team") or {}).get("abbreviation", ""))
        for key, p in (box["teams"][side].get("players") or {}).items():
            stats = (p.get("stats") or {}).get("pitching") or {}
            if not stats or stats.get("battersFaced") in (None, 0):
                continue
            person = p.get("person") or {}
            rows.append(
                {
                    "pitcher_id": person.get("id"),
                    "game_pk": game_pk,
                    "date": game["date"],
                    "team": team_abbr,
                    "opp": opp_abbr,
                    "is_home": 1 if side == "home" else 0,
                    "started": int(stats.get("gamesStarted", 0) or 0),
                    "ip_outs": util.ip_to_outs(stats.get("inningsPitched")),
                    "bf": int(stats.get("battersFaced", 0) or 0),
                    "k": int(stats.get("strikeOuts", 0) or 0),
                    "bb": int(stats.get("baseOnBalls", 0) or 0),
                    "h": int(stats.get("hits", 0) or 0),
                    "hr": int(stats.get("homeRuns", 0) or 0),
                    "pitches": int(stats.get("pitchesThrown", stats.get("numberOfPitches", 0)) or 0),
                }
            )
            player_meta.append({"mlb_id": person.get("id"), "name": person.get("fullName", "")})
    # Merge core stats without clobbering statcast enrichment columns
    for r in rows:
        con.execute(
            """INSERT INTO pitcher_game_logs
               (pitcher_id, game_pk, date, team, opp, is_home, started, ip_outs, bf, k, bb, h, hr, pitches)
               VALUES (:pitcher_id,:game_pk,:date,:team,:opp,:is_home,:started,:ip_outs,:bf,:k,:bb,:h,:hr,:pitches)
               ON CONFLICT(pitcher_id, game_pk) DO UPDATE SET
                 date=:date, team=:team, opp=:opp, is_home=:is_home, started=:started,
                 ip_outs=:ip_outs, bf=:bf, k=:k, bb=:bb, h=:h, hr=:hr, pitches=:pitches""",
            r,
        )
    for m in player_meta:
        con.execute(
            "INSERT INTO players (mlb_id, name) VALUES (?,?) "
            "ON CONFLICT(mlb_id) DO UPDATE SET name=excluded.name",
            (m["mlb_id"], m["name"]),
        )
    db.mark_done(con, "boxscore", str(game_pk))
    time.sleep(SLEEP)
    return True


def ensure_players(con, ids: list[int]) -> None:
    """Fetch name + handedness for ids missing them."""
    ids = [int(i) for i in ids if i]
    if not ids:
        return
    missing = [
        i
        for i in set(ids)
        if not con.execute(
            "SELECT 1 FROM players WHERE mlb_id=? AND throws IS NOT NULL", (i,)
        ).fetchone()
    ]
    for batch_start in range(0, len(missing), 100):
        batch = missing[batch_start : batch_start + 100]
        try:
            data = util.http_get(
                f"{config.MLB_API}/people",
                params={"personIds": ",".join(map(str, batch))},
            )
        except RuntimeError:
            continue
        rows = [
            {
                "mlb_id": p["id"],
                "name": p.get("fullName", ""),
                "throws": ((p.get("pitchHand") or {}).get("code") or None),
                "bats": ((p.get("batSide") or {}).get("code") or None),
                "position": ((p.get("primaryPosition") or {}).get("abbreviation") or None),
            }
            for p in data.get("people", [])
        ]
        db.upsert(con, "players", rows)
        time.sleep(SLEEP)


def final_game_pks(con, d) -> list[int]:
    return [
        r["game_pk"]
        for r in con.execute(
            "SELECT game_pk FROM games WHERE date=? AND status LIKE 'Final%'", (util.iso(d),)
        ).fetchall()
    ]
