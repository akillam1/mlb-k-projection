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
from datetime import date, timedelta

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


def cmd_daily(_args) -> None:
    from .edge.score import score_date
    from .export.site_export import export_all
    from .ingest import mlb_api
    from .ingest.manual_lines import ingest_lines_csv
    from .ingest.odds import fetch_game_lines
    from .ingest.statcast_ingest import ingest_finals_for_date
    from .ingest.weather import update_weather_for_date
    from .model.predict import project_date
    from .results.reconcile import reconcile_date

    today, yday = util.today_et(), util.yesterday_et()
    with db.session() as con:
        print(f"[daily] {today} (yesterday: {yday})")
        n_fin = ingest_finals_for_date(con, yday)
        print(f"[daily] ingested {n_fin} finals from {yday}")
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
        fetch_game_lines(con, today)
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

    today = util.today_et()
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


def cmd_export(_args) -> None:
    from .export.site_export import export_all

    with db.session() as con:
        export_all(con, util.iso(util.today_et()))
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
    sub.add_parser("rescore")
    rt = sub.add_parser("retrain")
    rt.add_argument("--quick", action="store_true")
    sub.add_parser("export")
    sub.add_parser("status")
    args = p.parse_args(argv)
    {
        "init": cmd_init, "backfill": cmd_backfill, "backfill-weather": cmd_backfill_weather,
        "daily": cmd_daily, "rescore": cmd_rescore, "retrain": cmd_retrain,
        "export": cmd_export, "status": cmd_status,
    }[args.cmd](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
