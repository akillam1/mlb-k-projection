"""Best-effort X/Twitter scraping via public Nitter mirrors + manual CSV.

X requires login and a paid API for reads, so this rotates through public
mirrors and treats failure as NORMAL: every attempt is logged to source
health and the page shows exactly how stale each capper's feed is.
lines/capper_picks.csv is the always-works manual path.
"""
import csv
import random
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

from .. import config
from . import parse, store

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _fetch_rss(handle: str) -> tuple[list, str] | tuple[None, str]:
    """Try each mirror until one returns a real RSS feed. Returns (items, mirror)."""
    mirrors = config.SIGNALS_MIRRORS[:]
    random.shuffle(mirrors)              # spread load; no mirror is 'primary'
    last = "no mirrors configured"
    for m in mirrors:
        try:
            r = requests.get(f"{m}/{handle}/rss", timeout=15,
                             headers={"User-Agent": UA, "Accept": "application/rss+xml,text/xml"})
        except requests.RequestException as e:
            last = f"{m}: {type(e).__name__}"
            continue
        if r.status_code != 200 or "<rss" not in r.text[:2000]:
            last = f"{m}: HTTP {r.status_code}"
            continue
        try:
            root = ET.fromstring(r.content)
            items = root.findall("./channel/item")
        except ET.ParseError:
            last = f"{m}: bad XML"
            continue
        if items:
            return items, m
        last = f"{m}: empty feed"
    return None, last


def _post_rows(handle: str, items: list) -> list[dict]:
    now = store.utcnow()
    rows = []
    for it in items:
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate") or ""
        m = re.search(r"/status/(\d+)", link)
        post_id = m.group(1) if m else link or title[:40]
        try:
            posted = parsedate_to_datetime(pub).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (TypeError, ValueError):
            posted = now
        rows.append({"capper": handle, "post_id": post_id, "posted_at": posted,
                     "fetched_at": now, "text": title,
                     "url": f"https://x.com/{handle}/status/{post_id}" if m else link})
    return rows


def scrape(con, probables: list) -> dict:
    """One pass over all cappers. Returns {handle: n_new_posts|-1}."""
    results = {}
    for handle in config.SIGNALS_CAPPERS:
        items, note = _fetch_rss(handle)
        if items is None:
            store.source_status(con, f"x:{handle}", ok=False, note=note)
            results[handle] = -1
            continue
        new = 0
        for row in _post_rows(handle, items):
            cur = con.execute(
                "INSERT OR IGNORE INTO capper_posts (capper, post_id, posted_at, fetched_at, text, url) "
                "VALUES (:capper, :post_id, :posted_at, :fetched_at, :text, :url)", row)
            if cur.rowcount:
                new += 1
                if row["text"].startswith("RT by"):
                    continue                       # keep retweets in feed, never as picks
                for pk in parse.extract_picks(row["text"], probables):
                    con.execute(
                        """INSERT OR IGNORE INTO capper_picks
                           (capper, date, pitcher_raw, pitcher_id, side, line, odds, book,
                            source, post_id, entered_at)
                           VALUES (?,?,?,?,?,?,?,?,'auto',?,?)""",
                        (handle, pk["date"], pk["pitcher_raw"], pk["pitcher_id"], pk["side"],
                         pk["line"], pk["odds"], pk["book"], row["post_id"], store.utcnow()))
        store.source_status(con, f"x:{handle}", ok=True, note=f"via {note if items else ''}" or "")
        results[handle] = new
        print(f"[signals] @{handle}: {new} new posts")
    fails = [h for h, n in results.items() if n < 0]
    if fails:
        print(f"[signals] mirrors unreachable for: {', '.join(fails)}")
    return results


def ingest_manual_csv(con, probables: list) -> int:
    """lines/capper_picks.csv: capper,date,pitcher,side,line,odds,book
    (odds/book optional; date optional = auto from probables). Phone-editable."""
    path = config.CAPPER_PICKS_CSV
    if not path.exists():
        return 0
    n = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if (row.get("capper") or "").lstrip().startswith("#"):
                continue                              # comment row
            pitcher = (row.get("pitcher") or "").strip()
            side = (row.get("side") or "").strip().lower()
            if not pitcher or side not in ("over", "under") or not (row.get("line") or "").strip():
                continue
            hit = parse.resolve_pitcher(pitcher, probables)
            date = (row.get("date") or "").strip() or (hit[1] if hit else None)
            if date is None:
                print(f"[signals] capper_picks.csv: no game date for '{pitcher}' — skipped")
                continue
            odds = (row.get("odds") or "").strip()
            cur = con.execute(
                """INSERT OR IGNORE INTO capper_picks
                   (capper, date, pitcher_raw, pitcher_id, side, line, odds, book,
                    source, post_id, entered_at)
                   VALUES (?,?,?,?,?,?,?,?,'manual',NULL,?)""",
                ((row.get("capper") or "manual").strip(), date, pitcher,
                 hit[0] if hit else None, side, float(row["line"]),
                 int(odds) if re.fullmatch(r"[+-]?\d{3,4}", odds) else None,
                 parse.BOOKS.get((row.get("book") or "").strip().lower()), store.utcnow()))
            n += cur.rowcount
    if n:
        print(f"[signals] manual capper picks ingested: {n}")
    return n
