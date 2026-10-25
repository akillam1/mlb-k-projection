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

1. **DONE, pushed Sept 28, 2026.** In-house team "quick hook" prior
   (`team_leash_bf` in `kproj/features/store.py`) from existing
   `pitcher_game_logs` data — no new dependency. Verified against synthetic
   short-/long-leash team histories. Wiring this in also surfaced a real
   deploy-ordering bug (retrain is weekly, projection is daily, so adding any
   feature would crash every projection against the still-active older model
   until the next retrain caught up) — fixed by having `load_active()` return
   each model's own trained feature list and having `predict.py` build its
   vector from that instead of from live `FEATURE_COLUMNS`.
2. **DONE, pushed Sept 28, 2026.** Postseason BF multiplier
   (`config.POSTSEASON_BF_MULTIPLIER = 4.35/5.19`, applied in
   `predict_distribution()`/`project_date()` in `kproj/model/predict.py`),
   gated on `game_type in config.POSTSEASON_GAME_TYPES`. Inference-time only —
   training is untouched — and scales the whole predicted distribution
   (point + all quantiles) down uniformly, preserving monotonicity, rather
   than adjusting any K-rate/quality input. Verified the gating logic (R →
   1.0, postseason game types → the multiplier, NULL/pre-migration → 1.0) and
   the scaling math (point re-floored at 0.05, quantile order preserved)
   directly; full smoke test (train → project → score → reconcile) still
   passes end to end.
3. **DONE, pushed Sept 28, 2026** (as `kproj/signals/workload.py`, not
   `workload_news.py` — matches the existing `social.py` naming). Built as
   designed, with one deliberate change from the original plan: **keyword/
   phrase matching instead of LLM extraction.** The evidence still says LLM
   extraction is the properly evidence-based answer for soft, qualitative
   language like the actual Misiorowski quote ("build him back up... in a
   mindful way") — but that costs money per call, and this project runs
   under an explicit zero-dollar rule (`PARKING_LOT.md`). Wiring in a paid
   API call inside an unattended daily workflow isn't a call to make
   silently; it's parked in `config.py`'s comment for Robin to decide
   explicitly if the keyword version's precision proves insufficient over a
   season. What shipped:
   - `config.WORKLOAD_KEYWORDS`: phrase → (confidence, flag_type), checked
     verbatim (case/accent-insensitive) against scraped/manual text.
     `config.WORKLOAD_NEWS_HANDLES` is deliberately empty by default — unlike
     `SIGNALS_CAPPERS`, these would be guessed, unverified handles if seeded
     now, so `lines/workload_notes.csv` (phone-editable, same pattern as
     `capper_picks.csv`) is the reliable path until real handles are added.
   - `kproj/signals/workload.py`: scrape (reuses `social.py`'s Nitter-mirror
     rotation, refactored into a shared `fetch_rss()`) + manual CSV + the
     conservative extractor. Never touches `kproj.db` — same boundary as the
     rest of `kproj/signals/`.
   - A `workload_flags` table in `signals.db` (isolated, best-effort, exactly
     like `capper_picks`).
   - **Trust but verify, wired for real**: `kproj/export/site_export.py` now
     best-effort reads `signals.db` (read-only; a missing file, lock, or
     schema mismatch degrades to no flag, never breaks `today.json`) and
     marks each flag `corroborated` when the K line has already moved down by
     `config.WORKLOAD_LINE_MOVE_CONFIRM_K` (0.5 K) or more, `unconfirmed`
     otherwise. This is genuinely "a best-effort flag on the board" — it
     rides in `today.json`'s `workload_flag` field per starter, not a silent
     model input.
   - Caught and fixed one real bug while testing: an early draft inferred
     `flag_type` from substrings of the matched phrase (checking for `"il"`
     to mean "return from injury"), which silently matched inside unrelated
     words (`"il"` is a substring of `"built"`). Fixed by attaching the type
     explicitly per keyword instead of inferring it — caught by a test built
     specifically to catch it, not by inspection.
   - **DONE, pushed Sept 28, 2026.** Frontend badge: `workload_flag` renders
     on the Today board table (`docs/assets/app.js`'s `workloadNote()`,
     styled in `docs/assets/style.css`) — a small note under the pitcher's
     name, solid/gold when corroborated, dim when unconfirmed, snippet on
     hover. All 3 build-order items are now fully shipped end to end.

## 7. Methodology and Performance pages — updated Sept 28, 2026

Robin asked for `methodology.html` to be rewritten to fully capture the
model (all of the above — postseason mode, the workload flag, the
`team_leash_bf` feature, the retrain/predict feature-list decoupling fix)
and for `performance.html` to be redesigned around what he actually cares
about: a historical record of what the model suggested vs. the book lines
and how it performed, which the old layout buried under a Betting-record
tile row, a Recent-form tile row, the Model-vs-market section, a raw daily
K-MAE chart, and a calibration chart — in that order, with the actual
pick-by-pick ledger last. He also flagged CLV (a graph near the top) and the
raw K-MAE chart as not worth the space.

Changes:
- `kproj/export/site_export.py`: `_bet_metrics` now also returns
  `wins`/`losses`/`pushes` (previously only `hit_pct`); a new
  `_market_history()` builds a newest-first, per-pick ledger (date, pitcher,
  side/line/book/odds, model point estimate, actual K, result, units, and an
  `is_edge` flag marking whether that pick was also a positive-EV
  recommendation) from the existing `_market_rows()` data — which already
  covered every graded start the model had an opinion on, not just +EV
  picks, it just wasn't exposed as a list before. The unused daily raw-MAE
  query/field is removed.
- `docs/performance.html` / `docs/assets/perf.js`: reordered around one
  headline section, "Model vs. the book lines" (tiles + cumulative-units
  chart + monthly model-vs-book MAE chart + the new ledger table, dot-marked
  for +EV picks), with the positive-EV betting record, model quality
  (MAE/bias/coverage, no chart), calibration, and model versions as smaller
  sections below it. CLV is no longer tracked or displayed anywhere on the
  page (previously an "avg CLV" tile in two places); the raw daily K-MAE
  line chart is gone (replaced by compact lifetime/30-day MAE tiles).
- `docs/methodology.html`: added "Postseason mode" and "Short-leash /
  workload flag" sections, a "What isn't modeled yet: times-through-the-order"
  note, a "Retraining without breaking live projections" note on the
  feature-list decoupling fix, `team_leash_bf` added to the feature list,
  and the Performance-tab and Signals-tab sections updated to match the
  above.
- Verified via the smoke test (asserts on `betting.lifetime.wins/losses` and
  `market.history`), a direct in-memory check of the new export functions'
  JSON shape, and a Node `vm`-sandbox render of `perf.js` against synthetic
  data (including an XSS-escaping check on a pitcher name).

## 8. Postseason ace adjustment — research plan (documented Oct 11, 2026, NOT YET STARTED)

Robin asked (Oct 2, then again Oct 11 after the Division Series) to check
model results against actual playoff outcomes and other public strikeout
prop pickers. Both checks found the same pattern, and a third conversation
(Oct 11) worked out what a real, evidence-based fix would need to include.
This section documents that plan so it can be picked up later — **no code
has been changed yet.**

### 8.1 The evidence

Pulled from the live site's `recent.json`/`performance.json` each time (not
re-derivable later without re-pulling, since those files roll forward) —
raw numbers kept here for reference.

