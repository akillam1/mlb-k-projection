"""Central configuration. Everything overridable via environment variables."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("KPROJ_DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.environ.get("KPROJ_DB", DATA_DIR / "kproj.db"))
MODELS_DIR = Path(os.environ.get("KPROJ_MODELS_DIR", ROOT / "models"))
SITE_DATA_DIR = Path(os.environ.get("KPROJ_SITE_DATA", ROOT / "docs" / "data"))
LINES_CSV = Path(os.environ.get("KPROJ_LINES_CSV", ROOT / "lines" / "manual_lines.csv"))
BALLPARKS_CSV = DATA_DIR / "ballparks.csv"

# --- The Odds API (free tier covers game lines AND current player props;
#     only *historical* endpoints require a paid plan)
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")          # empty = skip odds ingestion
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ODDS_MONTHLY_BUDGET = 500                                   # free tier credits/month
ODDS_BUDGET_FLOOR = 60                                      # stop calling below this remaining
ODDS_PROPS_MARKET = "pitcher_strikeouts"
# Credit budget: props cost 1/event (~14 games/day ≈ 430/mo); game lines cost 2/call.
# Both fetch once daily on the MORNING run (17:10 UTC = 10:10 AM AZ), gated by
# UTC hour (auto mode). Windows are 2h wide to survive GitHub cron delays; the
# nightly 04:10 UTC model refresh falls outside them, so it makes no odds calls.
ODDS_MODE = os.environ.get("KPROJ_ODDS_MODE", "auto")       # auto|both|gamelines|props|off
ODDS_GAMELINE_HOURS_UTC = {int(h) for h in os.environ.get("KPROJ_GAMELINE_HOURS_UTC", "17,18").split(",")}
ODDS_PROPS_HOURS_UTC = {int(h) for h in os.environ.get("KPROJ_PROPS_HOURS_UTC", "17,18").split(",")}

# --- Open-Meteo (free, non-commercial personal use)
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# --- MLB Stats API / Baseball Savant (free)
MLB_API = "https://statsapi.mlb.com/api/v1"
SAVANT_CSV = "https://baseballsavant.mlb.com/statcast_search/csv"
REQUEST_TIMEOUT = 60
SAVANT_CHUNK_DAYS = 5
USER_AGENT = "kproj-personal-hobby/0.1"

# --- Modeling constants (roadmap §5)
EWMA_HALF_LIFE_STARTS = 5          # recency half-life on pitcher's own starts
STABILIZATION_BF = 70              # Tango: weight_current = BF / (BF + 70)
PRIOR_REGRESSION_BF = 200          # Marcel-style prior regression toward league mean
LINEUP_BLEND_30D = 0.6             # opponent K%: trailing 30d weight
LINEUP_BLEND_SEASON = 0.4
BATTER_MIN_PA = 30                 # floor before a batter's own rate is trusted
LEAGUE_K_PCT = 0.222               # fallbacks; refreshed from data when available
LEAGUE_BF_PER_START = 22.5
LEAGUE_TOTAL_RUNS = 8.6
LEAGUE_CSW = 0.290
LEAGUE_SWSTR = 0.112
LEAGUE_KBB_PCT = 0.135
BATTER_SHRINK_PA = 25              # shrink batter K% toward league below this PA
DOME_TEMP_F = 72.0

QUANTILES = [0.10, 0.25, 0.50, 0.75, 0.90]
K_SUPPORT_MAX = 18                 # CDF support upper bound for P(over) math

# --- Edge scoring (roadmap §6)
MAJOR_BOOKS = {"draftkings", "fanduel", "betmgm", "caesars"}
# Canonical book for validation/performance: one row per pick, not one per book.
# DraftKings first (most consistent K-prop coverage on The Odds API); if it has
# no line for a pitcher, fall back down the chain so the pick isn't dropped.
# All books are still ingested and settled — this only picks which single
# line the Performance/Signals pages score and display.
PREFERRED_BOOKS = [b for b in os.environ.get(
    "KPROJ_PREFERRED_BOOKS",
    "draftkings,fanduel,betmgm,caesars,betrivers,bovada,betonlineag",
).split(",") if b]
BOOK_WEIGHT_MAJOR = 1.0
BOOK_WEIGHT_OTHER = 0.7
KELLY_FRACTION = 0.25              # quarter-Kelly
MIN_EV_DISPLAY = 0.0               # 'no negative EV' rule (§6.4)

# --- Training
TRAIN_AUGMENT_DEGRADED_LINEUP = 0.25   # fraction of rows re-featured with tier-2/3 lineups
MIN_TRAIN_ROWS = 1500
SEASON_START_MONTH = 3

ET_ZONE = "America/New_York"

# --- Signals page (validation vs cappers / market / FanGraphs) ---
# X/Twitter capper accounts tracked on the Signals page. Scraping X itself
# requires login + paid API, so this is BEST-EFFORT via public Nitter mirrors
# (rotates through SIGNALS_MIRRORS until one answers). Mirrors die often;
# lines/capper_picks.csv is the reliable manual fallback (phone-editable).
SIGNALS_DB = Path(os.environ.get("KPROJ_SIGNALS_DB", DATA_DIR / "signals.db"))
CAPPER_PICKS_CSV = Path(os.environ.get("KPROJ_CAPPER_CSV", ROOT / "lines" / "capper_picks.csv"))
SIGNALS_CAPPERS = {
    # handle (case as used on x.com) -> short display name
    "KSplitAnalytics": "KSplit",
    "WiningPlaybook": "WinningPlaybook",
    "HausOfPicks": "Haus",
    "IIPatll": "Pat",
    "AlexCaruso": "AlexCaruso",
}
SIGNALS_MIRRORS = [m for m in os.environ.get(
    "KPROJ_NITTER_MIRRORS",
    "https://xcancel.com,https://nitter.net,https://nitter.poast.org,"
    "https://lightbrd.com,https://nitter.privacyredirect.com,https://nitter.tiekoetter.com",
).split(",") if m]
SIGNALS_FEED_POSTS = 25              # recent raw posts kept in validation.json
SIGNALS_EDGE_GO = 0.05               # |prob edge| needed for a GO pick window
SIGNALS_LINE_STALE_H = 6.0           # K line older than this = CAUTION

# Targeted K-prop refresh (line movement): re-pull props near first pitch for
# the top-N games by model edge only (1 credit/game; guarded by budget floor).
# NOTE: with the two-run schedule there is no scheduled run in this window —
# it fires only on manual "Run workflow" taps during 22-23 UTC (3-4 PM AZ).
ODDS_PROPS_REFRESH_HOURS_UTC = {int(h) for h in os.environ.get("KPROJ_PROPS_REFRESH_HOURS_UTC", "22,23").split(",")}
ODDS_PROPS_REFRESH_TOP_N = int(os.environ.get("KPROJ_PROPS_REFRESH_TOP_N", "3"))

# FanGraphs Depth Charts rest-of-season projections (free JSON; daily pull).
FG_PROJ_URL = "https://www.fangraphs.com/api/projections"
FG_PROJ_TYPE = "rfangraphsdc"
