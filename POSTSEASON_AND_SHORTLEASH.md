# Postseason mode & short-leash detection — assessment

Written Sept 28, 2026, in response to: "now that the regular season is over...
do research and review playoff K models... understand if we need a news scrape
pregame... start with mandatory fix. After that, do research about how to
properly incorporate a short leash or a news scrape... i want this to be based
on evidence/stats... building a postseason mode would be ideal."

## 1. Mandatory fix — DONE, pushed Sept 27, 2026

The schedule fetch in `kproj/ingest/mlb_api.py` was hard-filtered to
`gameType == 'R'`. That meant the board didn't project the postseason badly —
it saw **zero postseason games at all**, so the board would have gone
completely dark for the rest of October. Fixed:

- `games.game_type` column added (migrated via `_add_column_if_missing`, so it
  applies to the live DB, not just fresh ones).
- Ingest now allows `F`/`D`/`L`/`W` (Wild Card / Division / Championship /
  World Series) alongside `R`, togglable via `KPROJ_INCLUDE_POSTSEASON` (env
  var, defaults on).
- Training (`training_frame()` in `kproj/features/build.py`) still filters to
  `game_type='R' OR game_type IS NULL` — the flag controls what the *board
  shows*, not what the *model trains on*. See §3 for why postseason starts
  shouldn't be blended into the regular-season model yet.
- Regression-tested (migration idempotency, schedule-filter allowlist with/
  without the flag, training-query exclusion) and verified live on
  `raw.githubusercontent.com` post-push.

## 2. Postseason workload research (recap from prior session)

Cross-checked across FanGraphs, SABR, and October Pitching Paradox: postseason
starters throw **~16% fewer innings** (≈4.35 IP vs ≈5.19 IP regular season,
2025 data) but their **per-inning performance doesn't decline** (FIP ≈3.64,
same as regular season). This is a *quantity* effect — fully-rested,
high-leverage bullpens let managers pull starters earlier — not a *quality*
decline. That matters for modeling: it means the right postseason adjustment
is to the expected **batters faced / innings**, not to the pitcher's
underlying stuff or K-rate.

## 3. Short-leash / TTOP evidence

Two things to keep separate, because the evidence points in different
directions for each:

