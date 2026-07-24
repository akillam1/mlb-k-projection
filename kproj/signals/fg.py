"""FanGraphs Depth Charts rest-of-season pitching projections (free JSON).

Per-start expected strikeouts (SO/GS) for today's probables — an independent
public model to sanity-check ours. Pulled once per ET day.
"""
from .. import config, util
from . import store


def refresh(con) -> int:
    date_s = util.iso(util.today_et())
    if store.get_kv(con, f"fg_fetched:{date_s}"):
        return 0
    try:
        data = util.http_get(config.FG_PROJ_URL, params={
            "type": config.FG_PROJ_TYPE, "stats": "pit", "pos": "all", "team": "0",
            "players": "0", "lg": "all",
        })
    except RuntimeError as e:
        store.source_status(con, "fangraphs", ok=False, note=str(e))
        print(f"[signals] fangraphs failed: {e}")
        return 0
    if not isinstance(data, list):
        store.source_status(con, "fangraphs", ok=False, note="unexpected payload")
        return 0
    now, n = store.utcnow(), 0
    for p in data:
        gs, mlbam = p.get("GS") or 0, p.get("xMLBAMID")
        if not mlbam or gs < 1:
            continue
        con.execute(
            """INSERT INTO fg_proj (date, pitcher_id, name, team, so_per_gs, ip_per_gs, tbf_per_gs, fetched_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(date, pitcher_id) DO UPDATE SET
                 so_per_gs=excluded.so_per_gs, ip_per_gs=excluded.ip_per_gs,
                 tbf_per_gs=excluded.tbf_per_gs, fetched_at=excluded.fetched_at""",
            (date_s, mlbam, p.get("PlayerName", ""), p.get("Team", ""),
             (p.get("SO") or 0) / gs, (p.get("IP") or 0) / gs, (p.get("TBF") or 0) / gs, now))
        n += 1
    store.set_kv(con, f"fg_fetched:{date_s}", now)
    store.source_status(con, "fangraphs", ok=True, note=f"{n} starters")
    print(f"[signals] fangraphs: {n} starter projections")
    return n


def for_pitcher(con, date_s: str, pitcher_id: int) -> dict | None:
    r = con.execute("SELECT so_per_gs, ip_per_gs FROM fg_proj WHERE date=? AND pitcher_id=?",
                    (date_s, pitcher_id)).fetchone()
    if r is None:  # fall back to the most recent day we have
        r = con.execute(
            "SELECT so_per_gs, ip_per_gs FROM fg_proj WHERE pitcher_id=? ORDER BY date DESC LIMIT 1",
            (pitcher_id,)).fetchone()
    return {"k_per_start": round(r["so_per_gs"], 2), "ip_per_start": round(r["ip_per_gs"], 1)} if r else None
