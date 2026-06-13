# Parking Lot — deferred items (everything that costs money, plus later-phase work)

Per the zero-cost constraint (June 2026). Each item lists the trigger that would
justify revisiting it and what to do then.

## Costs money — parked

### 1. The Odds API "20K" plan — automated K prop lines (~$59/mo, verify current pricing)
The big one. The roadmap called this "non-negotiable" for a public product; for
personal use, manual line entry replaces it at $0.
- **What you'd get:** automatic pitcher_strikeouts (+ alternate lines) from
  DK/FD/MGM/Caesars every refresh, automatic closing lines (real CLV on every
  bet), no typing.
- **Re-enable:** add the paid key as the `ODDS_API_KEY` secret, then extend
  `kproj/ingest/odds.py` to request `pitcher_strikeouts` via the event-odds
  endpoint and write rows into `manual_k_lines` (or a new `book_k_lines` table)
  — the scoring pipeline downstream is already line-source-agnostic.
- **Watch:** per-market billing burns credits fast; poll caps and the
  `x-requests-remaining` guard are already in the code. Cheaper alternatives to
  evaluate first: OddsBlaze, SportsGameOdds.

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
