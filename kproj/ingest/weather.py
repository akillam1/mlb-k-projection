"""Open-Meteo weather for outdoor parks (free, non-commercial personal use).

Dome parks skip the call entirely. Retractable parks get real weather but the
feature builder attenuates it (roof may be closed; day-of roof status is not
cleanly available for free — accepted simplification).
"""
import csv
from datetime import datetime

from .. import config, util


def load_ballparks() -> list[dict]:
    with open(config.BALLPARKS_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def park_for_venue(venue_name: str, parks=None) -> dict | None:
    parks = parks or load_ballparks()
    vn = util.norm_name(venue_name or "")
    for p in parks:
        if util.norm_name(p["venue_name"]) == vn:
            return p
    # loose contains-match (venue names drift: 'Rate Field' vs 'Guaranteed Rate Field')
    for p in parks:
        pn = util.norm_name(p["venue_name"])
        if vn and (vn in pn or pn in vn):
            return p
    return None


def fetch_forecast(lat: float, lon: float) -> dict | None:
    try:
        return util.http_get(
            config.OPEN_METEO_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "hourly": "temperature_2m,wind_speed_10m,relative_humidity_2m,precipitation_probability",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "timezone": "UTC",
                "forecast_days": 3,
            },
        )
    except RuntimeError:
        return None


ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def backfill_weather(con, start_year: int, end_year: int, progress=print) -> None:
    """Historical hourly weather per outdoor park (free ERA5 archive), one call per park-year."""
    from .. import db as _db

    parks = load_ballparks()
    for p in parks:
        if p["roof_type"] == "dome":
            continue
        for yr in range(start_year, end_year + 1):
            key = f"{p['venue_name']}_{yr}"
            if _db.is_done(con, "weather_archive", key):
                continue
            games = con.execute(
                """SELECT game_pk, first_pitch_utc FROM games
                   WHERE venue_name LIKE ? AND date LIKE ? AND temp_f IS NULL""",
                (f"%{p['venue_name']}%", f"{yr}-%"),
            ).fetchall()
            if not games:
                _db.mark_done(con, "weather_archive", key, detail="no games")
                continue
            try:
                fc = util.http_get(
                    ARCHIVE_URL,
                    params={
                        "latitude": p["lat"], "longitude": p["lon"],
                        "start_date": f"{yr}-03-01", "end_date": f"{yr}-11-15",
                        "hourly": "temperature_2m,wind_speed_10m,relative_humidity_2m",
                        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
                        "timezone": "UTC",
                    },
                    timeout=120,
                )
            except RuntimeError as e:
                progress(f"[weather] {key} failed: {e}")
                continue
            hours = fc.get("hourly", {}).get("time", [])
            index = {t: i for i, t in enumerate(hours)}
            h = fc.get("hourly", {})
            n = 0
            for g in games:
                t = (g["first_pitch_utc"] or "")[:13] + ":00"
                i = index.get(t)
                if i is None:
                    continue
                con.execute(
                    "UPDATE games SET temp_f=?, wind_mph=?, humidity=? WHERE game_pk=?",
                    (h["temperature_2m"][i], h["wind_speed_10m"][i],
                     h["relative_humidity_2m"][i], g["game_pk"]),
                )
                n += 1
            _db.mark_done(con, "weather_archive", key, detail=f"updated={n}")
            con.commit()
            progress(f"[weather] {key}: {n} games")


def update_weather_for_date(con, d) -> int:
    parks = load_ballparks()
    games = con.execute(
        "SELECT game_pk, venue_name, first_pitch_utc FROM games WHERE date=?",
        (util.iso(d),),
    ).fetchall()
    n = 0
    for g in games:
        park = park_for_venue(g["venue_name"], parks)
        if not park or park["roof_type"] == "dome":
            continue
        fc = fetch_forecast(float(park["lat"]), float(park["lon"]))
        if not fc or "hourly" not in fc:
            continue
        target = (g["first_pitch_utc"] or "")[:13] + ":00"  # YYYY-MM-DDTHH:00
        hours = fc["hourly"].get("time", [])
        idx = hours.index(target) if target in hours else None
        if idx is None:
            # nearest available hour
            try:
                tgt = datetime.fromisoformat((g["first_pitch_utc"] or "").replace("Z", ""))
                idx = min(
                    range(len(hours)),
                    key=lambda i: abs((datetime.fromisoformat(hours[i]) - tgt).total_seconds()),
                )
            except (ValueError, TypeError):
                continue
        h = fc["hourly"]
        con.execute(
            "UPDATE games SET temp_f=?, wind_mph=?, humidity=?, precip_prob=? WHERE game_pk=?",
            (
                h["temperature_2m"][idx],
                h["wind_speed_10m"][idx],
                h["relative_humidity_2m"][idx],
                (h.get("precipitation_probability") or [None] * len(hours))[idx],
                g["game_pk"],
            ),
        )
        n += 1
    return n
