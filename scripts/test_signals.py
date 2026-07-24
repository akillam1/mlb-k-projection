"""Offline smoke test for the signals pipeline (no network; fixtures only).
Run: python scripts/test_signals.py"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

TMP = Path(tempfile.mkdtemp(prefix="kproj_signals_"))
os.environ["KPROJ_SIGNALS_DB"] = str(TMP / "signals.db")
os.environ["KPROJ_SITE_DATA"] = str(TMP / "site")
os.environ["KPROJ_CAPPER_CSV"] = str(TMP / "capper_picks.csv")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kproj import config  # noqa: E402
from kproj.signals import export, fg, gameday, parse, settle, social, store  # noqa: E402

PROBS = [(669373, "Tarik Skubal", "2026-07-25", 777001),
         (683003, "Davis Martin", "2026-07-24", 777002),
         (608372, "Tomoyuki Sugano", "2026-07-24", 777003)]

# ---------- parser ----------
cases = [
    ("Third under signaled for tomorrow:\n\nSkubal u8.5 -134 DK\n\nRiding with the Royals again",
     [("under", 8.5, -134, "draftkings", 669373)]),
    ("Surgical Bet of the Day.\n\nDavis Martin Over 4.5 Strikeouts\n\n50 likes and I'll drop #4",
     [("over", 4.5, None, None, 683003)]),
    ("Skubal over 17.5 outs today, lock it", []),                    # outs, not Ks
    ("Kyle Schwarber Over 1.5 Total Bases -120", []),                # not a starter
    ("Sugano Under 4.5 Ks (+105) fanduel", [("under", 4.5, 105, "fanduel", 608372)]),
    ("MARTIN O4.5 says the model", [("over", 4.5, None, None, 683003)]),  # caps shorthand resolves by last name
    ("Davis Martin o4.5 +102 FD", [("over", 4.5, 102, "fanduel", 683003)]),
]
for text, want in cases:
    got = [(p["side"], p["line"], p["odds"], p["book"], p["pitcher_id"])
           for p in parse.extract_picks(text, PROBS)]
    assert got == want, f"\nTEXT: {text!r}\nWANT {want}\nGOT  {got}"
print(f"parser: {len(cases)} cases OK")

# ---------- social (fixture RSS) ----------
RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Third under signaled for tomorrow:

Skubal u8.5 -134 DK</title>
<link>https://nitter.example/IIPatll/status/1001#m</link>
<pubDate>Thu, 23 Jul 2026 21:00:00 GMT</pubDate></item>
<item><title>RT by @IIPatll: someone else's Skubal u8.5 take</title>
<link>https://nitter.example/other/status/1002#m</link>
<pubDate>Thu, 23 Jul 2026 20:00:00 GMT</pubDate></item>
</channel></rss>"""

class FakeResp:
    def __init__(self, text="", status=200, js=None):
        self.text, self.status_code, self._js = text, status, js
        self.content = text.encode()
        self.headers = {}
    def json(self): return self._js

def fake_get(url, **kw):
    if "/rss" in url:
        return FakeResp(RSS) if "IIPatll" in url else FakeResp("nope", 404)
    raise AssertionError(f"unexpected requests.get {url}")

with store.session() as con, mock.patch("kproj.signals.social.requests.get", side_effect=fake_get):
    res = social.scrape(con, PROBS)
    assert res["IIPatll"] == 2 and res["KSplitAnalytics"] == -1, res
    picks = con.execute("SELECT capper, side, line, odds, pitcher_id, date FROM capper_picks").fetchall()
    assert len(picks) == 1, [dict(p) for p in picks]     # RT ignored for picks
    p = picks[0]
    assert (p["capper"], p["side"], p["line"], p["odds"], p["pitcher_id"]) == \
           ("IIPatll", "under", 8.5, -134, 669373)
    # re-scrape: idempotent
    res = social.scrape(con, PROBS)
    assert res["IIPatll"] == 0
    assert con.execute("SELECT COUNT(*) c FROM capper_picks").fetchone()["c"] == 1
print("social scrape: OK (mirror rotation, RT skip, dedupe)")

# ---------- manual CSV ----------
(TMP / "capper_picks.csv").write_text(
    "capper,date,pitcher,side,line,odds,book\n"
    "# comment line\n"
    "# Example,2026-07-25,Tarik Skubal,under,8.5,-134,DK\n"
    "AlexCaruso,,Davis Martin,over,4.5,-115,DK\n", encoding="utf-8")
