"""Edge scoring & ranking (roadmap §6).

De-vig (multiplicative) → model probability vs vig-free book probability →
EV per unit, quarter-Kelly, composite score with book and confidence weights.
The 'no negative EV' rule is applied at display time (§6.4).
"""
import json

from .. import config, db, util
from ..model.predict import probs_for_line


def book_weight(book: str) -> float:
    return config.BOOK_WEIGHT_MAJOR if (book or "").lower() in config.MAJOR_BOOKS else config.BOOK_WEIGHT_OTHER


def confidence_weight(lineup_conf: float, p25: float, p75: float) -> float:
    spread_term = max(0.5, min(1.0, 1.0 - (p75 - p25) / 10.0))
    return (0.5 + 0.5 * float(lineup_conf)) * spread_term


def score_line(proj: dict, line: float, over_odds: int, under_odds: int, book: str) -> list[dict]:
    """Both sides of one K line vs one projection. Returns two opportunity dicts."""
    dist = {k: proj[k] for k in ("point", "p10", "p25", "p50", "p75", "p90")}
    probs = probs_for_line(dist, float(line))
    raw_o = util.american_to_prob(over_odds)
    raw_u = util.american_to_prob(under_odds)
    vf_o, vf_u = util.devig_two_way(raw_o, raw_u)
    bw = book_weight(book)
    cw = confidence_weight(proj["lineup_confidence"], proj["p25"], proj["p75"])
    out = []
    for side, odds, p_win, p_lose, vf in (
        ("over", over_odds, probs["over"], probs["under"], vf_o),
        ("under", under_odds, probs["under"], probs["over"], vf_u),
    ):
        payout = util.american_to_decimal(odds) - 1.0
        ev = p_win * payout - p_lose  # push returns stake: contributes 0
        kelly = max(0.0, ev / payout) if payout > 0 else 0.0
        out.append({
            "side": side, "odds": int(odds), "model_prob": round(p_win, 4),
            "vigfree_prob": round(vf, 4), "ev_per_unit": round(ev, 4),
            "kelly_quarter": round(config.KELLY_FRACTION * kelly, 4),
            "score": round(ev * bw * cw, 4),
        })
    return out


def latest_projection(con, game_pk: int, pitcher_id: int):
    return con.execute(
        """SELECT id, point_est AS point, p10, p25, p50, p75, p90, lineup_confidence
           FROM projections WHERE game_pk=? AND pitcher_id=? AND is_latest=1
           ORDER BY generated_at DESC LIMIT 1""",
        (game_pk, pitcher_id),
    ).fetchone()


def score_date(con, d, progress=print) -> int:
    """Score every manual K line for date d against the latest projections."""
    date_s = util.iso(d) if not isinstance(d, str) else d
    lines = con.execute(
        """SELECT m.*, ps.game_pk FROM manual_k_lines m
           JOIN games g ON g.date = m.date
           JOIN probable_starters ps ON ps.game_pk = g.game_pk AND ps.pitcher_id = m.pitcher_id
           WHERE m.date = ? AND m.pitcher_id IS NOT NULL AND m.is_closing = 0""",
        (date_s,),
    ).fetchall()
    n = 0
    for ln in lines:
        proj = latest_projection(con, ln["game_pk"], ln["pitcher_id"])
        if not proj:
            continue
        proj_d = dict(proj)
        proj_d["point"] = proj_d.pop("point")
        con.execute(
            """UPDATE opportunities SET is_latest=0
               WHERE projection_id IN (SELECT id FROM projections WHERE game_pk=? AND pitcher_id=?)
                 AND book=? AND line=?""",
            (ln["game_pk"], ln["pitcher_id"], ln["book"], ln["line"]),
        )
        for opp in score_line(proj_d, ln["line"], ln["over_odds"], ln["under_odds"], ln["book"]):
            con.execute(
                """INSERT INTO opportunities
                   (projection_id, game_pk, pitcher_id, source, book, line, side, odds,
                    model_prob, vigfree_prob, ev_per_unit, kelly_quarter, score, created_at, is_latest)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                (
                    proj["id"], ln["game_pk"], ln["pitcher_id"], "manual", ln["book"],
                    ln["line"], opp["side"], opp["odds"], opp["model_prob"], opp["vigfree_prob"],
                    opp["ev_per_unit"], opp["kelly_quarter"], opp["score"], db.utcnow(),
                ),
            )
            n += 1
    con.commit()
    progress(f"[score] {n} opportunity rows for {date_s} ({len(lines)} lines)")
    return n
