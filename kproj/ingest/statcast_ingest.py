"""Baseball Savant (Statcast) ingestion.

Pulls pitch-level CSVs in small date chunks, aggregates to game level
(CSW%, whiffs, fastball velo; batter PA/K vs pitcher hand), then discards
the raw pitches. Keeps the SQLite DB small. Free; be polite between chunks.
"""
import io
import time
from datetime import timedelta

import pandas as pd

from .. import config, db, util

USECOLS = [
    "game_pk", "game_date", "pitcher", "batter", "p_throws",
    "events", "description", "release_speed", "pitch_type",
    "home_team", "away_team", "inning_topbot",
]
K_EVENTS = {"strikeout", "strikeout_double_play"}
WHIFF_DESC = {"swinging_strike", "swinging_strike_blocked"}
FB_TYPES = ["FF", "SI", "FC"]
CHUNK_SLEEP = 3


def _savant_params(start: str, end: str) -> dict:
    return {
        "all": "true", "type": "details", "player_type": "pitcher",
        "hfGT": "R|",  # regular season
        "game_date_gt": start, "game_date_lt": end,
        "min_pitches": 0, "min_results": 0, "min_pas": 0,
        "group_by": "name", "sort_col": "pitches", "sort_order": "desc",
    }


def fetch_chunk(start: str, end: str) -> pd.DataFrame:
    r = util.http_get(config.SAVANT_CSV, params=_savant_params(start, end),
                      as_json=False, timeout=300, retries=4)
    df = pd.read_csv(io.StringIO(r.text), usecols=lambda c: c in USECOLS, low_memory=False)
    return df


def aggregate_and_store(con, df: pd.DataFrame) -> tuple[int, int]:
    """Aggregate one chunk of raw pitches into pitcher enrichment + batter splits."""
    if df.empty:
        return 0, 0
    df = df.dropna(subset=["game_pk", "pitcher", "batter"])
    df["events"] = df["events"].fillna("")
    df["description"] = df["description"].fillna("")

    # --- pitcher game enrichment (csw / whiffs / fb velo / hand)
    grp = df.groupby(["game_pk", "pitcher"], sort=False)
    enrich = grp.agg(
        pitches_sc=("description", "size"),
        called=("description", lambda s: (s == "called_strike").sum()),
        whiffs=("description", lambda s: s.isin(WHIFF_DESC).sum()),
        p_throws=("p_throws", "first"),
    ).reset_index()
    velo = (
        df[df["pitch_type"].isin(FB_TYPES)]
        .groupby(["game_pk", "pitcher"])["release_speed"]
        .mean()
        .reset_index()
        .rename(columns={"release_speed": "fb_velo"})
    )
    enrich = enrich.merge(velo, on=["game_pk", "pitcher"], how="left")
    n_p = 0
    for r in enrich.itertuples(index=False):
        cur = con.execute(
            """UPDATE pitcher_game_logs
               SET called_strikes=?, whiffs=?, fb_velo=?, p_throws=?
               WHERE game_pk=? AND pitcher_id=?""",
            (int(r.called), int(r.whiffs),
             None if pd.isna(r.fb_velo) else round(float(r.fb_velo), 1),
             r.p_throws, int(r.game_pk), int(r.pitcher)),
        )
        n_p += cur.rowcount
        con.execute(
            "UPDATE players SET throws=COALESCE(throws, ?) WHERE mlb_id=?",
            (r.p_throws, int(r.pitcher)),
        )

    # --- batter PA / K vs pitcher hand
    pa_df = df[df["events"] != ""]
    bat = (
        pa_df.groupby(["batter", "game_pk", "p_throws"], sort=False)
        .agg(
            pa=("events", "size"),
            k=("events", lambda s: s.isin(K_EVENTS).sum()),
            game_date=("game_date", "first"),
            home_team=("home_team", "first"),
            away_team=("away_team", "first"),
            half=("inning_topbot", "first"),
        )
        .reset_index()
    )
    # Batter's team: batting in Top = away team, Bot = home team
    bat["team"] = bat.apply(
        lambda r: r["away_team"] if r["half"] == "Top" else r["home_team"], axis=1
    )
    from .mlb_api import canon
    rows = [
        {
            "batter_id": int(r.batter),
            "game_pk": int(r.game_pk),
            "date": str(r.game_date)[:10],
            "team": canon(str(r.team)),
            "vs_hand": r.p_throws,
            "pa": int(r.pa),
            "k": int(r.k),
        }
        for r in bat.itertuples(index=False)
    ]
    n_b = db.upsert(con, "batter_game_vs_hand", rows)
    return n_p, n_b


def backfill(con, start, end, fetch_boxscores=True, progress=print) -> None:
    """Resumable backfill of [start, end]: schedule → boxscores → statcast aggregates."""
    from . import mlb_api

    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=config.SAVANT_CHUNK_DAYS - 1), end)
        key = f"{util.iso(cur)}_{util.iso(chunk_end)}"
        if db.is_done(con, "statcast", key):
            cur = chunk_end + timedelta(days=1)
            continue
        if not (util.in_season_window(cur) or util.in_season_window(chunk_end)):
            db.mark_done(con, "statcast", key, detail="offseason skip")
            cur = chunk_end + timedelta(days=1)
            continue
        progress(f"[backfill] {key}")
        mlb_api.fetch_schedule(con, cur, chunk_end)
        if fetch_boxscores:
            for d in util.daterange(cur, chunk_end):
                for pk in mlb_api.final_game_pks(con, d):
                    if not db.is_done(con, "boxscore", str(pk)):
                        mlb_api.fetch_boxscore(con, pk)
        df = fetch_chunk(util.iso(cur), util.iso(chunk_end))
        n_p, n_b = aggregate_and_store(con, df)
        db.mark_done(con, "statcast", key, detail=f"pitcher_rows={n_p} batter_rows={n_b}")
        con.commit()
        progress(f"[backfill] {key} done: {n_p} pitcher updates, {n_b} batter rows")
        time.sleep(CHUNK_SLEEP)
        cur = chunk_end + timedelta(days=1)
    refresh_league_averages(con)


def ingest_finals_for_date(con, d) -> int:
    """Daily incremental: pull yesterday's finals (boxscores + statcast for that date)."""
    from . import mlb_api

    mlb_api.fetch_schedule(con, d, d)
    pks = mlb_api.final_game_pks(con, d)
    for pk in pks:
        mlb_api.fetch_boxscore(con, pk)
    if pks:
        df = fetch_chunk(util.iso(d), util.iso(d))
        aggregate_and_store(con, df)
    refresh_league_averages(con)
    return len(pks)


def refresh_league_averages(con) -> None:
    """Cache league K%/BF-per-start from our own data (fallbacks in config)."""
    row = con.execute(
        """SELECT SUM(k)*1.0/NULLIF(SUM(bf),0) AS k_pct, AVG(bf) AS bf_avg
           FROM pitcher_game_logs WHERE started=1 AND date >= date('now', '-365 days')"""
    ).fetchone()
    if row and row["k_pct"]:
        db.set_kv(con, "league_k_pct", round(row["k_pct"], 4))
        db.set_kv(con, "league_bf_per_start", round(row["bf_avg"], 2))
