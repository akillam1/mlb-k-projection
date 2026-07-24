# Parking Lot — deferred items (everything that costs money, plus later-phase work)

Per the zero-cost constraint (June 2026). Each item lists the trigger that would
justify revisiting it and what to do then.

## Costs money — parked

### 1. ~~The Odds API paid plan for K props~~ — UN-PARKED July 2, 2026
It turned out current-day player props (incl. `pitcher_strikeouts`) ARE on the
free tier — only *historical* endpoints require a paid plan. `fetch_k_props` in
`kproj/ingest/odds.py` now pulls one props snapshot daily (~1 credit/game via
the per-event endpoint) into `manual_k_lines`; manual CSV entry remains as an
override/fallback.

What a paid plan (~$59/mo, verify pricing) would still add:
- **Multiple snapshots/day + true closing lines** (real CLV on every bet):
  the 500-credit free budget only affords ~1 props pass/day (~430/mo) plus one
  game-lines pass (~60/mo).
- **Alternate K lines** (`pitcher_strikeouts_alternate`) — more credits/event.
- **Trigger to revisit:** wanting real CLV tracking or fresher evening lines.
  Cheaper alternatives to evaluate first: OddsBlaze, SportsGameOdds.

### 1b. X (Twitter) API for reliable capper scraping (~$200/mo)
Added July 24, 2026. The Signals tab tracks five capper accounts, but X blocks
anonymous scraping (login wall + datacenter-IP blocks) and the free API tier has
no read access. Current approach: best-effort public Nitter-style mirrors, which
die often, plus the lines/capper_picks.csv manual fallback. If mirror coverage
gets unbearable, the Basic API tier ($200/mo, 10k reads) would make it reliable
— parked as flagrantly violating the zero-dollar rule.

### 2. Open-Meteo commercial plan (~€29/mo)
Only required if the site becomes commercial (ads, subscriptions, affiliate
links). Personal hobby use is squarely within the free non-commercial tier.
- **Trigger:** any monetization. Also re-read §11 of the roadmap (affiliate
  disclosure, state rules) before that — monetization changes the legal posture,
  not just this bill.

### 3. Managed hosting stack (Supabase Pro $25/mo, Fly.io ~$5/mo, Vercel Pro $20/mo)
The roadmap's public-scale stack. Irrelevant while the audience is you + friends.
- **Trigger:** you outgrow GitHub (need sub-5-minute refresh cadence, user
  accounts, a real API, >hundreds of daily visitors). Migration path: SQLite →
  Postgres is straightforward from `kproj/db.py`'s schema; ingestion/model code
  is storage-agnostic apart from SQL dialect touch-ups.

### 4. Custom domain (~$10–15/yr)
`yourname.github.io/k-board` works fine. If you want `kboard.whatever`: buy the
domain, add a CNAME in repo Pages settings. Cosmetic only.

### 5. Paid projection priors (THE BAT X, ATC, etc.)
The model uses a free Marcel-style prior (last season regressed to league).
Steamer/ZiPS scraping is ToS-gray, so it's skipped too.
- **Trigger:** if April–May accuracy bothers you next spring. Cheap manual
  option: paste a preseason K% projection CSV into `data/` and join it in
  `FeatureStore.pitcher_features` as the prior.

## Free but deferred (complexity, not cost)

- **Monthly Optuna hyperparameter sweeps** (roadmap §5.5): worth doing after a
  full season of logged data; current fixed params are sensible defaults.
- **Hierarchical Bayesian model in PyMC** (roadmap Phase 5): revisit after ~2
  seasons of your own logged projections.
- **Roof status scraping for retractable parks**: weather is attenuated 50%
  there instead; tiny signal, real scraping headache.
- **Catcher framing feature**: Savant publishes it; small Tier-3 gain. Add as a
  static per-catcher CSV if motivated.
- **Issue-form line entry** (GitHub issue template → workflow parses → commits):
  slicker phone UX than CSV editing if friends start contributing lines.
- **Notifications** (email/push on high-edge picks 90 min pre-game): GitHub
  Actions can send email free via `mail` actions or a Discord webhook — add
  when there's a track record worth alerting on.
- **Bullpen/alt-line/multi-sport markets** (roadmap Phase 5+).

## Explicitly rejected free options (for the record)

- **Render free Postgres** — expires after 90 days; data loss trap.
- **Supabase free tier** — pauses after 7 days of inactivity.
- **Streamlit Community Cloud** — sleeps + cold-starts; worse on phones than
  static Pages.
- **Railway** — no free tier since 2023.
