"""Command-line entry points. Run as: python -m kproj <command> [options]

Commands
  init                       create the database schema
  backfill --start --end     resumable historical ingest (statcast + boxscores)
  backfill-weather --years   historical weather for outdoor parks (after backfill)
  daily                      full game-day cycle: ingest, reconcile, project, score, export
  rescore                    re-ingest manual lines + rescore + export (fast; lines edits)
  retrain [--quick]          weekly full retrain on expanding window
  export                     regenerate site JSON only
  status                     quick database/model status
"""
import argparse
import sys
from datetime import date, datetime, timedelta, timezone

from . import config, db, util


def _parse_date(s: str) -> date:
    return date(int(s[:4]), int(s[5:7]), int(s[8:10]))


def cmd_init(_args) -> None:
    with db.session() as con:
        n = con.execute("SELECT COUNT(*) c FROM sqlite_master WHERE type='table'").fetchone()["c"]
    print(f"[init] schema ready at {config.DB_PATH} ({n} tables)")


def cmd_backfill(args) -> None:
    from .ingest.statcast_ingest import backfill

    start = _parse_date(args.start)
    end = _parse_date(args.end) if args.end else util.yesterday_et()
    with db.session() as con:
        backfill(con, start, end)
    print("[backfill] complete")


def cmd_backfill_weather(args) -> None:
    from .ingest.weather import backfill_weather

    years = [int(y) for y in args.years.split(",")]
    with db.session() as con:
        backfill_weather(con, min(years), max(years))
    print("[backfill-weather] complete")


# Statuses a game can no longer move out of. 'Final%' is the codebase-wide test
# for a played game (see mlb_api.final_game_pks); the others will never produce
# a box score, and must count as settled or one postponement keeps the day
# "open" forever. NOT included: 'Suspended' — a suspended game resumes under the
# same game_pk on its original date, so its stats are still coming.
DONE_STATUSES = ("Final%", "Completed%", "Postponed%", "Cancel%")


def _slate_complete(con, d) -> bool:
    """No game on date d can still change status."""
    where = " OR ".join("status LIKE ?" for _ in DONE_STATUSES)
    row = con.execute(
        f"SELECT COUNT(*) n, SUM(CASE WHEN {where} THEN 1 ELSE 0 END) done "
        "FROM games WHERE date=?", (*DONE_STATUSES, util.iso(d))).fetchone()
    return bool(row and row["n"] and row["n"] == row["done"])


def _fully_ingested(con, d) -> bool:
    """Safe to stop re-pulling date d.

    Status alone is not enough: fetch_boxscore swallows HTTP errors and Savant
    routinely serves an empty CSV for a date it hasn't finished processing, so
    a day can be all-Final with no rows stored. Latching on that would lose the
    day permanently — the old five-run schedule papered over it by re-pulling
    four more times. Require actual game logs for every played game."""
    if not _slate_complete(con, d):
        return False
    row = con.execute(
        "SELECT (SELECT COUNT(*) FROM games WHERE date=? AND status LIKE 'Final%') played,"
        "       (SELECT COUNT(DISTINCT game_pk) FROM pitcher_game_logs WHERE date=?) logged",
        (util.iso(d), util.iso(d))).fetchone()
    return bool(row and row["played"] and row["played"] == row["logged"])


def cmd_daily(_args) -> None:
    from .edge.score import score_date
    from .export.site_export import export_all
    from .ingest import mlb_api
    from .ingest.manual_lines import ingest_lines_csv
    from .ingest.odds import fetch_game_lines, fetch_k_props
    from .ingest.statcast_ingest import ingest_finals_for_date
    from .ingest.weather import update_weather_for_date
    from .model.predict import project_date
    from .results.reconcile import reconcile_date

    # The board rolls forward at 8 PM AZ (config.BOARD_ROLLOVER_HOUR), so the
    # evening run projects TOMORROW and settles the slate that just finished.
    today, yday = util.board_date(), util.board_prev_date()
    with db.session() as con:
        print(f"[daily] board {today} (settling: {yday}; wall clock ET {util.today_et()})")
        if db.get_kv(con, f"finals_done:{util.iso(yday)}"):
            print(f"[daily] {yday} already fully ingested — skipping finals pull")
        else:
            n_fin = ingest_finals_for_date(con, yday)
            print(f"[daily] ingested {n_fin} finals from {yday}")
            if _fully_ingested(con, yday):
                db.set_kv(con, f"finals_done:{util.iso(yday)}", 1)
                print(f"[daily] {yday} fully ingested — will not re-pull")
        reconcile_date(con, yday)
        games = mlb_api.fetch_schedule(con, today, today + timedelta(days=1))
        print(f"[daily] schedule: {len(games)} games today+tomorrow")
        pids = [r["pitcher_id"] for r in con.execute(
            "SELECT DISTINCT ps.pitcher_id FROM probable_starters ps "
            "JOIN games g ON g.game_pk=ps.game_pk WHERE g.date>=?", (util.iso(today),))]
        bids = [r["batter_id"] for r in con.execute(
            "SELECT DISTINCT l.batter_id FROM lineups l "
            "JOIN games g ON g.game_pk=l.game_pk WHERE g.date>=?", (util.iso(today),))]
        mlb_api.ensure_players(con, pids + bids)
        update_weather_for_date(con, today)
        # Odds fetches are budget-gated to one snapshot each per day. Auto mode:
        # the first run at/after the window hour that hasn't fetched yet today —
        # self-heals delayed crons and makes late manual runs "just work".
        # Manual runs can force via the workflow's odds_mode input -> KPROJ_ODDS_MODE.
        mode = config.ODDS_MODE
        now_utc = datetime.now(timezone.utc)
        date_s = util.iso(today)

        def _due(window_hours, kv_key):
            """This board day's window has opened and the fetch hasn't fired."""
            return (util.window_open(today, min(window_hours), now_utc)
                    and not db.get_kv(con, kv_key))

        # Whether props already existed BEFORE this run: the movement re-pull is
        # pointless (and costs credits) seconds after the first snapshot, which
        # is what happens when a missed morning run pushes both into the 22:00 slot.
        props_were_fetched = bool(db.get_kv(con, f"props_fetched:{date_s}"))

        if mode in ("both", "gamelines") or (
                mode == "auto" and _due(config.ODDS_GAMELINE_HOURS_UTC, f"gamelines_fetched:{date_s}")):
            fetch_game_lines(con, today)
        if mode in ("both", "props") or (
                mode == "auto" and _due(config.ODDS_PROPS_HOURS_UTC, f"props_fetched:{date_s}")):
            fetch_k_props(con, today)
        # Targeted line-movement refresh: top-edge games only, near first pitch.
        if (mode == "auto" and props_were_fetched
                and _due(config.ODDS_PROPS_REFRESH_HOURS_UTC, f"props_refreshed:{date_s}")):
            from .ingest.odds import refresh_top_props
            refresh_top_props(con, today)
        res = ingest_lines_csv(con)
        for w in res["unmatched"]:
            print(f"[lines] warning: {w}")
        project_date(con, util.iso(today))
        score_date(con, today)
        export_all(con, util.iso(today))
    print("[daily] done")