with store.session() as con:
    n = social.ingest_manual_csv(con, PROBS)
    assert n == 1, n
    r = con.execute("SELECT * FROM capper_picks WHERE source='manual'").fetchone()
    assert r["date"] == "2026-07-24" and r["pitcher_id"] == 683003 and r["book"] == "draftkings"
print("manual csv: OK (auto-date from probables)")

# ---------- gameday (fixture schedule) ----------
SCHED = {"dates": [{"date": "2026-07-24", "games": [{
    "gamePk": 777002, "gameDate": "2026-07-24T23:10:00Z",
    "status": {"detailedState": "Scheduled"},
    "lineups": {"homePlayers": [{"id": 1}], "awayPlayers": []},
    "teams": {"home": {"team": {"name": "Astros"}, "probablePitcher": {"id": 999, "fullName": "Somebody Else"}},
              "away": {"team": {"name": "White Sox"}, "probablePitcher": {"id": 683003, "fullName": "Davis Martin"}}}}]}]}
with store.session() as con, mock.patch("kproj.util.http_get", return_value=SCHED):
    probs = gameday.refresh(con)
    assert (683003, "Davis Martin", "2026-07-24", 777002) in probs
    gs = gameday.game_state(con, "2026-07-24")
    assert gs[777002]["home_lineup"] == 1 and gs[777002]["away_lineup"] == 0
print("gameday: OK")

# ---------- fg (fixture projections) ----------
FG = [{"PlayerName": "Tarik Skubal", "Team": "DET", "xMLBAMID": 669373, "GS": 11, "IP": 64.0, "TBF": 255, "SO": 78},
      {"PlayerName": "Reliever Guy", "Team": "NYY", "xMLBAMID": 5, "GS": 0, "IP": 20, "TBF": 80, "SO": 25}]
with store.session() as con, mock.patch("kproj.util.http_get", return_value=FG):
    assert fg.refresh(con) == 1                      # GS=0 excluded
    assert fg.refresh(con) == 0                      # daily kv-flag
    row = fg.for_pitcher(con, "2026-07-24", 669373)  # date fallback
    assert row and abs(row["k_per_start"] - 78 / 11) < 0.01
print("fg: OK")

# ---------- settle (fixture boxscore) ----------
BOX = {"teams": {"home": {"players": {}}, "away": {"players": {
    "ID669373": {"stats": {"pitching": {"gamesStarted": 1, "strikeOuts": 7}}}}}}}
with store.session() as con:
    con.execute("UPDATE capper_picks SET date='2026-07-20' WHERE capper='IIPatll'")
    con.execute("""INSERT INTO gameday (date, game_pk, status, probables_json, as_of)
                   VALUES ('2026-07-20', 777001, 'Final',
                           '{"DET": {"id": 669373, "name": "Tarik Skubal"}}', 'x')""")
with store.session() as con, mock.patch("kproj.util.http_get", return_value=BOX):
    assert settle.settle(con) == 1
    r = con.execute("SELECT * FROM capper_results").fetchone()
    assert r["result"] == "win" and r["actual_k"] == 7      # under 8.5, 7 Ks
    assert abs(r["pnl_units"] - 100 / 134) < 0.01
print("settle: OK (under 8.5 with 7K = win at -134)")

# ---------- export (real today.json from the repo) ----------
site = TMP / "site"
site.mkdir(exist_ok=True)
repo_data = Path(__file__).resolve().parent.parent / "docs" / "data"
for f in ("today.json", "meta.json"):
    site.joinpath(f).write_bytes(repo_data.joinpath(f).read_bytes())
with store.session() as con:
    export.export(con)
v = json.loads((site / "validation.json").read_text())
assert v["starters"] and v["date"] and "sources" in v
st = v["starters"][0]
for key in ("window", "capper_picks", "lineups", "fg", "k_line", "best_edge"):
    assert key in st, key
assert st["window"]["state"] in ("go", "caution", "wait", "off")
states = {s["window"]["state"] for s in v["starters"]}
assert v["cappers"] and v["cappers"][0]["w"] == 1
print(f"export: OK ({len(v['starters'])} starters, window states seen: {sorted(states)})")
print("\nALL SIGNALS TESTS PASSED")
