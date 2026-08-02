"""Shared helpers: odds math, dates, names, HTTP."""
import re
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from zoneinfo import ZoneInfo

import requests

from . import config

ET = ZoneInfo(config.ET_ZONE)
BOARD_TZ = ZoneInfo(config.BOARD_ZONE)


# ---------- HTTP ----------
def http_get(url, params=None, retries=3, timeout=None, as_json=True):
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(
                url,
                params=params,
                timeout=timeout or config.REQUEST_TIMEOUT,
                headers={"User-Agent": config.USER_AGENT},
            )
            if r.status_code == 200:
                return r.json() if as_json else r
            last_err = f"HTTP {r.status_code}"
            if r.status_code in (400, 401, 403, 404, 422):
                break  # not retryable
        except requests.RequestException as e:  # noqa: PERF203
            last_err = str(e)
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {last_err}")


# ---------- Dates ----------
def today_et() -> date:
    return datetime.now(ET).date()


def yesterday_et() -> date:
    return today_et() - timedelta(days=1)


def board_date(now: datetime | None = None) -> date:
    """The slate the site should be showing right now.

    Rolls to the next day at config.BOARD_ROLLOVER_HOUR, local to
    config.BOARD_ZONE (Arizona: UTC-7 all year). Explicit on purpose — the old
    behaviour depended on the nightly run landing after midnight ET, which
    broke the moment the schedule moved earlier. Late runs are safe: at 11 PM
    AZ the hour is still past the rollover, and after local midnight the date
    itself has advanced, so both paths agree.
    """
    n = (now or datetime.now(BOARD_TZ)).astimezone(BOARD_TZ)
    d = n.date()
    return d + timedelta(days=1) if n.hour >= config.BOARD_ROLLOVER_HOUR else d


def board_prev_date(now: datetime | None = None) -> date:
    """The slate to settle/ingest finals for: the one before the live board."""
    return board_date(now) - timedelta(days=1)


def window_open(board_day: date, hour_utc: int, now: datetime | None = None) -> bool:
    """Has the UTC fetch window for this board day opened?

    Anchored to the board day, not to the bare UTC hour. The board rolls at
    03:00 UTC, so a run that lands at 00:30 UTC is still working the previous
    board day — whose window opened at 15:00 UTC the morning before. Comparing
    hours alone ("is 0 >= 15?") answered no and silently skipped the fetch.
    """
    opens = datetime.combine(board_day, dtime(hour_utc), tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) >= opens


def iso(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def season_of(d: date) -> int:
    return d.year


def in_season_window(d: date) -> bool:
    """Rough MLB regular-season window (spring training excluded by hfGT=R filter anyway)."""
    return 3 <= d.month <= 11


# ---------- Odds math (roadmap §6) ----------
def american_to_decimal(odds: int) -> float:
    o = int(odds)
    return 1 + (o / 100.0) if o > 0 else 1 + (100.0 / abs(o))


def american_to_prob(odds: int) -> float:
    o = int(odds)
    return 100.0 / (o + 100.0) if o > 0 else abs(o) / (abs(o) + 100.0)


def devig_two_way(p_a_raw: float, p_b_raw: float) -> tuple[float, float]:
    """Multiplicative de-vig: normalize the two raw implied probabilities."""
    s = p_a_raw + p_b_raw
    if s <= 0:
        return 0.5, 0.5
    return p_a_raw / s, p_b_raw / s


# ---------- Names ----------
def norm_name(name: str) -> str:
    """Lowercase, strip accents/punctuation for fuzzy pitcher matching."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z\s]", "", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def name_matches(entered: str, official: str) -> bool:
    """'skubal' or 'tarik skubal' should match 'Tarik Skubal'."""
    e, o = norm_name(entered), norm_name(official)
    if not e or not o:
        return False
    if e == o:
        return True
    o_parts = o.split()
    e_parts = e.split()
    if len(e_parts) == 1:
        return e_parts[0] == o_parts[-1]  # last name exact
    return e_parts[0][0] == o_parts[0][0] and e_parts[-1] == o_parts[-1]


def ip_to_outs(ip_str) -> int:
    """'5.2' → 17 outs."""
    if ip_str in (None, ""):
        return 0
    s = str(ip_str)
    whole, _, frac = s.partition(".")
    return int(whole or 0) * 3 + int(frac or 0)
