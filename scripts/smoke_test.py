"""End-to-end smoke test on synthetic data — no network required.

Generates ~2.5 fake seasons, then runs the full pipeline:
train → project today's slate → ingest manual lines → score edges →
reconcile yesterday → export site JSON. Asserts each stage produced output.

Run from repo root:  python scripts/smoke_test.py
"""
import csv
import json
import os
import random
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="kproj_smoke_"))
os.environ["KPROJ_DB"] = str(TMP / "kproj.db")
os.environ["KPROJ_MODELS_DIR"] = str(TMP / "models")
os.environ["KPROJ_SITE_DATA"] = str(TMP / "site_data")
os.environ["KPROJ_LINES_CSV"] = str(TMP / "manual_lines.csv")

from kproj import db, util  # noqa: E402
from kproj.edge.score import score_date  # noqa: E402
from kproj.export.site_export import export_all  # noqa: E402
from kproj.ingest.manual_lines import ingest_lines_csv  # noqa: E402
from kproj.ingest.statcast_ingest import refresh_league_averages  # noqa: E402
from kproj.ingest.weather import load_ballparks  # noqa: E402
from kproj.model.predict import project_date  # noqa: E402
from kproj.model.train import train  # noqa: E402
from kproj.results.reconcile import reconcile_date  # noqa: E402

rng = random.Random(7)
TODAY = util.today_et()
YDAY = TODAY - timedelta(days=1)

parks = load_ballparks()
TEAMS = sorted({p["team"] for p in parks})
VENUE = {p["team"]: p["venue_name"] for p in parks}
UMPS = [f"Ump {i}" for i in range(1, 25)]

PITCHERS = []
for i, team in enumerate(TEAMS * 2):  # 2 starters per team
    pid = 100000 + i
    PITCHERS.append({
        "id": pid, "team": team, "name": f"Pitcher {i:02d}",
        "throws": rng.choice(["R", "R", "R", "L"]),
        "k_pct": rng.uniform(0.14, 0.34),
        "velo": rng.uniform(91, 98),
        "swstr": rng.uniform(0.07, 0.17),
    })

BATTERS = {}
for t in TEAMS:
    BATTERS[t] = [{
        "id": 500000 + TEAMS.index(t) * 20 + j,
        "k_pct": {"R": rng.uniform(0.12, 0.33), "L": rng.uniform(0.12, 0.33)},
    } for j in range(12)]


