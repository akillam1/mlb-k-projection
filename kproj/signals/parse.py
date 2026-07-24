"""Extract K-prop picks from capper tweet text.

Deliberately conservative: a pick is only recorded when the named player
resolves to a probable starter on today's or tomorrow's slate. Everything
else stays in the raw feed for eyeballing.
"""
import re
import unicodedata

BOOKS = {
    "dk": "draftkings", "draftkings": "draftkings",
    "fd": "fanduel", "fanduel": "fanduel",
    "mgm": "betmgm", "betmgm": "betmgm",
    "czr": "caesars", "caesars": "caesars",
    "br": "betrivers", "betrivers": "betrivers",
    "espn": "espnbet", "espnbet": "espnbet",
    "fanatics": "fanatics", "bol": "betonlineag", "bovada": "bovada",
}

# Words that mean the number is NOT a strikeout line
_NOT_K = r"outs?|hits?|tb|bases?|bb|walks?|er|runs?|ip|innings?|pitches"

NAME = r"(?P<name>[A-Z][a-zA-Z'.À-ſ-]+(?:\s+[A-Z][a-zA-Z'.À-ſ-]+){0,2})"

# "Davis Martin Over 4.5 Strikeouts", "Skubal Under 8.5 Ks (-134)"
R_WORD = re.compile(
    NAME + r"\s+(?P<side>(?i:over|under))\s+(?P<line>\d{1,2}(?:\.5)?)\s*"
    r"(?P<kword>(?i:k'?s?|strikeouts?|so)\b)?"
    r"(?!\s*(?:" + _NOT_K + r"))"
    r"(?:[^\S\n]*\(?(?P<odds>[+-]\d{3})\)?)?"
    r"(?:[^\S\n]+(?P<book>[A-Za-z]{2,9})\b)?"
)

# "Skubal u8.5 -134 DK", "Brown o7.5"
R_COMPACT = re.compile(
    NAME + r"\s+(?P<side>[ouOU])\s?(?P<line>\d{1,2}(?:\.5)?)\b"
    r"(?!\s*(?:" + _NOT_K + r"))"
    r"(?:[^\S\n]*\(?(?P<odds>[+-]\d{3})\)?)?"
    r"(?:[^\S\n]+(?P<book>[A-Za-z]{2,9})\b)?"
)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z ]", "", s.lower()).strip()


def resolve_pitcher(name: str, probables: list) -> tuple | None:
    """probables: [(pitcher_id, pitcher_name, date, game_pk)] for today+tomorrow.
    Returns (pitcher_id, date, game_pk) or None. Match order: full name,
    unique last name, first-initial + last name."""
    n = _norm(name)
    if not n:
        return None
    full = [p for p in probables if _norm(p[1]) == n]
    if len({p[0] for p in full}) == 1:
        p = full[0]
        return p[0], p[2], p[3]
    last = n.split()[-1]
    by_last = [p for p in probables if _norm(p[1]).split()[-1] == last]
    if len({p[0] for p in by_last}) == 1:
        p = by_last[0]
        return p[0], p[2], p[3]
    if len(n.split()) >= 2:
        fi = n.split()[0][0]
        cand = [p for p in by_last if _norm(p[1])[0] == fi]
        if len({p[0] for p in cand}) == 1:
            p = cand[0]
            return p[0], p[2], p[3]
    return None


def extract_picks(text: str, probables: list) -> list[dict]:
    """All K picks found in one tweet. Compact 'u8.5' form requires the name
    to resolve; the word form additionally accepts an explicit K-word."""
    txt = re.sub(r"https?://\S+", " ", text)
    out, seen = [], set()
    for rex, needs_kword in ((R_WORD, False), (R_COMPACT, False)):
        for m in rex.finditer(txt):
            side = m.group("side").lower()
            side = "over" if side.startswith("o") else "under"
            hit = resolve_pitcher(m.group("name"), probables)
            if hit is None:
                continue
            if rex is R_WORD and not m.group("kword"):
                # No explicit K-word: only trust it if the tweet talks strikeouts
                if not re.search(r"strikeout|whiff|\bk's\b|\bks\b|k rate|k target", txt, re.I):
                    continue
            pid, date, game_pk = hit
            line = float(m.group("line"))
            if not 1.5 <= line <= 13.5:
                continue
            key = (pid, side, line)
            if key in seen:
                continue
            seen.add(key)
            book = (m.group("book") or "").lower()
            out.append({
                "pitcher_raw": m.group("name").strip(),
                "pitcher_id": pid, "date": date, "game_pk": game_pk,
                "side": side, "line": line,
                "odds": int(m.group("odds")) if m.group("odds") else None,
                "book": BOOKS.get(book),
            })
    return out
