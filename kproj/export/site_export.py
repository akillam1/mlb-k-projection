"""Export compact JSON for the static GitHub Pages dashboard (roadmap §7)."""
import json
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import config, db, util
from ..ingest.odds import gameline_snapshot

ET = ZoneInfo(config.ET_ZONE)


def _book_rank(col: str = "o.book") -> str:
    """SQL CASE ranking books by config.PREFERRED_BOOKS — used to pick ONE
    canonical book per pick for performance/validation (DraftKings first,
    then down the chain). Same bet at six books is one pick, not six."""
    whens = " ".join(
        f"WHEN {col}='{b}' THEN {i}" for i, b in enumerate(config.PREFERRED_BOOKS))
    return f"CASE {whens} ELSE {len(config.PREFERRED_BOOKS)} END"


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


def _build_slate(con, d) -> dict:
    """Everything the Today board needs for one calendar date: shared by
    export_today (the live board date) and export_tomorrow (a look-ahead at
    the next day, for the postseason's sparser schedule — see export_tomorrow's
    docstring)."""
    date_s = util.iso(d) if not isinstance(d, str) else d
    rows = con.execute(
        """SELECT g.game_pk, g.date, g.home_team, g.away_team, g.first_pitch_utc, g.status,
                  g.temp_f, g.wind_mph, g.venue_name, g.game_type,
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
    odds_cache: dict = {}
    signals_con = _open_signals_readonly()
    try:
        for r in rows:
            opp_team = r["away_team"] if r["team"] == r["home_team"] else r["home_team"]
            pk = r["game_pk"]
            if pk not in odds_cache:
                odds_cache[pk] = gameline_snapshot(con, pk)
            k_line = _k_line_summary(con, date_s, r["pitcher_id"])
            entry = {
                "game_pk": r["game_pk"],
                "pitcher": r["pitcher_name"],
                "pitcher_id": r["pitcher_id"],
                "team": r["team"],
                "opp": opp_team,
                "home": r["team"] == r["home_team"],
                "time_et": _et_time(r["first_pitch_utc"]),
                "status": r["status"],
                "game_type": r["game_type"],
                "venue": r["venue_name"],
                "temp_f": r["temp_f"],
                "wind_mph": r["wind_mph"],
                "odds": odds_cache[pk],
                "k_line": k_line,
                "last5_k": _last5_k(con, r["pitcher_id"], date_s),
                "workload_flag": _workload_flag(signals_con, date_s, r["pitcher_id"], k_line),
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
    finally:
        if signals_con is not None:
            signals_con.close()
    return {"date": date_s, "generated_at": db.utcnow(), "starters": starters}


def export_today(con, d) -> None:
    _write("today.json", _build_slate(con, d))


def export_tomorrow(con, d) -> None:
    """A look-ahead at the day after the live board date, in the same shape
    as today.json. Built for the postseason: with 1-2 games a day and long
    gaps between them, Robin wants to see the next start as soon as its
    probable is posted, not wait for the 7 PM AZ rollover. Regular-season
    schedules already ingest tomorrow's probables (kproj/cli.py's cmd_daily
    fetches a 2-day schedule window), so this works the same way year-round —
    it degrades to an empty starters list, not an error, when tomorrow's
    slate isn't known yet (no game, or probables not posted)."""
    date_s = util.iso(d) if not isinstance(d, str) else d
    nd = util.iso(date.fromisoformat(date_s) + timedelta(days=1))
    _write("tomorrow.json", _build_slate(con, nd))


