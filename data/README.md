# data/

- `ballparks.csv` — static ballpark metadata (public facts) + 3-yr K park factors.
  - Coordinates/elevation: approximate, good enough for weather lookups.
  - `k_factor`: 100 = neutral. Seeded from public FanGraphs/Savant values (~2024-25).
    Small (Tier-2) effect. Update once a year by editing this file; a wrong value
    by ±2 moves a projection by ~0.05 K.
  - `orientation_deg` (home plate → CF bearing) is approximate and currently unused
    by the model (wind direction is a Tier-3 signal we skip).
- `kproj.db` — the SQLite database. Not committed; persisted as a GitHub Release
  asset (`data-latest`) by the workflows.
