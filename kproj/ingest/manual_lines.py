"""Manual K-line entry — the zero-cost replacement for paid prop odds.

Edit lines/manual_lines.csv from your phone (GitHub app → repo → file → pencil
icon) or any editor. Pushing the change triggers the rescore workflow.

CSV columns:
  date,pitcher,book,line,over_odds,under_odds,closing
  2026-06-12,Tarik Skubal,draftkings,7.5,-115,-105,0

- pitcher: full or last name; fuzzy-matched to that date's probable starters.
- closing: 1 = this is the closing line (used for CLV); 0 or blank otherwise.
"""
import csv

from .. import config, db, util


def ingest_lines_csv(con) -> dict:
    path = config.LINES_CSV
    if not path.exists():
        return {"ingested": 0, "unmatched": []}
    ingested, unmatched = 0, []
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("date") or "").strip()]
    for r in rows:
        date = r["date"].strip()
        raw_name = (r.get("pitcher") or "").strip()
        if not raw_name or raw_name.startswith("#"):
            continue
        try:
            line = float(r["line"])
            over_odds = int(r["over_odds"])
            under_odds = int(r["under_odds"])
        except (KeyError, TypeError, ValueError):
            unmatched.append(f"{date} {raw_name}: bad line/odds values")
            continue
        probables = con.execute(
            """SELECT ps.pitcher_id, ps.pitcher_name FROM probable_starters ps
               JOIN games g ON g.game_pk = ps.game_pk WHERE g.date = ?""",
            (date,),
        ).fetchall()
        pid = None
        for p in probables:
            if util.name_matches(raw_name, p["pitcher_name"]):
                pid = p["pitcher_id"]
                break
        if pid is None:
            unmatched.append(f"{date} {raw_name}: no probable starter matched")
        con.execute(
            """INSERT OR IGNORE INTO manual_k_lines
               (date, pitcher_raw, pitcher_id, book, line, over_odds, under_odds, is_closing, entered_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                date, raw_name, pid, (r.get("book") or "unknown").strip().lower(),
                line, over_odds, under_odds,
                1 if (r.get("closing") or "").strip() in ("1", "true", "yes") else 0,
                db.utcnow(),
            ),
        )
        # Re-resolve pitcher_id on rows entered before probables were known
        con.execute(
            "UPDATE manual_k_lines SET pitcher_id=? WHERE date=? AND pitcher_raw=? AND pitcher_id IS NULL",
            (pid, date, raw_name),
        )
        ingested += 1
    return {"ingested": ingested, "unmatched": unmatched}


def lines_for_date(con, d) -> list:
    return con.execute(
        "SELECT * FROM manual_k_lines WHERE date=? AND pitcher_id IS NOT NULL", (util.iso(d),)
    ).fetchall()
