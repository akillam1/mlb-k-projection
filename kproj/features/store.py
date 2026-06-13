"""FeatureStore — loads history once, answers leakage-free as-of-date feature queries.

Implements roadmap §5.1–5.3:
  Tier 1: pitcher EWMA CSW%/SwStr%/K%, opponent lineup K% vs hand, expected BF inputs
  Tier 2: K-BB%, fastball velo delta, park K factor, home-plate ump factor
  Tier 3: temperature, wind, home/away, days rest
  §5.2:  EWMA half-life ≈ 5 starts; Marcel-style prior + Tango stabilization BF/(BF+70)
  §5.3:  lineup fallback cascade with lineup_confidence ∈ {1.0, 0.6, 0.3}
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .. import config
from ..ingest.weather import load_ballparks, park_for_venue

LINEUP_SLOT_WEIGHTS = np.array([4.65, 4.55, 4.43, 4.33, 4.22, 4.12, 4.01, 3.90, 3.79])
LINEUP_SLOT_WEIGHTS = LINEUP_SLOT_WEIGHTS / LINEUP_SLOT_WEIGHTS.sum()


def _d2i(s) -> np.ndarray:
    """date strings → int days since epoch."""
    return pd.to_datetime(s).values.astype("datetime64[D]").astype(int)


def _di(d: date) -> int:
    return (d - date(1970, 1, 1)).days


class FeatureStore:
    def __init__(self, con):
        self.con = con
        from .. import db as _db

        self.lg_k = _db.get_kv(con, "league_k_pct", config.LEAGUE_K_PCT)
        self.lg_bf = _db.get_kv(con, "league_bf_per_start", config.LEAGUE_BF_PER_START)

        # ---- pitcher starts (started=1), per-pitcher arrays sorted by date
        pgl = pd.read_sql_query(
            """SELECT pitcher_id, game_pk, date, bf, k, bb, pitches,
                      called_strikes, whiffs, fb_velo
               FROM pitcher_game_logs WHERE started=1 ORDER BY pitcher_id, date""",
            con,
        )
        self.pitcher_starts = {}
        if not pgl.empty:
            pgl["di"] = _d2i(pgl["date"])
            for pid, g in pgl.groupby("pitcher_id", sort=False):
                self.pitcher_starts[int(pid)] = {
                    c: g[c].to_numpy() for c in
                    ("di", "game_pk", "bf", "k", "bb", "pitches", "called_strikes", "whiffs", "fb_velo")
                }

        # ---- pitcher season/yearly BF+K for priors (all appearances)
        yearly = pd.read_sql_query(
            """SELECT pitcher_id, substr(date,1,4) AS yr, SUM(bf) bf, SUM(k) k
               FROM pitcher_game_logs GROUP BY pitcher_id, yr""",
            con,
        )
        self.pitcher_year = {
            (int(r.pitcher_id), int(r.yr)): (int(r.bf), int(r.k))
            for r in yearly.itertuples(index=False)
        }

        # ---- batter cumulative arrays per (batter, hand)
        bat = pd.read_sql_query(
            """SELECT batter_id, date, team, vs_hand, pa, k
               FROM batter_game_vs_hand ORDER BY batter_id, vs_hand, date""",
            con,
        )
        self.batter = {}
        self.team_day = {}  # (team, hand) → arrays for team-aggregate fallbacks
        if not bat.empty:
            bat["di"] = _d2i(bat["date"])
            for (bid, hand), g in bat.groupby(["batter_id", "vs_hand"], sort=False):
                self.batter[(int(bid), hand)] = {
                    "di": g["di"].to_numpy(),
                    "cpa": g["pa"].to_numpy().cumsum(),
                    "ck": g["k"].to_numpy().cumsum(),
                }
            for (team, hand), g in bat.groupby(["team", "vs_hand"], sort=False):
                gg = g.groupby("di", sort=True)[["pa", "k"]].sum().reset_index()
                self.team_day[(team, hand)] = {
                    "di": gg["di"].to_numpy(),
                    "cpa": gg["pa"].to_numpy().cumsum(),
                    "ck": gg["k"].to_numpy().cumsum(),
                }
            # per-team batter index for tier-2 lineups
            self.bat_team = bat[["batter_id", "team", "vs_hand", "di", "pa", "k"]]
        else:
            self.bat_team = pd.DataFrame(
                columns=["batter_id", "team", "vs_hand", "di", "pa", "k"]
            )

        # ---- games table bits: run environment + ump factors
        gms = pd.read_sql_query(
            """SELECT game_pk, date, home_team, away_team, home_score, away_score, ump_name
               FROM games WHERE status LIKE 'Final%'""",
            con,
        )
        self.team_runs = {}
        self.ump = {}
        if not gms.empty:
            gms["di"] = _d2i(gms["date"])
            rows = []
            for r in gms.itertuples(index=False):
                if r.home_score is not None and r.away_score is not None:
                    rows.append((r.home_team, r.di, r.home_score))
                    rows.append((r.away_team, r.di, r.away_score))
            if rows:
                tr = pd.DataFrame(rows, columns=["team", "di", "runs"]).sort_values(["team", "di"])
                for team, g in tr.groupby("team", sort=False):
                    self.team_runs[team] = {
                        "di": g["di"].to_numpy(),
                        "cruns": g["runs"].to_numpy().cumsum(),
                    }
            # ump factor: total game Ks vs league average, shrunk
            gk = pd.read_sql_query(
                "SELECT game_pk, SUM(k) AS gk FROM pitcher_game_logs GROUP BY game_pk", con
            )
            m = gms.merge(gk, on="game_pk").dropna(subset=["ump_name", "gk"])
            m = m[m["ump_name"] != ""]
            if len(m) > 30:
                lg_gk = m["gk"].mean()
                for ump_name, g in m.groupby("ump_name"):
                    n = len(g)
                    raw = g["gk"].mean() / lg_gk
                    self.ump[ump_name] = 1.0 + (raw - 1.0) * (n / (n + 50.0))

        self.parks = load_ballparks()

    # ------------------------------------------------------------------ pitcher
    def pitcher_features(self, pid: int, as_of: date) -> dict:
        di = _di(as_of)
        s = self.pitcher_starts.get(int(pid))
        f = {}
        H = config.EWMA_HALF_LIFE_STARTS
        if s is not None:
            mask = (s["di"] < di) & (s["di"] >= di - 365)
            idx = np.where(mask)[0][-25:]
        else:
            idx = np.array([], dtype=int)

        if len(idx):
            order = idx[::-1]  # most recent first
            w = 0.5 ** (np.arange(len(order)) / H)
            bf, k, bb = s["bf"][order], s["k"][order], s["bb"][order]
            pit = s["pitches"][order].astype(float)
            cs, wh = s["called_strikes"][order], s["whiffs"][order]
            f["k_pct_ewma"] = float(np.sum(w * k) / max(np.sum(w * bf), 1))
            f["kbb_pct_ewma"] = float(np.sum(w * (k - bb)) / max(np.sum(w * bf), 1))
            ok = ~(pd.isna(cs) | pd.isna(wh)) & (pit > 0)
            if ok.any():
                f["csw_ewma"] = float(np.sum(w[ok] * (cs[ok].astype(float) + wh[ok].astype(float)))
                                      / max(np.sum(w[ok] * pit[ok]), 1))
                f["swstr_ewma"] = float(np.sum(w[ok] * wh[ok].astype(float))
                                        / max(np.sum(w[ok] * pit[ok]), 1))
            else:
                f["csw_ewma"], f["swstr_ewma"] = config.LEAGUE_CSW, config.LEAGUE_SWSTR
            f["bf_avg3"] = float(np.mean(bf[:3]))
            f["days_rest"] = float(min(di - s["di"][idx[-1]], 30))
            velo = s["fb_velo"][order].astype(float)
            v3 = velo[:3][~pd.isna(velo[:3])]
            m90 = (s["di"] < di) & (s["di"] >= di - 90)
            v90 = s["fb_velo"][m90].astype(float)
            v90 = v90[~pd.isna(v90)]
            f["fb_velo_delta"] = float(v3.mean() - v90.mean()) if len(v3) and len(v90) else 0.0
            season_mask = (s["di"] < di) & (s["di"] >= _di(date(as_of.year, 1, 1)))
            bf_season = float(s["bf"][season_mask].sum())
        else:
            f["k_pct_ewma"] = self.lg_k
            f["kbb_pct_ewma"] = config.LEAGUE_KBB_PCT
            f["csw_ewma"], f["swstr_ewma"] = config.LEAGUE_CSW, config.LEAGUE_SWSTR
            f["bf_avg3"] = self.lg_bf
            f["days_rest"] = 6.0
            f["fb_velo_delta"] = 0.0
            bf_season = 0.0

        # Marcel-style prior from last season (any role), regressed to league
        bf_ly, k_ly = self.pitcher_year.get((int(pid), as_of.year - 1), (0, 0))
        reg = config.PRIOR_REGRESSION_BF
        prior = (k_ly + self.lg_k * reg) / (bf_ly + reg)
        wcur = bf_season / (bf_season + config.STABILIZATION_BF)
        f["prior_k_pct"] = float(prior)
        f["stab_weight"] = float(wcur)
        f["blended_k_pct"] = float(wcur * f["k_pct_ewma"] + (1 - wcur) * prior)
        return f

    # ------------------------------------------------------------------ batters
    def batter_k_rate(self, bid: int, hand: str, as_of: date) -> tuple[float, float]:
        """Blended K% (30d 0.6 / season 0.4), shrunk to league. Returns (rate, eff_pa)."""
        a = self.batter.get((int(bid), hand))
        lg = self.lg_k
        if a is None:
            return lg, 0.0
        di = _di(as_of)

        def window(lo, hi):
            i_lo = np.searchsorted(a["di"], lo, side="left")
            i_hi = np.searchsorted(a["di"], hi, side="left")
            if i_hi <= i_lo:
                return 0, 0
            pa = a["cpa"][i_hi - 1] - (a["cpa"][i_lo - 1] if i_lo > 0 else 0)
            k = a["ck"][i_hi - 1] - (a["ck"][i_lo - 1] if i_lo > 0 else 0)
            return int(pa), int(k)

        pa30, k30 = window(di - 30, di)
        pa_sn, k_sn = window(_di(date(as_of.year, 1, 1)), di)
        if pa_sn == 0:  # early season: use last 365d as 'season'
            pa_sn, k_sn = window(di - 365, di)
        r30 = k30 / pa30 if pa30 else None
        rsn = k_sn / pa_sn if pa_sn else None
        if r30 is not None and rsn is not None:
            rate = config.LINEUP_BLEND_30D * r30 + config.LINEUP_BLEND_SEASON * rsn
            pa_eff = config.LINEUP_BLEND_30D * pa30 + config.LINEUP_BLEND_SEASON * pa_sn
        elif rsn is not None:
            rate, pa_eff = rsn, pa_sn
        else:
            return lg, 0.0
        sh = config.BATTER_SHRINK_PA
        return ((pa_eff * rate + sh * lg) / (pa_eff + sh), float(pa_eff))

    def lineup_k_pct(self, batter_ids: list[int], hand: str, as_of: date) -> float:
        """Tier-1/2: slot-weighted lineup K% vs hand."""
        ids = list(batter_ids)[:9]
        if not ids:
            return self.lg_k
        w = LINEUP_SLOT_WEIGHTS[: len(ids)]
        rates = np.array([self.batter_k_rate(b, hand, as_of)[0] for b in ids])
        return float(np.sum(w * rates) / np.sum(w))

    def common_lineup(self, team: str, hand: str, as_of: date) -> list[int]:
        """Tier-2: team's 9 most-used batters vs that hand over the last 7 days."""
        di = _di(as_of)
        bt = self.bat_team
        sel = bt[(bt["team"] == team) & (bt["di"] >= di - 7) & (bt["di"] < di)]
        if sel.empty:
            return []
        top = (
            sel.groupby("batter_id")["pa"].sum().sort_values(ascending=False).head(9)
        )
        return [int(b) for b in top.index]

    def team_aggregate_k_pct(self, team: str, hand: str, as_of: date) -> float:
        """Tier-3: team PA-weighted K% vs hand, trailing 30 days."""
        a = self.team_day.get((team, hand))
        if a is None:
            return self.lg_k
        di = _di(as_of)
        i_lo = np.searchsorted(a["di"], di - 30, side="left")
        i_hi = np.searchsorted(a["di"], di, side="left")
        if i_hi <= i_lo:
            return self.lg_k
        pa = a["cpa"][i_hi - 1] - (a["cpa"][i_lo - 1] if i_lo > 0 else 0)
        k = a["ck"][i_hi - 1] - (a["ck"][i_lo - 1] if i_lo > 0 else 0)
        if pa < 50:
            return self.lg_k
        sh = config.BATTER_SHRINK_PA * 9
        return float((k + sh * self.lg_k) / (pa + sh))

    def opponent_features(
        self, opp_team: str, hand: str, as_of: date,
        confirmed_ids: list[int] | None = None,
        actual_ids: list[int] | None = None,
    ) -> tuple[float, float, str]:
        """§5.3 cascade → (opp_k_pct, lineup_confidence, tier)."""
        if actual_ids:  # training: the batters actually faced
            return self.lineup_k_pct(actual_ids, hand, as_of), 1.0, "actual"
        if confirmed_ids:
            return self.lineup_k_pct(confirmed_ids, hand, as_of), 1.0, "confirmed"
        common = self.common_lineup(opp_team, hand, as_of)
        if len(common) >= 6:
            return self.lineup_k_pct(common, hand, as_of), 0.6, "common7d"
        return self.team_aggregate_k_pct(opp_team, hand, as_of), 0.3, "team_agg"

    # ------------------------------------------------------------------ context
    def run_environment(self, home: str, away: str, as_of: date) -> float:
        """Combined trailing-30d runs/game of both teams (proxy for the Vegas total)."""
        di = _di(as_of)
        total = 0.0
        for team in (home, away):
            a = self.team_runs.get(team)
            if a is None:
                total += config.LEAGUE_TOTAL_RUNS / 2
                continue
            i_lo = np.searchsorted(a["di"], di - 30, side="left")
            i_hi = np.searchsorted(a["di"], di, side="left")
            n = i_hi - i_lo
            if n < 5:
                total += config.LEAGUE_TOTAL_RUNS / 2
                continue
            runs = a["cruns"][i_hi - 1] - (a["cruns"][i_lo - 1] if i_lo > 0 else 0)
            total += runs / n
        return float(total)

    def park_features(self, venue_name: str) -> dict:
        p = park_for_venue(venue_name, self.parks)
        if not p:
            return {"park_k_factor": 1.0, "roof_type": "open"}
        return {"park_k_factor": float(p["k_factor"]) / 100.0, "roof_type": p["roof_type"]}

    def ump_factor(self, ump_name: str | None) -> float:
        return self.ump.get(ump_name or "", 1.0)