**Times-through-the-order penalty (TTOP)** — the general in-game decline as a
starter faces a lineup multiple times. Per the Bayesian TTOP analysis
([Brill et al., arXiv:2210.06724](https://arxiv.org/abs/2210.06724);
[Baseball Prospectus summary](https://www.baseballprospectus.com/news/article/22156/baseball-proguestus-everything-you-always-wanted-to-know-about-the-times-through-the-order-penalty/)):
wOBA rises gradually (.340 → .350 → .359 across the first three trips, then
plateaus), it's driven by **batter familiarity, not pitcher fatigue** (pitch
count has *weak* correlation with the penalty — hitters improve just from
seeing more pitches in their first look, independent of how gassed the pitcher
is), and the effect is **continuous**, not a threshold you cross. This is
already partially absorbed into our model implicitly (recency-weighted
`k_pct_ewma`, `bf_avg3`), but there's no explicit "3rd-time-through-order"
signal. Given the evidence says this is continuous and fatigue isn't the
mechanism, a hard rule ("cut K rate by X% past the 2nd time through") would be
the wrong shape — a smooth adjustment or an interaction feature would fit the
data better if we ever add one, but this is a secondary priority next to short
leash below.

**Short leash** (a specific start where the manager has *decided* in advance
to limit a pitcher — rookie workload management, return from injury, a
piggyback game) is a different phenomenon: it's not about in-game batter
familiarity, it's a **pre-set batters-faced ceiling** that's often knowable
before first pitch. The best evidence-based precedent I found for how to model
this is a direct competitor: **KSplit's methodology**
([ksplitanalytics.com/methodology](https://ksplitanalytics.com/methodology))
explicitly models two batters-faced "leash profiles" — a normal leash centered
around 23 BF, and a short leash centered around 17 BF — and widens the whole
strikeout distribution for a start where the BF outcome is uncertain, rather
than trying to predict a single point value. That's the right shape: **short
leash should widen and lower the batters-faced (and therefore K) distribution,
not just haircut the point estimate**, and it should come from a workload
model, not a stuff/quality adjustment. It also matches the TTOP finding: since
the penalty isn't fatigue-driven, a manager pulling a starter early isn't
"protecting him from declining stuff," it's just capping opportunities.

Real-world example that prompted this ask: the Brewers' handling of Jacob
Misiorowski this season was **not announced as a hard pitch-count number** —
manager Pat Murphy's public language was qualitative ("we have to build him
back up... in a mindful way") days before starts, not a same-day "75-pitch
cap" press release. That's an important constraint on the news-scrape design
below: we can't expect to scrape a clean numeric pitch limit most of the time.
The signal will usually be soft ("on a short leash," "limited," "bullpen
game," "stretched out") and needs to be treated as a probability/flag, not a
hard number.

## 4. Recommended design: workload feature + news scrape

**Feature side (model):** add a workload-adjustment layer that sits alongside
the existing `bf_avg3`/`days_rest` features rather than replacing them:

- A team-level "quick hook" prior computed from our *own* ingested data —
  we already store every relief appearance in `pitcher_game_logs`, so a
  team's recent average starter BF/IP-before-first-reliever is derivable
  in-house, no new dependency needed. (InsidethePen's bullpen tracker
  ([insidethepen.com/bullpen-usage.html](https://insidethepen.com/bullpen-usage.html))
  is a decent secondary/sanity-check source — daily-updated, scrapable HTML
  tables of bullpen rest state — but shouldn't be a hard dependency since we
  can compute the core signal ourselves.)
- A `short_leash_flag` (probability, not boolean) sourced from the news
  scrape below, which nudges the BF distribution the way KSplit's two-profile
  model does — down and wider, never a hard override.

**News-scrape side:** extend the existing (currently dormant) `kproj/signals/`
module rather than building new infrastructure — it already has the right
shape for this: best-effort scraping with graceful degradation, per-source
health logging (`store.source_status()`), and an isolated DB
(`config.SIGNALS_DB`) so a scrape failure can never take down the main
pipeline. Concretely:

1. New signals sub-module (e.g. `kproj/signals/workload_news.py`) that scrapes
   team beat-reporter accounts (same best-effort Nitter-mirror pattern as
   `social.py`, same fragility caveat already logged in `PARKING_LOT.md` §1b)
   plus official MLB.com pregame notes, looking for the qualitative language
   patterns above ("short leash," "pitch limit," "innings limit," "bullpen
   game," "stretched out," "on a count").
2. LLM-based structured extraction of free text into
   `{pitcher_id, game_pk, flag_type, confidence, source_url, scraped_at}` —
   this is the established pattern for this exact problem (found in prior
   research, e.g. the JonJonz7/sports-betting-analytics repo's injury-NLP
   approach); regex is too brittle for "he might be on a short count tonight"
   phrasing.
3. **Trust but verify, concretely**: cross-check every flag against that
   game's K-line movement, which we already capture (`k_line.open`/`.move`).
   If the line has moved down meaningfully since the flag's timestamp →
   corroborated, apply the full adjustment. If the flag exists but the line
   hasn't moved → mark UNCONFIRMED, apply a damped adjustment, and surface it
   for manual review (a single scraped tweet is not a source to fully trust).
   If the line moves with no scraped flag → surface as a market-only signal
   worth a manual look, don't silently ignore it. This is the same
   corroboration discipline already used for the FiveThirtyEight source check
   this session (discarded a dead/redirected link rather than citing it).

## 5. Postseason mode

Built on top of the mandatory fix's `game_type` plumbing:

- Because we have **zero real postseason training rows**, don't try to train
  a postseason-specific model yet — one or two postseasons of data isn't
  enough, and per §2 the effect is well-characterized already (fewer BF, same
  per-BF quality). Instead, apply a **stopgap multiplicative adjustment** to
  the expected batters-faced input at inference time only, when
  `game["game_type"] != "R"` — derived directly from the measured ~16% IP
  reduction (FanGraphs 4.35 vs 5.19 IP), not a guess.
- Tag postseason predictions distinctly in `projection_results` (game_type is
  already exported) so postseason calibration can be tracked separately from
  regular-season MAE/Poisson deviance — don't let a handful of October starts
  quietly drag down the metric we use to judge the regular-season model.
- Revisit properly (real postseason-specific training) once enough postseason
  starts accumulate across multiple Octobers to clear `MIN_TRAIN_ROWS`-style
  thresholds for that subset specifically.

## 6. Suggested build order

1. In-house team "quick hook" prior from existing `pitcher_game_logs` data
   (no new dependency, immediately available).
2. Postseason BF multiplier (stopgap, inference-time only) — cheap, evidence-
   backed, ready for this postseason.
3. `workload_news.py` scrape + LLM extraction + line-movement corroboration —
   the highest-effort piece, and the one with the least reliable upside
   (soft/qualitative signals, fragile scraping), so it should come last and
   ship as a best-effort *flag on the board*, not a silent model input, until
   its precision is proven out over a season.