def _open_signals_readonly():
    """Best-effort, READ-ONLY peek into signals.db for the workload-news flag
    below. This is the one direction of the boundary in kproj/cli.py's
    cmd_signals docstring ('never touches kproj.db') that's actually fine —
    signals must never touch kproj.db, but kproj proper reading its small,
    best-effort sibling DB for display purposes doesn't create the failure
    mode that boundary exists to prevent. Every failure mode here (file
    doesn't exist yet, mid-write, wrong schema) must still never break
    today.json, so this returns None rather than raising."""
    import sqlite3
    if not config.SIGNALS_DB.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{config.SIGNALS_DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.Error:
        return None


def _workload_flag(signals_con, date_s: str, pitcher_id: int, k_line: dict | None) -> dict | None:
    """Short-leash/pitch-limit flag for the board (see kproj/signals/workload.py),
    'trust but verify'-corroborated against the K line movement already
    computed for this starter: a flag backed by a line that's moved down is
    CORROBORATED; a flag with no matching line move is UNCONFIRMED, not
    discarded — still worth a glance, just not fully trusted on its own."""
    if signals_con is None:
        return None
    try:
        from ..signals import workload
        flag = workload.flags_for(signals_con, date_s, pitcher_id)
    except Exception:  # noqa: BLE001 — a signals-db hiccup must never break today.json
        return None
    if not flag:
        return None
    move = (k_line or {}).get("move")
    corroborated = move is not None and move <= -config.WORKLOAD_LINE_MOVE_CONFIRM_K
    return {**flag, "status": "corroborated" if corroborated else "unconfirmed"}


def _last5_k(con, pitcher_id: int, before_date: str) -> list:
    """Strikeout totals from the pitcher's 5 most recent actual starts before
    today's slate, oldest to newest (left-to-right, matching how a form
    guide reads) — the quick "is he trending up or down" glance Robin wants
    under the pitcher's name instead of the lineup-confirmed note."""
    rows = con.execute(
        """SELECT k FROM pitcher_game_logs
           WHERE pitcher_id=? AND started=1 AND date<? AND k IS NOT NULL
           ORDER BY date DESC LIMIT 5""",
        (pitcher_id, before_date),
    ).fetchall()
    return [r["k"] for r in reversed(rows)]


def _k_line_summary(con, date_s: str, pitcher_id: int) -> dict | None:
    """K line from the canonical book among config.SHOWN_BOOKS (FanDuel and
    DraftKings — Robin's actual books) plus its open->latest movement. Every
    book is still ingested and stored for the Performance page's full
    canonical-chain history; the Today board just never surfaces a line from
    a book Robin doesn't use."""
    if not config.SHOWN_BOOKS:
        return None
    placeholders = ",".join("?" for _ in config.SHOWN_BOOKS)
    rows = con.execute(
        f"""SELECT book,
                  FIRST_VALUE(line) OVER (PARTITION BY book ORDER BY entered_at)      first_line,
                  FIRST_VALUE(line) OVER (PARTITION BY book ORDER BY entered_at DESC) last_line,
                  MAX(entered_at)   OVER (PARTITION BY book) latest_at
           FROM manual_k_lines
           WHERE date=? AND pitcher_id=? AND is_closing=0 AND book IN ({placeholders})""",
        (date_s, pitcher_id, *config.SHOWN_BOOKS),
    ).fetchall()
    per_book = {r["book"]: r for r in rows}          # one row per shown book
    if not per_book:
        return None
    rank = {b: i for i, b in enumerate(config.SHOWN_BOOKS)}
    book = min(per_book, key=lambda b: (rank.get(b, len(rank)), b))
    r = per_book[book]
    if r["last_line"] is None:
        return None
    out = {"line": r["last_line"], "book": book, "latest_at": r["latest_at"]}
    if r["first_line"] is not None and r["first_line"] != r["last_line"]:
        out["open"] = r["first_line"]
        out["move"] = round(r["last_line"] - r["first_line"], 1)
    return out


def _edges_for(con, game_pk: int, pitcher_id: int) -> list:
    """Ranked by probability edge: model win % minus the vig-free market win %.
    That is the cleanest 'how much more often does the model think this wins
    than the market does' number; EV and quarter-Kelly ride along for sizing.
    Restricted to config.SHOWN_BOOKS (FanDuel/DraftKings) — the Today board
    only ever surfaces a pick at a book Robin actually bets."""
    if not config.SHOWN_BOOKS:
        return []
    placeholders = ",".join("?" for _ in config.SHOWN_BOOKS)
    rows = con.execute(
        f"""SELECT book, line, side, odds, model_prob, vigfree_prob, ev_per_unit,
                  kelly_quarter, score,
                  ROUND(model_prob - vigfree_prob, 4) AS prob_edge
           FROM opportunities WHERE game_pk=? AND pitcher_id=? AND is_latest=1
                 AND book IN ({placeholders})
           ORDER BY prob_edge DESC, ev_per_unit DESC""",
        (game_pk, pitcher_id, *config.SHOWN_BOOKS),
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
    """Betting record over POSITIVE-EV picks only (what the site surfaces).
    ONE canonical book per pick (PREFERRED_BOOKS chain) — the same call
    settled at six books is one pick, not six."""
    where = "AND b.date >= ?" if since else ""
    args = (since,) if since else ()
    r = con.execute(
        f"""SELECT COUNT(*) n,
                   SUM(pnl_units) units,
                   SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) wins,
                   SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END) losses,
                   AVG(CASE WHEN result='win' THEN 1.0 WHEN result='loss' THEN 0.0 END)*100 hit,
                   AVG(clv_pct) clv
            FROM (
              SELECT b.pnl_units, b.result, b.clv_pct,
                     ROW_NUMBER() OVER (
                       PARTITION BY b.date, o.game_pk, o.pitcher_id
                       ORDER BY {_book_rank()}, o.ev_per_unit DESC) rn
              FROM bet_results b
              JOIN opportunities o ON o.id = b.opportunity_id
              WHERE o.ev_per_unit > 0 {where}
            ) WHERE rn = 1""",
        args,
    ).fetchone()
    n = r["n"] or 0
    units = r["units"] or 0.0
    wins = r["wins"] or 0
    losses = r["losses"] or 0
    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "pushes": n - wins - losses,
        "units": round(units, 2),
        "roi_pct": round(units / n * 100, 2) if n else None,
        "hit_pct": round(r["hit"], 1) if r["hit"] is not None else None,
        "avg_clv_pct": round(r["clv"], 2) if r["clv"] is not None else None,
        "low_sample": n < 500,
    }


def _calibration(con) -> list:
    rows = con.execute(
        f"""SELECT CAST(model_prob*20 AS INT) bucket,
                  COUNT(*) n, AVG(model_prob)*100 pred,
                  AVG(CASE WHEN result='win' THEN 1.0 WHEN result='loss' THEN 0.0 END)*100 actual
           FROM (
             SELECT b.model_prob, b.result,
                    ROW_NUMBER() OVER (
                      PARTITION BY b.date, o.game_pk, o.pitcher_id
                      ORDER BY {_book_rank()}, o.ev_per_unit DESC) rn
             FROM bet_results b
             JOIN opportunities o ON o.id = b.opportunity_id
             WHERE o.ev_per_unit > 0 AND b.result IN ('win','loss')
           ) WHERE rn = 1
           GROUP BY bucket HAVING COUNT(*) >= 5 ORDER BY bucket""",
    ).fetchall()
    return [
        {"pred_pct": round(r["pred"], 1), "actual_pct": round(r["actual"], 1), "n": r["n"]}
        for r in rows
    ]


def _market_rows(con, since: str | None) -> list:
    """ONE row per settled (game, pitcher): the canonical book's line
    (PREFERRED_BOOKS chain), the side the model favored, its result/PnL at
    1u flat, plus model point est vs the line. Includes every graded start
    the model had an opinion on — NOT filtered to positive-EV picks — so
    this is the full "model vs the book line" record, a superset of the
    picks Robin would actually have staked (see `is_edge` per row)."""
    where = "AND b.date >= ?" if since else ""
    args = (since,) if since else ()
    return con.execute(
        f"""SELECT * FROM (
              SELECT b.date, o.game_pk, o.pitcher_id, pl.name pitcher,
                     o.book, o.line, b.side, b.odds, b.model_prob,
                     o.ev_per_unit, b.actual_k, b.result, b.pnl_units,
                     pr.point_est,
                     ROW_NUMBER() OVER (
                       PARTITION BY o.game_pk, o.pitcher_id
                       ORDER BY {_book_rank()}, b.model_prob DESC) rn
              FROM bet_results b
              JOIN opportunities o ON o.id = b.opportunity_id
              LEFT JOIN projection_results pr
                     ON pr.game_pk = o.game_pk AND pr.pitcher_id = o.pitcher_id
              LEFT JOIN players pl ON pl.mlb_id = o.pitcher_id
              WHERE o.is_latest = 1 AND b.result IN ('win','loss','push') {where}
            ) WHERE rn = 1 ORDER BY date""",
        args,
    ).fetchall()


def _market_history(rows, limit: int = 300) -> list:
    """Newest-first per-pick ledger for the Performance page's main table:
    what the model favored vs the canonical book line, and how it landed.
    `is_edge` marks the subset that was actually a positive-EV recommendation
    (what Robin would have staked) so one table can show both without
    needing two separate queries/sections."""
    ordered = sorted(rows, key=lambda r: r["date"], reverse=True)[:limit]
    return [
        {
            "date": r["date"],
            "pitcher": r["pitcher"],
            "side": r["side"],
            "line": r["line"],
            "book": r["book"],
            "odds": r["odds"],
            "point_est": r["point_est"],
            "actual_k": r["actual_k"],
            "result": r["result"],
            "pnl_units": round(r["pnl_units"], 2) if r["pnl_units"] is not None else None,
            "is_edge": bool(r["ev_per_unit"] is not None and r["ev_per_unit"] > 0),
        }
        for r in ordered
    ]


def _market_metrics(rows) -> dict:
    """Model vs the K lines it was compared against (every line, not just +EV)."""
    n = len(rows)
    wins = sum(1 for r in rows if r["result"] == "win")
    losses = sum(1 for r in rows if r["result"] == "loss")
    units = sum(r["pnl_units"] or 0.0 for r in rows)
    acc = [
        (abs(r["point_est"] - r["actual_k"]), abs(r["line"] - r["actual_k"]))
        for r in rows
        if r["point_est"] is not None and r["actual_k"] is not None
    ]
    closer = sum(1 for m, l in acc if m < l)
    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "pushes": n - wins - losses,
        "hit_pct": round(wins / (wins + losses) * 100, 1) if wins + losses else None,
        "units": round(units, 2),
        "roi_pct": round(units / n * 100, 2) if n else None,
        "model_mae": round(sum(m for m, _ in acc) / len(acc), 3) if acc else None,
        "line_mae": round(sum(l for _, l in acc) / len(acc), 3) if acc else None,
        "model_closer_pct": round(closer / len(acc) * 100, 1) if acc else None,
        "low_sample": n < 200,
    }


