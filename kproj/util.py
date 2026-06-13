"""Shared helpers: odds math, dates, names, HTTP."""
import re
import time
import unicodedata
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from . import config

ET = ZoneInfo(config.ET_ZONE)


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
