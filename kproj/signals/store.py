"""SQLite store for the signals pipeline (separate from kproj.db on purpose)."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .. import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS capper_posts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    capper     TEXT,                 -- x.com handle
    post_id    TEXT,                 -- tweet id (or manual:<hash>)
    posted_at  TEXT,
    fetched_at TEXT,
    text       TEXT,
    url        TEXT,
    UNIQUE (capper, post_id)
);

CREATE TABLE IF NOT EXISTS capper_picks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    capper      TEXT,
    date        TEXT,                -- ET game date the pick applies to
    pitcher_raw TEXT,
    pitcher_id  INTEGER,
    side        TEXT,                -- 'over' | 'under'
    line        REAL,
    odds        INTEGER,             -- american; NULL if not stated
    book        TEXT,
    source      TEXT,                -- 'auto' | 'manual'
    post_id     TEXT,
    entered_at  TEXT,
    UNIQUE (capper, date, pitcher_raw, side, line)
);

CREATE TABLE IF NOT EXISTS capper_results (
    pick_id    INTEGER PRIMARY KEY,
    date       TEXT,
    capper     TEXT,
    pitcher_id INTEGER,
    actual_k   INTEGER,
    result     TEXT,                 -- 'win' | 'loss' | 'push' | 'void'
    pnl_units  REAL,                 -- 1u flat at stated odds (-110 if none)
    settled_at TEXT
);

CREATE TABLE IF NOT EXISTS gameday (
    date            TEXT,
    game_pk         INTEGER,
    status          TEXT,
    first_pitch_utc TEXT,
    home_lineup     INTEGER DEFAULT 0,
    away_lineup     INTEGER DEFAULT 0,
    probables_json  TEXT,            -- {"TEAM": {"id":..,"name":..}, ...}
    as_of           TEXT,
    PRIMARY KEY (date, game_pk)
);

CREATE TABLE IF NOT EXISTS fg_proj (
    date        TEXT,
    pitcher_id  INTEGER,
    name        TEXT,
    team        TEXT,
    so_per_gs   REAL,
    ip_per_gs   REAL,
    tbf_per_gs  REAL,
    fetched_at  TEXT,
    PRIMARY KEY (date, pitcher_id)
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def session():
    config.SIGNALS_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.SIGNALS_DB)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def get_kv(con, key, default=None):
    row = con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_kv(con, key, value) -> None:
    con.execute("INSERT INTO kv (key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def source_status(con, source: str, ok: bool, note: str = "") -> None:
    now = utcnow()
    set_kv(con, f"src:{source}:last_attempt", now)
    set_kv(con, f"src:{source}:note", note)
    if ok:
        set_kv(con, f"src:{source}:last_success", now)


def source_report(con, source: str) -> dict:
    return {
        "last_attempt": get_kv(con, f"src:{source}:last_attempt"),
        "last_success": get_kv(con, f"src:{source}:last_success"),
        "note": get_kv(con, f"src:{source}:note", ""),
    }
