"""Assemble docs/data/validation.json for the Signals page.

Joins the model's own board (docs/data/today.json — no big-DB access needed)
with live slate state, FanGraphs comparison, capper picks and their records,
and a transparent rule-based 'pick window' per starter.
"""
import json
from datetime import datetime, timezone

from .. import config, util
from . import fg, gameday, store


def _load_site_json(name):
    try:
        with open(config.SITE_DATA_DIR / name, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _hours_since(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return round((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 1)
    except ValueError:
        return None


def _best_edge(edges: list) -> dict | None:
    pos = [e for e in edges or [] if e.get("prob_edge") is not None]
    return max(pos, key=lambda e: e["prob_edge"]) if pos else None


def _our_prob_for(edges: list, side: str, line: float) -> float | None:
    cands = [e["model_prob"] for e in edges or []
             if e.get("side") == side and e.get("line") == line and e.get("model_prob") is not None]
    return max(cands) if cands else None


def _pick_window(st: dict, gd: dict | None, scratched: bool) -> dict:
    status = (gd or {}).get("status") or st.get("status") or ""
    if scratched:
        return {"state": "off", "reasons": ["Probable starter changed — pick is off the board"]}
    if any(s in status for s in ("In Progress", "Final", "Game Over", "Completed")):
        return {"state": "off", "reasons": [f"Game status: {status}"]}
    reasons, state = [], "go"
    k_line = st.get("k_line") or {}
    best = _best_edge(st.get("edges"))
    if not k_line.get("line"):
        return {"state": "wait", "reasons": ["No K line posted yet"]}
    if best is None or best["prob_edge"] < config.SIGNALS_EDGE_GO:
        pe = f"{best['prob_edge']*100:+.1f}" if best else "n/a"
        return {"state": "wait", "reasons": [f"Model edge below {config.SIGNALS_EDGE_GO*100:.0f} pts ({pe})"]}
    reasons.append(f"{best['side']} {best['line']} edge {best['prob_edge']*100:+.1f} pts @{best['book']}")
    if gd:
        opp_lineup = gd["away_lineup"] if st.get("home") else gd["home_lineup"]
        if not opp_lineup:
            state = "caution"
            reasons.append("Opposing lineup not posted yet")
        elif st.get("proj", {}).get("lineup_tier") != "confirmed":
            reasons.append("Lineup posted; projection not yet re-run on it")
    age = _hours_since(k_line.get("latest_at") or k_line.get("fetched_at"))
    if age is not None and age > config.SIGNALS_LINE_STALE_H:
        state = "caution"
        reasons.append(f"K line snapshot {age:.0f}h old — verify current price")
    move = k_line.get("move")
    if move:
        state = "caution"
        reasons.append(f"Line moved {move:+g} since open — edge may be priced out")
    return {"state": state, "reasons": reasons}


def _capper_boards(con, date_s: str) -> tuple[dict, list, list]:
    """(picks by pitcher_id for date, leaderboard rows, feed rows)."""
    by_pid: dict = {}
    for r in con.execute("SELECT * FROM capper_picks WHERE date=?", (date_s,)):
        by_pid.setdefault(r["pitcher_id"], []).append(dict(r))
    lb = [dict(r) for r in con.execute(
        """SELECT p.capper,
                  SUM(CASE WHEN r.result='win' THEN 1 ELSE 0 END)  w,
                  SUM(CASE WHEN r.result='loss' THEN 1 ELSE 0 END) l,
                  SUM(CASE WHEN r.result='push' THEN 1 ELSE 0 END) p,
                  ROUND(SUM(CASE WHEN r.result IN ('win','loss') THEN r.pnl_units ELSE 0 END), 2) units,
                  COUNT(r.pick_id) settled,
                  MAX(r.date) last_pick
           FROM capper_picks p JOIN capper_results r ON r.pick_id=p.id
           WHERE r.result != 'void'
           GROUP BY p.capper ORDER BY units DESC""")]
    pending = {r["capper"]: r["n"] for r in con.execute(
        """SELECT capper, COUNT(*) n FROM capper_picks p
           WHERE NOT EXISTS (SELECT 1 FROM capper_results r WHERE r.pick_id=p.id)
           GROUP BY capper""")}
    for row in lb:
        row["pending"] = pending.get(row["capper"], 0)
        row["display"] = config.SIGNALS_CAPPERS.get(row["capper"], row["capper"])
    feed = []
    for r in con.execute(
            "SELECT capper, post_id, posted_at, text, url FROM capper_posts "
            "ORDER BY posted_at DESC LIMIT ?", (config.SIGNALS_FEED_POSTS,)):
        parsed = con.execute("SELECT 1 FROM capper_picks WHERE post_id=? LIMIT 1",
                             (r["post_id"],)).fetchone() is not None
        feed.append({"capper": r["capper"],
                     "display": config.SIGNALS_CAPPERS.get(r["capper"], r["capper"]),
                     "posted_at": r["posted_at"],
                     "text": (r["text"] or "")[:300], "url": r["url"], "parsed": parsed})
    return by_pid, lb, feed


def export(con) -> None:
    today = _load_site_json("today.json") or {}
    # Follow the published board rather than the clock. Between the 03:00 UTC
    # rollover and the moment the daily run finishes, board_date() is already
    # tomorrow while today.json still holds tonight's slate — and hourly.yml
    # fires at 03:25, right inside that gap. Labelling tonight's starters with
    # tomorrow's date empties every lineup, pick and FG lookup.
    date_s = today.get("date") or util.iso(util.board_date())
    meta = _load_site_json("meta.json") or {}
    gstate = gameday.game_state(con, date_s)
    picks_by_pid, leaderboard, feed = _capper_boards(con, date_s)

    starters = []
    for st in today.get("starters", []):
        gd = gstate.get(st.get("game_pk"))
        scratched = False
        if gd and gd.get("probables_json"):
            ids = {p["id"] for p in json.loads(gd["probables_json"]).values()}
            started = any(s in (gd.get("status") or "") for s in ("In Progress", "Final", "Live"))
            scratched = bool(ids) and st.get("pitcher_id") not in ids and not started
        fg_row = fg.for_pitcher(con, date_s, st.get("pitcher_id"))
        proj_pt = (st.get("proj") or {}).get("point")
        cp = []
        for p in picks_by_pid.get(st.get("pitcher_id"), []):
            our = _our_prob_for(st.get("edges"), p["side"], p["line"])
            if our is None and proj_pt is not None:
                agrees = (proj_pt > p["line"]) == (p["side"] == "over")
            else:
                agrees = our is not None and our >= 0.5
            cp.append({"capper": p["capper"],
                       "display": config.SIGNALS_CAPPERS.get(p["capper"], p["capper"]),
                       "side": p["side"], "line": p["line"], "odds": p["odds"],
                       "book": p["book"], "source": p["source"],
                       "our_prob": round(our, 3) if our is not None else None,
                       "agrees": agrees})
        best = _best_edge(st.get("edges"))
        starters.append({
            "game_pk": st.get("game_pk"), "pitcher": st.get("pitcher"),
            "pitcher_id": st.get("pitcher_id"), "team": st.get("team"),
            "opp": st.get("opp"), "home": st.get("home"), "time_et": st.get("time_et"),
            "status": (gd or {}).get("status") or st.get("status"),
            "proj_point": proj_pt,
            "p25": (st.get("proj") or {}).get("p25"), "p75": (st.get("proj") or {}).get("p75"),
            "lineup_tier": (st.get("proj") or {}).get("lineup_tier"),
            "k_line": st.get("k_line"),
            "best_edge": {"side": best["side"], "line": best["line"], "book": best["book"],
                          "prob_edge": best["prob_edge"], "model_prob": best["model_prob"],
                          "odds": best["odds"]} if best else None,
            "fg": (fg_row | {"delta": round(proj_pt - fg_row["k_per_start"], 2)}
                   if fg_row and proj_pt is not None else fg_row),
            "lineups": {"home": bool(gd and gd["home_lineup"]), "away": bool(gd and gd["away_lineup"])},
            "scratched": scratched,
            "capper_picks": cp,
            "window": _pick_window(st, gd, scratched),
        })

    x_sources = {}
    for handle in config.SIGNALS_CAPPERS:
        x_sources[handle] = store.source_report(con, f"x:{handle}")
    payload = {
        "generated_at": store.utcnow(),
        "date": date_s,
        "board_generated_at": today.get("generated_at"),
        "sources": {
            "x": x_sources,
            "statsapi": store.source_report(con, "statsapi"),
            "fangraphs": store.source_report(con, "fangraphs"),
            "odds_props_fetched_at": meta.get("props_fetched_at"),
            "odds_credits_remaining": meta.get("odds_credits_remaining"),
        },
        "starters": starters,
        "cappers": leaderboard,
        "feed": feed,
    }
    config.SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.SITE_DATA_DIR / "validation.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"), default=float)
    print(f"[signals] validation.json: {len(starters)} starters, "
          f"{sum(len(s['capper_picks']) for s in starters)} picks today, {len(feed)} feed posts")
