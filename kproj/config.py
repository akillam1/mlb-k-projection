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

# --- The Odds API (free tier: game lines only; K props are PARKED — see PARKING_LOT.md)
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")          # empty = skip odds ingestion
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ODDS_MONTHLY_BUDGET = 500                                   # free tier credits/month
ODDS_BUDGET_FLOOR = 60                                      # stop calling below this remaining

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
BOOK_WEIGHT_MAJOR = 1.0
BOOK_WEIGHT_OTHER = 0.7
KELLY_FRACTION = 0.25              # quarter-Kelly
MIN_EV_DISPLAY = 0.0               # 'no negative EV' rule (§6.4)

# --- Training
TRAIN_AUGMENT_DEGRADED_LINEUP = 0.25   # fraction of rows re-featured with tier-2/3 lineups
MIN_TRAIN_ROWS = 1500
SEASON_START_MONTH = 3

ET_ZONE = "America/New_York"
