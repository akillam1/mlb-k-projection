"""Nightly reconciliation (roadmap §7.5): actuals → projection errors → bet settlement → CLV."""
from .. import db, util


def reconcile_date(con, d, progress=print) -> dict:
    date_s = util.iso(d) if not isinstance(d, str) else d

    # 1) actuals for starters in finals
    starters = con.execute(
        """SELECT l.game_pk, l.pitcher_id, l.k, l.ip_outs FROM pitcher_game_logs l
           JOIN games g ON g.game_pk = l.game_pk
           WHERE g.date=? AND g.status LIKE 'Final%' AND l.started=1""",
        (date_s,),
    ).fetchall()
    db.upsert(con, "actuals", [
        {"game_pk": s["game_pk"], "pitcher_id": s["pitcher_id"], "actual_k": s["k"],
         "ip_outs": s["ip_outs"], "recorded_at": db.utcnow()}
        for s in starters
    ])

    # 2) projection results (operative = latest pre-game projection)
    n_res = 0
    for s in starters:
        proj = con.execute(
            """SELECT id, model_version, point_est, p10, p90 FROM projections
               WHERE game_pk=? AND pitcher_id=? AND is_latest=1
               ORDER BY generated_at DESC LIMIT 1""",
            (s["game_pk"], s["pitcher_id"]),
        ).fetchone()
        if not proj:
            continue
        err = float(proj["point_est"]) - s["k"]
        db.upsert(con, "projection_results", [{
            "projection_id": proj["id"], "game_pk": s["game_pk"], "pitcher_id": s["pitcher_id"],
            "model_version": proj["model_version"], "date": date_s,
            "point_est": proj["point_est"], "actual_k": s["k"],
            "abs_error": round(abs(err), 3), "signed_error": round(err, 3),
            "in_band_10_90": int(proj["p10"] <= s["k"] <= proj["p90"]),
        }])
        n_res += 1

    # 3) settle bets (latest opportunities on manual lines)
    opps = con.execute(
        """SELECT o.*, a.actual_k, p.model_version AS mv FROM opportunities o
           JOIN actuals a ON a.game_pk = o.game_pk AND a.pitcher_id = o.pitcher_id
           JOIN projections p ON p.id = o.projection_id
           JOIN games g ON g.game_pk = o.game_pk
           WHERE g.date=? AND o.is_latest=1
             AND o.id NOT IN (SELECT opportunity_id FROM bet_results)""",
        (date_s,),
    ).fetchall()
    n_bets = 0
    for o in opps:
        k, line = o["actual_k"], float(o["line"])
        if abs(line - round(line)) < 1e-9 and k == int(round(line)):
            result, pnl = "push", 0.0
        elif (o["side"] == "over" and k > line) or (o["side"] == "under" and k < line):
            result, pnl = "win", util.american_to_decimal(o["odds"]) - 1.0
        else:
            result, pnl = "loss", -1.0
        clv = _clv_pct(con, o)
        db.upsert(con, "bet_results", [{
            "opportunity_id": o["id"], "date": date_s, "model_version": o["mv"],
            "side": o["side"], "line": o["line"], "odds": o["odds"],
            "model_prob": o["model_prob"], "actual_k": k, "result": result,
            "pnl_units": round(pnl, 4), "clv_pct": clv, "settled_at": db.utcnow(),
        }])
        n_bets += 1
    con.commit()
    progress(f"[reconcile] {date_s}: {len(starters)} actuals, {n_res} projections, {n_bets} bets settled")
    return {"actuals": len(starters), "results": n_res, "bets": n_bets}


def _clv_pct(con, opp) -> float | None:
    """CLV vs a manually-entered closing line (same pitcher; same book preferred)."""
    date_row = con.execute("SELECT date FROM games WHERE game_pk=?", (opp["game_pk"],)).fetchone()
    if not date_row:
        return None
    closers = con.execute(
        """SELECT book, line, over_odds, under_odds FROM manual_k_lines
           WHERE date=? AND pitcher_id=? AND is_closing=1""",
        (date_row["date"], opp["pitcher_id"]),
    ).fetchall()
    if not closers:
        return None
    close = next((c for c in closers if c["book"] == opp["book"]), closers[0])
    entry_raw = util.american_to_prob(opp["odds"])
    co, cu = util.american_to_prob(close["over_odds"]), util.american_to_prob(close["under_odds"])
    vf_o, vf_u = util.devig_two_way(co, cu)
    close_vf = vf_o if opp["side"] == "over" else vf_u
    # adjust for line moves: a different closing line is still informative only
    # when on the same number; otherwise approximate via half-K shift of 6%/0.5K
    line_shift = (float(close["line"]) - float(opp["line"]))
    if opp["side"] == "under":
        line_shift = -line_shift
    close_vf_adj = min(0.99, max(0.01, close_vf + 0.12 * line_shift))
    entry_vf = float(opp["vigfree_prob"]) if opp["vigfree_prob"] else entry_raw
    if entry_vf <= 0:
        return None
    return round((close_vf_adj - entry_vf) / entry_vf * 100.0, 2)
