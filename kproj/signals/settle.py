"""Settle capper picks against official boxscores (MLB Stats API, free).

Runs every cycle; only touches unsettled picks for past dates whose games
are Final. Grades: win/loss (push on integer lines), void when the pitcher
never started. PnL is 1u flat at stated odds, -110 when the tweet had none.
"""
import json

from .. import config, util
from . import store


def _pnl(result: str, odds: int | None) -> float:
    if result == "win":
        o = odds if odds is not None else -110
        return round(o / 100.0, 3) if o > 0 else round(100.0 / -o, 3)
    return -1.0 if result == "loss" else 0.0


def settle(con) -> int:
    today = util.iso(util.today_et())
    rows = con.execute(
        """SELECT p.id, p.date, p.capper, p.pitcher_id, p.side, p.line, p.odds
           FROM capper_picks p LEFT JOIN capper_results r ON r.pick_id = p.id
           WHERE r.pick_id IS NULL AND p.date < ? AND p.pitcher_id IS NOT NULL""",
        (today,)).fetchall()
    if not rows:
        return 0
    # game_pk lookup from stored gameday snapshots
    pk_for = {}
    for g in con.execute("SELECT date, game_pk, status, probables_json FROM gameday WHERE date < ?", (today,)):
        for p in json.loads(g["probables_json"] or "{}").values():
            pk_for[(g["date"], p["id"])] = g["game_pk"]
    box_cache: dict = {}
    n = 0
    for pick in rows:
        pk = pk_for.get((pick["date"], pick["pitcher_id"]))
        if pk is None:
            continue        # no snapshot that day; leave pending
        if pk not in box_cache:
            try:
                box_cache[pk] = util.http_get(f"{config.MLB_API}/game/{pk}/boxscore")
            except RuntimeError:
                box_cache[pk] = None
        box = box_cache[pk]
        if box is None:
            continue
        stats = None
        for side in ("home", "away"):
            pl = ((box.get("teams") or {}).get(side, {}).get("players") or {}).get(f"ID{pick['pitcher_id']}")
            if pl:
                stats = (pl.get("stats") or {}).get("pitching") or {}
                break
        if stats is None or not stats.get("gamesStarted"):
            result, k = "void", None
        else:
            k = stats.get("strikeOuts", 0)
            if k == pick["line"]:
                result = "push"
            elif (k > pick["line"]) == (pick["side"] == "over"):
                result = "win"
            else:
                result = "loss"
        con.execute(
            "INSERT OR REPLACE INTO capper_results (pick_id, date, capper, pitcher_id, actual_k, result, pnl_units, settled_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (pick["id"], pick["date"], pick["capper"], pick["pitcher_id"], k,
             result, _pnl(result, pick["odds"]), store.utcnow()))
        n += 1
    if n:
        print(f"[signals] settled {n} capper picks")
    return n
