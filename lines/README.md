# Entering K lines from your phone (~2 minutes)

K prop odds are paid-API-only, so you enter the lines you see in your book:

1. Open the **GitHub app** (or github.com) → this repo → `lines/manual_lines.csv` → pencil (edit).
2. Add one row per line, e.g.:

```
date,pitcher,book,line,over_odds,under_odds,closing
2026-06-12,Skubal,draftkings,7.5,-115,-105,0
2026-06-12,Paul Skenes,fanduel,7.5,-120,-102,0
```

3. Commit. The **rescore workflow** runs automatically (~2 min) and the site's
   Today page updates with EV / quarter-Kelly rankings for those lines.

Notes
- `pitcher`: last name is enough — it's matched to that day's probable starters.
- Odds are American (-115, +100). `closing`: set 1 if you grab the line again
  just before first pitch — that powers the CLV metric. Optional but valuable.
- Books `draftkings`, `fanduel`, `betmgm`, `caesars` get full weight in
  rankings; anything else gets 0.7× (roadmap §6.3).
- Old rows are fine to leave; duplicates are ignored.
