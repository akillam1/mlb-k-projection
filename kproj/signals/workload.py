"""Best-effort short-leash / pitch-limit news scrape.

Item 3 of the short-leash design in POSTSEASON_AND_SHORTLEASH.md. Same shape
as social.py's capper scrape on purpose: public Nitter mirrors (best-effort,
logged to source health, never a hard dependency) plus a phone-editable
manual CSV fallback. What's different is the extraction: capper picks are a
strict numeric pattern (parse.py's regex), but a short-leash situation is
usually reported in soft, qualitative language ("we have to build him back
up... in a mindful way" — the actual Misiorowski quote that prompted this),
which a numeric regex can't catch. The properly evidence-based answer here
would be an LLM extracting structured flags from free text — but that costs
money per call, and this project runs under a zero-dollar rule (see
PARKING_LOT.md), so it's not something to wire in silently inside an
unattended daily workflow. This ships the honest, free version instead: a
conservative keyword/phrase match (config.WORKLOAD_KEYWORDS), which will
under-catch novel phrasing but will never fabricate a flag from nothing.

Every flag is written UNCONFIRMED. Corroboration against K-line movement
(the "trust but verify" step) happens on the read side, in
kproj/export/site_export.py — deliberately NOT here, because this module
must never touch kproj.db (see kproj/cli.py's cmd_signals docstring); line
data lives there, not in signals.db.
"""
import re
import unicodedata
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .. import config
from . import parse, social, store


def _norm_for_match(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def extract_workload_flags(text: str, probables: list) -> list[dict]:
    """All keyword-phrase matches in one post that resolve to a probable
    starter. Conservative by construction: the phrase must be one of
    config.WORKLOAD_KEYWORDS verbatim (case/accent-insensitive) AND the named
    player must resolve uniquely via parse.resolve_pitcher — no partial
    credit for a phrase alone with no attributable pitcher, or a pitcher name
    with no workload language nearby."""
    norm = _norm_for_match(text)
    out = []
    for phrase, (confidence, flag_type) in config.WORKLOAD_KEYWORDS.items():
        idx = norm.find(phrase)
        if idx < 0:
            continue
        # crude but effective: only credit this phrase to a name that shares
        # the post with it — for a short scraped snippet (one tweet), that's
        # the whole text, so just try resolving every capitalized run in it.
        names = re.findall(r"[A-Z][a-zA-Z'.À-ſ-]+(?:\s+[A-Z][a-zA-Z'.À-ſ-]+){0,2}", text)
        for name in names:
            hit = parse.resolve_pitcher(name, probables)
            if hit is None:
                continue
            pid, date, game_pk = hit
            start = max(0, idx - 60)
            out.append({
                "pitcher_raw": name.strip(), "pitcher_id": pid, "date": date,
                "flag_type": flag_type, "confidence": confidence,
                "phrase": phrase, "snippet": text[start:idx + len(phrase) + 60].strip(),
            })
            break  # one name per phrase match is enough; avoid duplicate flags per post
    return out


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
        rows.append({"post_id": post_id, "text": title,
                     "url": f"https://x.com/{handle}/status/{post_id}" if m else link})
    return rows


def _store_flags(con, flags: list[dict], source: str, source_url: str, post_id: str | None) -> int:
    n = 0
    for f in flags:
        cur = con.execute(
            """INSERT OR IGNORE INTO workload_flags
               (date, pitcher_raw, pitcher_id, flag_type, confidence, phrase, snippet,
                source, source_url, post_id, scraped_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (f["date"], f["pitcher_raw"], f["pitcher_id"], f["flag_type"], f["confidence"],
             f["phrase"], f["snippet"], source, source_url, post_id, store.utcnow()),
        )
        n += cur.rowcount
    return n


def scrape(con, probables: list) -> dict:
    """One pass over configured beat-reporter handles. Returns {handle: n_new_flags|-1}.
    Empty config.WORKLOAD_NEWS_HANDLES (the default — see config.py comment on
    why no handles are seeded) means this is a no-op, not a failure."""
    results = {}
    for handle in config.WORKLOAD_NEWS_HANDLES:
        items, note = social.fetch_rss(handle)
        if items is None:
            store.source_status(con, f"workload:{handle}", ok=False, note=note)
            results[handle] = -1
            continue
        new = 0
        for row in _post_rows(handle, items):
            flags = extract_workload_flags(row["text"], probables)
            new += _store_flags(con, flags, f"auto:{handle}", row["url"], row["post_id"])
        store.source_status(con, f"workload:{handle}", ok=True, note=f"via {note}")
        results[handle] = new
        print(f"[workload] @{handle}: {new} new flags")
    fails = [h for h, n in results.items() if n < 0]
    if fails:
        print(f"[workload] mirrors unreachable for: {', '.join(fails)}")
    return results


def ingest_manual_csv(con, probables: list) -> int:
    """lines/workload_notes.csv: pitcher,date,note[,flag_type][,confidence]
    Phone-editable, same spirit as capper_picks.csv — the reliable fallback
    when scraping a beat reporter isn't set up or a mirror is down. `note`
    is free text; it's matched against config.WORKLOAD_KEYWORDS same as a
    scraped post, so writing "short leash tonight per beat writer" works
    without needing to know the internal flag_type vocabulary."""
    import csv

    path = config.WORKLOAD_NOTES_CSV
    if not path.exists():
        return 0
    n = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if (row.get("pitcher") or "").lstrip().startswith("#"):
                continue
            pitcher = (row.get("pitcher") or "").strip()
            note = (row.get("note") or "").strip()
            if not pitcher or not note:
                continue
            hit = parse.resolve_pitcher(pitcher, probables)
            if hit is None:
                print(f"[workload] workload_notes.csv: '{pitcher}' didn't resolve to a probable — skipped")
                continue
            pid, date, game_pk = hit
            date = (row.get("date") or "").strip() or date
            flags = extract_workload_flags(f"{pitcher} {note}", probables)
            if not flags:
                # Note didn't match a known phrase — file it anyway at low
                # confidence rather than silently dropping what Robin typed.
                flags = [{
                    "pitcher_raw": pitcher, "pitcher_id": pid, "date": date,
                    "flag_type": "other", "confidence": 0.4,
                    "phrase": "manual note", "snippet": note,
                }]
            n += _store_flags(con, flags, "manual", None, None)
    if n:
        print(f"[workload] manual workload notes ingested: {n}")
    return n


def flags_for(con, date_s: str, pitcher_id: int) -> dict | None:
    """The single highest-confidence flag for this pitcher/date, or None.
    Read-only lookup used by kproj/export/site_export.py — kproj proper is
    allowed to read signals.db (best-effort, never the reverse); see the
    module docstring for why the boundary only runs one direction."""
    row = con.execute(
        """SELECT flag_type, confidence, phrase, snippet, source, source_url, scraped_at
           FROM workload_flags WHERE date=? AND pitcher_id=?
           ORDER BY confidence DESC, scraped_at DESC LIMIT 1""",
        (date_s, pitcher_id),
    ).fetchone()
    return dict(row) if row else None
