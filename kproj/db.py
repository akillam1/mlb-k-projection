"""SQLite storage layer. Schema adapted from roadmap §4 (Postgres → SQLite, zero-cost)."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    mlb_id     INTEGER PRIMARY KEY,
    name       TEXT,
    throws     TEXT,
    bats       TEXT,
    position   TEXT
);

CREATE TABLE IF NOT EXISTS games (
    game_pk          INTEGER PRIMARY KEY,
    date             TEXT NOT NULL,          -- ET game date YYYY-MM-DD
    season           INTEGER,
    home_team        TEXT,
    away_team        TEXT,
    venue_id         INTEGER,
    venue_name       TEXT,
    first_pitch_utc  TEXT,
    status           TEXT,
    home_score       INTEGER,
    away_score       INTEGER,
    ump_name         TEXT,
    temp_f           REAL,
    wind_mph         REAL,
    humidity         REAL,
    precip_prob      REAL
);
CREATE INDEX IF NOT EXISTS idx_games_date ON games(date);

CREATE TABLE IF NOT EXISTS probable_starters (
    game_pk      INTEGER,
    team         TEXT,
    pitcher_id   INTEGER,
    pitcher_name TEXT,
    as_of        TEXT,
    PRIMARY KEY (game_pk, team)
);

CREATE TABLE IF NOT EXISTS lineups (
    game_pk       INTEGER,
    team          TEXT,
    batting_order INTEGER,
    batter_id     INTEGER,
    source        TEXT,                       -- 'confirmed' | 'projected'
    as_of         TEXT,
    PRIMARY KEY (game_pk, team, batting_order, source)
);

CREATE TABLE IF NOT EXISTS pitcher_game_logs (
    pitcher_id     INTEGER,
    game_pk        INTEGER,
    date           TEXT,
    team           TEXT,
    opp            TEXT,
    is_home        INTEGER,
    started        INTEGER,
    ip_outs        INTEGER,
    bf             INTEGER,
    k              INTEGER,
    bb             INTEGER,
    h              INTEGER,
    hr             INTEGER,
    pitches        INTEGER,
    called_strikes INTEGER,
    whiffs         INTEGER,
    fb_velo        REAL,
    p_throws       TEXT,
    PRIMARY KEY (pitcher_id, game_pk)
);
CREATE INDEX IF NOT EXISTS idx_pgl_pitcher_date ON pitcher_game_logs(pitcher_id, date);
CREATE INDEX IF NOT EXISTS idx_pgl_date ON pitcher_game_logs(date);

CREATE TABLE IF NOT EXISTS batter_game_vs_hand (
    batter_id INTEGER,
    game_pk   INTEGER,
    date      TEXT,
    team      TEXT,
    vs_hand   TEXT,                            -- 'L' | 'R' (pitcher hand)
    pa        INTEGER,
    k         INTEGER,
    PRIMARY KEY (batter_id, game_pk, vs_hand)
);
CREATE INDEX IF NOT EXISTS idx_bgvh_batter ON batter_game_vs_hand(batter_id, vs_hand, date);
CREATE INDEX IF NOT EXISTS idx_bgvh_team ON batter_game_vs_hand(team, date);

CREATE TABLE IF NOT EXISTS game_odds (
    game_pk    INTEGER,
    book       TEXT,
    market     TEXT,                           -- 'totals' | 'h2h'
    line       REAL,
    over_odds  INTEGER,
    under_odds INTEGER,
    home_ml    INTEGER,
    away_ml    INTEGER,
    fetched_at TEXT,
    PRIMARY KEY (game_pk, book, market, fetched_at)
);
CREATE INDEX IF NOT EXISTS idx_odds_game ON game_odds(game_pk, fetched_at DESC);

CREATE TABLE IF NOT EXISTS manual_k_lines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT,
    pitcher_raw TEXT,
    pitcher_id  INTEGER,
    book        TEXT,
    line        REAL,
    over_odds   INTEGER,
    under_odds  INTEGER,
    is_closing  INTEGER DEFAULT 0,
    entered_at  TEXT,
    UNIQUE (date, pitcher_raw, book, line, is_closing)
);

CREATE TABLE IF NOT EXISTS projections (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    game_pk            INTEGER,
    pitcher_id         INTEGER,
    model_version      TEXT,
    generated_at       TEXT,
    point_est          REAL,
    p10 REAL, p25 REAL, p50 REAL, p75 REAL, p90 REAL,
    lineup_confidence  REAL,
    features_json      TEXT,
    is_latest          INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_proj_gp ON projections(game_pk, pitcher_id, is_latest);

CREATE TABLE IF NOT EXISTS opportunities (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    projection_id INTEGER,
    game_pk       INTEGER,
    pitcher_id    INTEGER,
    source        TEXT,                        -- 'manual'
    book          TEXT,
    line          REAL,
    side          TEXT,                        -- 'over' | 'under'
    odds          INTEGER,
    model_prob    REAL,
    vigfree_prob  REAL,
    ev_per_unit   REAL,
    kelly_quarter REAL,
    score         REAL,
    created_at    TEXT,
    is_latest     INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_opp_gp ON opportunities(game_pk, pitcher_id, is_latest);

CREATE TABLE IF NOT EXISTS actuals (
    game_pk     INTEGER,
    pitcher_id  INTEGER,
    actual_k    INTEGER,
    ip_outs     INTEGER,
    recorded_at TEXT,
    PRIMARY KEY (game_pk, pitcher_id)
);

CREATE TABLE IF NOT EXISTS projection_results (
    projection_id  INTEGER PRIMARY KEY,
    game_pk        INTEGER,
    pitcher_id     INTEGER,
    model_version  TEXT,
    date           TEXT,
    point_est      REAL,
    actual_k       INTEGER,
    abs_error      REAL,
    signed_error   REAL,
    in_band_10_90  INTEGER
);

CREATE TABLE IF NOT EXISTS bet_results (
    opportunity_id INTEGER PRIMARY KEY,
    date           TEXT,
    model_version  TEXT,
    side           TEXT,
    line           REAL,
    odds           INTEGER,
    model_prob     REAL,
    actual_k       INTEGER,
    result         TEXT,                       -- 'win' | 'loss' | 'push' | 'void'
    pnl_units      REAL,
    clv_pct        REAL,
    settled_at     TEXT
);

CREATE TABLE IF NOT EXISTS model_registry (
    version       TEXT PRIMARY KEY,
    trained_at    TEXT,
    train_rows    INTEGER,
    valid_rows    INTEGER,
    valid_mae     REAL,
    valid_poisson_dev REAL,
    params_json   TEXT,
    active        INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ingest_log (
    job    TEXT,
    key    TEXT,
    status TEXT,
    detail TEXT,
    run_at TEXT,
    PRIMARY KEY (job, key)
);

CREATE TABLE IF NOT EXISTS api_budget (
    provider   TEXT,
    month      TEXT,
    used       INTEGER,
    remaining  INTEGER,
    updated_at TEXT,
    PRIMARY KEY (provider, month)
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(db_path=None) -> sqlite3.Connection:
    path = db_path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    con.commit()


@contextmanager
def session(db_path=None):
    con = connect(db_path)
    try:
        init_db(con)
        yield con
        con.commit()
    finally:
        con.close()


def upsert(con, table: str, rows: list[dict]) -> int:
    """INSERT OR REPLACE a list of dicts."""
    if not rows:
        return 0
    cols = list(rows[0].keys())
    sql = f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
    con.executemany(sql, [[r.get(c) for c in cols] for r in rows])
    return len(rows)


def mark_done(con, job: str, key: str, status="done", detail="") -> None:
    con.execute(
        "INSERT OR REPLACE INTO ingest_log (job, key, status, detail, run_at) VALUES (?,?,?,?,?)",
        (job, key, status, detail, utcnow()),
    )


def is_done(con, job: str, key: str) -> bool:
    row = con.execute(
        "SELECT status FROM ingest_log WHERE job=? AND key=?", (job, key)
    ).fetchone()
    return bool(row and row["status"] == "done")


def set_kv(con, key: str, value) -> None:
    con.execute(
        "INSERT OR REPLACE INTO kv (key, value) VALUES (?,?)", (key, json.dumps(value))
    )


def get_kv(con, key: str, default=None):
    row = con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default