def cmd_rescore(_args) -> None:
    from .edge.score import score_date
    from .export.site_export import export_all
    from .ingest.manual_lines import ingest_lines_csv

    today = util.board_date()
    with db.session() as con:
        res = ingest_lines_csv(con)
        print(f"[rescore] ingested {res['ingested']} line rows")
        for w in res["unmatched"]:
            print(f"[lines] warning: {w}")
        score_date(con, today)
        export_all(con, util.iso(today))
    print("[rescore] done")


def cmd_retrain(args) -> None:
    from .model.train import train

    with db.session() as con:
        out = train(con, quick=args.quick)
    if out:
        print(f"[retrain] active model: {out['version']} (valid MAE {out['mae']:.3f})")


def cmd_signals(_args) -> None:
    """Hourly best-effort cycle. Never touches kproj.db — see kproj/signals/."""
    from .signals import export as sig_export
    from .signals import fg, gameday, settle, social, store

    with store.session() as con:
        probables = gameday.refresh(con)
        social.scrape(con, probables)
        social.ingest_manual_csv(con, probables)
        fg.refresh(con)
        settle.settle(con)
        sig_export.export(con)
    print("[signals] done")


def cmd_export(_args) -> None:
    from .export.site_export import export_all

    with db.session() as con:
        export_all(con, util.iso(util.board_date()))
    print("[export] site JSON refreshed")


def cmd_status(_args) -> None:
    with db.session() as con:
        for label, q in [
            ("game logs", "SELECT COUNT(*) c, MIN(date) lo, MAX(date) hi FROM pitcher_game_logs"),
            ("batter rows", "SELECT COUNT(*) c, MIN(date) lo, MAX(date) hi FROM batter_game_vs_hand"),
            ("projections", "SELECT COUNT(*) c, MIN(generated_at) lo, MAX(generated_at) hi FROM projections"),
            ("results", "SELECT COUNT(*) c, MIN(date) lo, MAX(date) hi FROM projection_results"),
            ("bets settled", "SELECT COUNT(*) c, MIN(date) lo, MAX(date) hi FROM bet_results"),
        ]:
            r = con.execute(q).fetchone()
            print(f"{label:14} {r['c']:>8}   {r['lo'] or '-'} → {r['hi'] or '-'}")
        m = con.execute("SELECT version, trained_at, valid_mae FROM model_registry WHERE active=1").fetchone()
        print(f"{'model':14} {m['version'] + ' MAE ' + str(m['valid_mae']) if m else 'none trained'}")
        b = con.execute("SELECT month, remaining FROM api_budget ORDER BY month DESC LIMIT 1").fetchone()
        print(f"{'odds budget':14} {str(b['remaining']) + ' credits left (' + b['month'] + ')' if b else 'n/a'}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="kproj", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    bp = sub.add_parser("backfill")
    bp.add_argument("--start", required=True)
    bp.add_argument("--end")
    wp = sub.add_parser("backfill-weather")
    wp.add_argument("--years", required=True, help="e.g. 2022,2026")
    sub.add_parser("daily")
    sub.add_parser("signals")
    sub.add_parser("rescore")
    rt = sub.add_parser("retrain")
    rt.add_argument("--quick", action="store_true")
    sub.add_parser("export")
    sub.add_parser("status")
    args = p.parse_args(argv)
    {
        "init": cmd_init, "backfill": cmd_backfill, "backfill-weather": cmd_backfill_weather,
        "daily": cmd_daily, "signals": cmd_signals, "rescore": cmd_rescore, "retrain": cmd_retrain,
        "export": cmd_export, "status": cmd_status,
    }[args.cmd](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