**Oct 2 check (wild card only, n=16 starts, Sept 29-30):** MAE 2.09 vs. the
model's trailing-30-day regular-season MAE of 1.85 and lifetime 1.77. Mean
signed error (point estimate minus actual) -0.52 — a real bias, not just
noise: 10 of 16 starts came in *over* the projection. Of 6 starts that had a
posted prop line, the model's edge picks went 2-4 for -1.70 units, and the
market line finished closer to the actual K total than the model on 5 of
6 (tiny sample, flagged as such at the time).

Checked against publicly posted picks for the same games (DraftKings
Network, RotoWire, Lineups.com): on the two highest-profile matchups — Cam
Schlittler over 7.5 (actual 10) and Michael King over 4.5/5 (actual 8) —
every outlet correctly called the over; our model projected both well
under (5.49 and 4.40). On Payton Tolle, conversely, our model's line play
won while a Lineups.com over-7 pick on him lost (he threw 4). So it wasn't
"behind the market across the board" — it was specifically missing high on
the marquee, dominant-stuff starts.

**Oct 11 check (wild card + Division Series, n=50 starts through Oct 10):**
The betting record recovered to roughly normal — 16-15 (51.6%) for -1.51
units across 31 graded lines, with the model's point estimate closer than
the book line on 16 of 31 (51.6%), both numbers landing right on the
model's long-run averages (54.6% hit rate, 51.7% model-closer lifetime).
**But the underlying projection bias did not shrink. If anything it got
more lopsided:** 30 of 50 postseason starts came in over projection (60%),
wild card alone was 10/18 (56%), Division Series alone was 20/32 (62.5%).
Average error size (MAE) did improve to 1.85 postseason-wide, back near
normal range — so the *typical* miss is a normal-sized miss, but the
*tail* is still consistently missing low.