def season_dates(year, until=None):
    d, end = date(year, 4, 1), date(year, 9, 25)
    if until and until < end:
        end = until
    out = []
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def gen(con):
    game_pk = 700000
    p_rows, b_rows, g_rows = [], [], []
    next_start = {p["id"]: rng.randint(0, 4) for p in PITCHERS}
    all_dates = (season_dates(2024) + season_dates(2025)
                 + season_dates(2026, until=YDAY))
    by_team = {}
    for p in PITCHERS:
        by_team.setdefault(p["team"], []).append(p)

    for di, d in enumerate(all_dates):
        todays = [p for p in PITCHERS if (di - next_start[p["id"]]) % 5 == 0]
        rng.shuffle(todays)
        used = set()
        for i in range(0, len(todays) - 1, 2):
            p1, p2 = todays[i], todays[i + 1]
            if p1["team"] == p2["team"] or p1["team"] in used or p2["team"] in used:
                continue
            used.update((p1["team"], p2["team"]))
            game_pk += 1
            home, away = (p1, p2) if rng.random() < 0.5 else (p2, p1)
            hs, as_ = rng.randint(0, 9), rng.randint(0, 9)
            g_rows.append({
                "game_pk": game_pk, "date": util.iso(d), "season": d.year,
                "home_team": home["team"], "away_team": away["team"],
                "venue_id": None, "venue_name": VENUE[home["team"]],
                "first_pitch_utc": f"{util.iso(d)}T23:10:00Z",
                "status": "Final", "home_score": hs, "away_score": as_,
                "ump_name": rng.choice(UMPS), "temp_f": rng.uniform(55, 95),
                "wind_mph": rng.uniform(0, 18), "humidity": 50, "precip_prob": 0,
            })
            for p, opp, is_home in ((home, away, 1), (away, home, 0)):
                opp_bats = rng.sample(BATTERS[opp["team"]], 9)
                bf = max(12, min(30, int(rng.gauss(23, 3.5))))
                lineup_k = sum(b["k_pct"][p["throws"]] for b in opp_bats) / 9
                true_p = max(0.05, min(0.45, 0.55 * p["k_pct"] + 0.35 * lineup_k
                                       + rng.gauss(0, 0.02)))
                k = sum(1 for _ in range(bf) if rng.random() < true_p)
                pitches = int(bf * rng.uniform(3.6, 4.3))
                p_rows.append({
                    "pitcher_id": p["id"], "game_pk": game_pk, "date": util.iso(d),
                    "team": p["team"], "opp": opp["team"], "is_home": is_home,
                    "started": 1, "ip_outs": min(27, bf - rng.randint(3, 8)),
                    "bf": bf, "k": k, "bb": rng.randint(0, 4),
                    "h": rng.randint(2, 10), "hr": rng.randint(0, 2),
                    "pitches": pitches,
                    "called_strikes": int(pitches * rng.uniform(0.15, 0.19)),
                    "whiffs": int(pitches * (p["swstr"] + rng.gauss(0, 0.012))),
                    "fb_velo": round(p["velo"] + rng.gauss(0, 0.6), 1),
                    "p_throws": p["throws"],
                })
                for b in opp_bats:
                    pa = rng.randint(3, 5)
                    bk = sum(1 for _ in range(pa) if rng.random() < b["k_pct"][p["throws"]])
                    b_rows.append({
                        "batter_id": b["id"], "game_pk": game_pk, "date": util.iso(d),
                        "team": opp["team"], "vs_hand": p["throws"], "pa": pa, "k": bk,
                    })
    db.upsert(con, "games", g_rows)
    db.upsert(con, "pitcher_game_logs", p_rows)
    db.upsert(con, "batter_game_vs_hand", b_rows)
    db.upsert(con, "players", [
        {"mlb_id": p["id"], "name": p["name"], "throws": p["throws"], "bats": "R",
         "position": "P"} for p in PITCHERS
    ])
    print(f"[gen] {len(g_rows)} games, {len(p_rows)} starts, {len(b_rows)} batter rows")


def gen_slate(con, d, status="Preview"):
    """A 6-game slate on date d with probables + confirmed lineups for half."""
    game_pk = 900000 + d.toordinal() * 20  # ×20 spacing avoids cross-day pk collisions
    teams = rng.sample(TEAMS, 12)
    starters = []
    for i in range(6):
        home_t, away_t = teams[2 * i], teams[2 * i + 1]
        ph = next(p for p in PITCHERS if p["team"] == home_t)
        pa_ = next(p for p in PITCHERS if p["team"] == away_t)
        pk = game_pk + i
        db.upsert(con, "games", [{
            "game_pk": pk, "date": util.iso(d), "season": d.year,
            "home_team": home_t, "away_team": away_t, "venue_id": None,
            "venue_name": VENUE[home_t],
            "first_pitch_utc": f"{util.iso(d)}T{17 + i}:10:00Z",
            "status": status, "ump_name": "", "temp_f": 78.0, "wind_mph": 7.0,
        }])
        db.upsert(con, "probable_starters", [
            {"game_pk": pk, "team": home_t, "pitcher_id": ph["id"],
             "pitcher_name": ph["name"], "as_of": db.utcnow()},
            {"game_pk": pk, "team": away_t, "pitcher_id": pa_["id"],
             "pitcher_name": pa_["name"], "as_of": db.utcnow()},
        ])
        if i < 3:  # confirmed lineups for half the slate
            for team in (home_t, away_t):
                db.upsert(con, "lineups", [
                    {"game_pk": pk, "team": team, "batting_order": o + 1,
                     "batter_id": BATTERS[team][o]["id"], "source": "confirmed",
                     "as_of": db.utcnow()} for o in range(9)
                ])
        starters.extend([(pk, ph), (pk, pa_)])
    return starters


def write_lines(starters, d):
    with open(os.environ["KPROJ_LINES_CSV"], "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "pitcher", "book", "line", "over_odds", "under_odds", "closing"])
        for _, p in starters[:8]:
            w.writerow([util.iso(d), p["name"],
                        rng.choice(["draftkings", "fanduel", "bovada"]),
                        rng.choice([4.5, 5.5, 6.5, 7.0]), -115, -105, 0])
        # one closing line for CLV
        _, p0 = starters[0]
        w.writerow([util.iso(d), p0["name"], "draftkings", 5.5, -125, +105, 1])


def fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)