def _market_cum_pnl(rows) -> list:
    by_date: dict = {}
    for r in rows:
        by_date[r["date"]] = by_date.get(r["date"], 0.0) + (r["pnl_units"] or 0.0)
    cum, out = 0.0, []
    for d in sorted(by_date):
        cum += by_date[d]
        out.append({"date": d, "units": round(cum, 2)})
    return out


def _market_monthly(rows) -> list:
    months: dict = {}
    for r in rows:
        if r["point_est"] is None or r["actual_k"] is None:
            continue
        m = months.setdefault(r["date"][:7], {"n": 0, "me": 0.0, "le": 0.0})
        m["n"] += 1
        m["me"] += abs(r["point_est"] - r["actual_k"])
        m["le"] += abs(r["line"] - r["actual_k"])
    return [
        {"month": k, "n": v["n"],
         "model_mae": round(v["me"] / v["n"], 3), "line_mae": round(v["le"] / v["n"], 3)}
        for k, v in sorted(months.items())
    ]


def export_performance(con) -> None:
    today = datetime.now(timezone.utc).date()
    d30 = (today - timedelta(days=30)).isoformat()
    d7 = (today - timedelta(days=7)).isoformat()
    versions = con.execute(
        """SELECT m.version, m.trained_at, m.train_rows, m.valid_mae, m.active,
                  (SELECT COUNT(*) FROM projection_results pr WHERE pr.model_version = m.version) n_scored,
                  (SELECT AVG(pr.abs_error) FROM projection_results pr WHERE pr.model_version = m.version) live_mae
           FROM model_registry m ORDER BY m.trained_at DESC""",
    ).fetchall()
    mkt_rows = _market_rows(con, None)
    mkt_30 = [r for r in mkt_rows if r["date"] >= d30]
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
        "market": {
            "lifetime": _market_metrics(mkt_rows),
            "t30": _market_metrics(mkt_30),
            "cum_pnl": _market_cum_pnl(mkt_rows),
            "monthly": _market_monthly(mkt_rows),
            "history": _market_history(mkt_rows),
        },
        "calibration": _calibration(con),
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
        f"""SELECT * FROM (
              SELECT b.date, pl.name pitcher, b.side, b.line, b.odds, b.model_prob,
                     b.result, b.pnl_units, b.clv_pct, o.book,
                     ROW_NUMBER() OVER (
                       PARTITION BY b.date, o.game_pk, o.pitcher_id
                       ORDER BY {_book_rank()}, o.ev_per_unit DESC) rn
              FROM bet_results b
              JOIN opportunities o ON o.id = b.opportunity_id
              LEFT JOIN players pl ON pl.mlb_id = o.pitcher_id
              WHERE b.date >= ? AND o.ev_per_unit > 0
            ) WHERE rn = 1 ORDER BY date DESC""",
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
    today_s = util.iso(util.board_date())
    klines = con.execute(
        "SELECT COUNT(*) c FROM manual_k_lines WHERE date=?", (today_s,)
    ).fetchone()
    _write("meta.json", {
        "generated_at": db.utcnow(),
        "board_date": today_s,
        "trigger": os.environ.get("KPROJ_TRIGGER", "") or None,
        "model_version": mv["version"] if mv else None,
        "model_trained_at": mv["trained_at"] if mv else None,
        "model_valid_mae": mv["valid_mae"] if mv else None,
        "data_from": span["lo"], "data_to": span["hi"], "game_log_rows": span["n"],
        "odds_credits_remaining": budget["remaining"] if budget else None,
        "k_lines_today": klines["c"],
        "props_fetched_at": db.get_kv(con, f"props_fetched:{today_s}"),
    })


def export_all(con, d) -> None:
    export_today(con, d)
    export_tomorrow(con, d)
    export_performance(con)
    export_recent(con)
    export_meta(con)