The sharpest new data point: **Gavin Williams was underprojected by more
than 5 strikeouts in back-to-back playoff starts** — projected 5.47,
actual 11 (Oct 5); projected 5.61, actual 12 (Oct 10) — the second one with
the model at 72.7% confidence on the *under*. That's the same pitcher
fooling the model twice, which argues for a specific signal being missed
(stuff/role change) more than generic postseason noise. Yamamoto (proj
4.08, actual 10) and Drew Rasmussen (proj 4.66, actual 10) were the other
standout misses — all three are swing-and-miss/high-ceiling arms blowing
well past projection in high-leverage spots.

### 8.2 Root cause, found by reading the actual adjustment code

`kproj/config.py`'s `POSTSEASON_BF_MULTIPLIER = 4.35 / 5.19` (~0.838) is
the entire postseason adjustment today. Per its own comment, it's a pure
**quantity** discount — scales the whole predicted distribution down at
inference time to reflect FanGraphs' 2025 finding that postseason starts
average 4.35 IP vs. 5.19 IP in the regular season (fully-rested bullpens
pull starters earlier), explicitly **not** a quality/rate adjustment —
FIP is flat (~3.64) either way per that same source, so no K-rate/quality
input is touched, only the output volume.

This explains the pattern cleanly: for a pitcher whose K-rate per batter
holds steady in the playoffs, "fewer batters faced → fewer K's" is
correct, and that's exactly the cohort the current multiplier gets right
(Tolle, Hagen Smith, Blubaugh all tracked close). For a pitcher whose
per-batter K-rate is *rising* enough in these spots to offset or outrun
the shorter outing, the multiplier has no mechanism to catch that — it can
only ever push the number down, never up. That's the aces: Sale,
Schlittler, King, Yamamoto, Rasmussen, Williams.

Also from that same comment block: **postseason game ingestion was broken
until Sept 2026** (`kproj/ingest/mlb_api.py` hard-filtered to
`gameType == 'R'`, so the schedule fetch returned zero games for any
October date — see §1). That means the database currently holds real
postseason box scores for exactly one month: this October. Everything in
§8.1 is drawn from essentially two pitchers having big games, not a
trained signal.

### 8.3 What a real, data-grounded adjustment needs (in order)

1. **Backfill historical postseason box scores first.** Statcast/boxscore
   ingestion already reaches back to 2022-04-07 for the regular season;
   the postseason game-type filter (`POSTSEASON_GAME_TYPES = {"F","D","L","W"}`
   in `kproj/config.py`) now exists and works (fixed §1), it was just never
   run retroactively. Re-running `kproj backfill` (or a postseason-scoped
   variant) for Octobers 2022-2025 is a data-collection step, not a
   modeling step, and is the actual prerequisite for everything below —
   right now there isn't enough data to fit anything.