def main():
    with db.session() as con:
        gen(con)
        refresh_league_averages(con)

        out = train(con, quick=True)
        if not out:
            fail("training produced no model")

        # --- yesterday: project pre-game, score lines, then finalize + reconcile
        y_starters = gen_slate(con, YDAY, status="Preview")
        if project_date(con, util.iso(YDAY)) == 0:
            fail("no projections for yesterday slate")
        write_lines(y_starters, YDAY)
        res = ingest_lines_csv(con)
        if res["ingested"] == 0:
            fail("no manual lines ingested (yesterday)")
        if score_date(con, YDAY) == 0:
            fail("no opportunities scored (yesterday)")
        # finalize: write actual results for those starts
        for pk, p in y_starters:
            game = con.execute("SELECT * FROM games WHERE game_pk=?", (pk,)).fetchone()
            opp = game["away_team"] if p["team"] == game["home_team"] else game["home_team"]
            bf = 23
            k = sum(1 for _ in range(bf) if rng.random() < p["k_pct"])
            db.upsert(con, "pitcher_game_logs", [{
                "pitcher_id": p["id"], "game_pk": pk, "date": util.iso(YDAY),
                "team": p["team"], "opp": opp,
                "is_home": int(p["team"] == game["home_team"]), "started": 1,
                "ip_outs": 18, "bf": bf, "k": k, "bb": 2, "h": 5, "hr": 1,
                "pitches": 95, "called_strikes": 16, "whiffs": 11,
                "fb_velo": p["velo"], "p_throws": p["throws"],
            }])
            con.execute("UPDATE games SET status='Final', home_score=4, away_score=3 "
                        "WHERE game_pk=?", (pk,))
        r = reconcile_date(con, YDAY)
        if r["results"] == 0 or r["bets"] == 0:
            fail(f"reconcile incomplete: {r}")

        # --- today: fresh slate, project + score + export
        t_starters = gen_slate(con, TODAY, status="Preview")
        if project_date(con, util.iso(TODAY)) == 0:
            fail("no projections for today")
        write_lines(t_starters, TODAY)
        ingest_lines_csv(con)
        score_date(con, TODAY)
        export_all(con, util.iso(TODAY))

        # --- assertions on exports
        site = Path(os.environ["KPROJ_SITE_DATA"])
        for name in ("today.json", "performance.json", "recent.json", "meta.json"):
            p = site / name
            if not p.exists():
                fail(f"missing export {name}")
            json.loads(p.read_text())
        today = json.loads((site / "today.json").read_text())
        with_proj = [s for s in today["starters"] if s.get("proj")]
        with_edge = [s for s in with_proj if any(e["ev_per_unit"] > 0 for e in s.get("edges", []))]
        perf = json.loads((site / "performance.json").read_text())
        if not with_proj:
            fail("today.json has no projections")
        if perf["projection"]["lifetime"]["n"] == 0:
            fail("performance.json has no reconciled projections")
        if perf["betting"]["lifetime"]["n"] == 0:
            fail("performance.json has no settled bets")

        ms = con.execute("SELECT version, valid_mae FROM model_registry WHERE active=1").fetchone()
        print("\n================ SMOKE TEST PASS ================")
        print(f"model            {ms['version']}  valid MAE {ms['valid_mae']}")
        print(f"today starters   {len(today['starters'])} ({len(with_proj)} projected, "
              f"{len(with_edge)} with +EV edge)")
        print(f"reconciled       {perf['projection']['lifetime']['n']} projections, "
              f"{perf['betting']['lifetime']['n']} bets, "
              f"ROI {perf['betting']['lifetime']['roi_pct']}%")
        sample = next(s for s in with_proj if s.get("edges"))
        print(f"sample card      {sample['pitcher']}: point {sample['proj']['point']}, "
              f"p10-p90 {sample['proj']['p10']}-{sample['proj']['p90']}, "
              f"conf {sample['proj']['lineup_confidence']}")
        for e in sample["edges"][:2]:
            print(f"  edge           {e['side']} {e['line']} @{e['odds']} {e['book']}: "
                  f"EV {e['ev_per_unit']:+.3f}, ¼K {e['kelly_quarter']:.3f}, score {e['score']:+.3f}")
        print(f"workspace        {TMP}")


if __name__ == "__main__":
    main()
