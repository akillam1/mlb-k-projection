"""Signals: best-effort capper scraping + free validation feeds.

Everything here is intentionally independent of the main kproj.db so the
hourly workflow can never race or clobber the projection database. State
lives in a small separate SQLite file (config.SIGNALS_DB) persisted as its
own release asset; model context comes from docs/data/today.json (already
exported by the daily pipeline) rather than from the big DB.
"""