2. **Build a continuous stuff-quality tier, not a hand-picked "ace" list.**
   Candidates already derivable from existing data: season K/9 percentile,
   K-rate, whiff rate, fastball velocity percentile. The question to test:
   does the postseason K-rate delta (actual K-rate per batter faced minus
   the pitcher's own regular-season rate) scale with that tier, or is it
   flat across pitchers and we're looking at a couple of loud outliers.

3. **Compare each pitcher to their own regular-season baseline, not to a
   pooled postseason-vs-regular-season average.** Playoff teams' pitchers
   are already selection-biased toward better than league average, so a
   pooled comparison would bias any adjustment upward for the wrong
   reason. Within-pitcher (same person, same stuff, different context)
   is the comparison that actually isolates a postseason effect.

4. **Test whether the leash assumption itself holds by tier.** The
   multiplier assumes every postseason starter gets pulled earlier. The
   `team_leash_bf` feature (already in the model) can check this directly:
   does batters-faced actually drop for top-tier arms in the playoffs, or
   does the FanGraphs league-wide average get dragged down entirely by the
   complementary-arm population? If leash doesn't shrink for the top tier,
   the BF discount shouldn't apply to them at all, independent of any
   rate-side correction.

5. **Test plausible mechanistic covariates for correlation with the
   residual** (not assumed, tested): same-start fastball velocity vs. the
   pitcher's own season average (adrenaline/short-rest effect); days of
   rest before the start (playoff starters often get extra rest); and
   elimination-game leverage, tested as its own variable rather than
   lumping all postseason games together (a do-or-die start may get a
   different hook than a Game 1).

6. **Use shrinkage, not a free-fit coefficient, once there's a tier-level
   signal.** Postseason innings will always be a tiny fraction of the
   regular-season dataset even after backfill, so any tier-level
   adjustment should partially pool each new playoff start toward its
   tier's group average (empirical-Bayes style) rather than letting one or
   two marquee starts swing the whole correction — the exact failure mode
   that would let the two Gavin Williams starts alone define the "ace"
   tier's adjustment.

7. **Backtest with the same discipline `kproj/model/train.py` already
   uses for the main model:** fit on backfilled 2022-2024 Octobers,
   validate on 2025, sanity-check against 2026-to-date. Acceptance bar:
   closes the bias on the ace tier **without** re-breaking the
   complementary-arm tier the current flat multiplier already gets right
   — i.e. the fix should nest the existing multiplier as a floor/base
   case and add a tier-scaled upward correction, not replace it outright.

### 8.4 Status

Not started. This is a backfill project first, then a validation project,
before any change to `POSTSEASON_BF_MULTIPLIER` or the prediction code
itself. Pick up at step 1 (§8.3) when ready to execute.

## 9. Offseason model-review plan — October 25, 2026

### 9.1 What's already handled automatically

No manual action is needed to train the model on the full 2026 regular
season. `.github/workflows/retrain.yml` runs on an unconditional cron
(`0 8 * * 1`, every Monday, year-round, no season gate) and already
executes `kproj retrain` + `kproj export` against the complete database
every week, including straight through the 2026 season and postseason.
Model version timestamps confirm this has fired on schedule throughout
the year (e.g. `v20260928_1639` → `v20261005_1700` → `v20261019_1526`).
By the time the 2026 season is fully logged, the model will already have
been retrained on all of it without anyone doing anything.

One honest caveat: this session's GitHub API access is not enabled for
pulling historical Actions run-duration logs (`gh run list` and the raw
`api.github.com/.../actions/workflows/retrain.yml/runs` endpoint both
returned 403 "GitHub access to this repository is not enabled for this
session"), so the exact wall-clock time of a retrain run couldn't be
confirmed empirically this session. A reasoned estimate, not a measured
one: with roughly 104,000 training rows and 6 total LightGBM models (1
Poisson point model + 5 quantile heads), training itself is almost
certainly a low single-digit number of minutes; the 120-minute workflow
timeout exists mainly as a generous ceiling, not because training is
expected to approach it. If a precise number is ever wanted, it's a
one-line look at the Actions tab in a browser, or pulled next session if
GitHub API access is enabled by then.

### 9.2 The actual offseason work is a planning problem, not a training one

Since retraining is already fully automated, the real question for the
winter is *what, if anything, should change* before the model trains on
2027 data, and when to check that nothing silently broke before next
Opening Day. Proposed four-phase plan:

**Phase 1 — Now / postseason wrap-up.** Freeze an end-of-2026 baseline
before memory of the season fades: current model version, full-season
MAE and bias, and the full betting record (win rate, units, ROI) as of
World Series end. This baseline is what any 2027 change gets measured
against, and it's also what a 2027 spring-training sanity check gets
compared to, to confirm nothing regressed over the offseason.

**Phase 2 — Winter (the actual window for deliberate changes).** This is
the one stretch of the year where a model change can be made, tested, and
reverted with zero live betting exposure. Top item already queued: the
§8 postseason ace-adjustment research plan above, now anchored by a
concrete case study (Gavin Williams missed low in all 3 of his 2026
postseason starts by an average of 5.4 K, the strongest single data
point for why the current flat BF-discount can't be right for top-tier
arms). Any other model ideas that come up over the winter should also
land here, not mid-season.

**Phase 3 — Spring training (~mid-February).** Before Opening Day, re-run
a smoke test against that offseason's actual rosters and verify the MLB
Stats API ingestion still matches the expected schema (upstream APIs
occasionally change shape between seasons without notice). Compare
against the Phase 1 baseline to confirm the automated weekly retrains
over the winter (if postseason-adjacent data keeps flowing in) didn't
drift the model somewhere unintended.

**Phase 4 — Opening Day.** Normal weekly retrain cadence resumes mattering
live; no different from how 2026 ran.

### 9.3 Status

Not started, this is a plan to pick up in winter, not a task for right
now. Documented so it's sitting here ready to execute whenever Phase 2
actually begins. No code changed as part of writing this section.
