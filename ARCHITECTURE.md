# Architecture — what was actually built

`README.md` is the original **research/planning doc**. This file records what the
code actually does, and where it deliberately diverged from that plan and why.

## System at a glance

```
  SOURCES                        PIPELINE                      DASHBOARD
  ───────                        ────────                      ─────────
  Craigslist (HTML) ─┐
  PM sites (httpx)  ─┼─► scraper.py ──► filters ──► SQLite ──► app.py (Flask)
  PM sites (JS)     ─┘    (normalize)   (non-rental,  (rentals.db)   │
  Facebook groups ──► import_listings.py  geo, type)        ▲        ▼
  Nextdoor (manual) ─► quick_add()                          │   browser UI
                                          enrich.py ────────┘   (status, notes,
                                          (Haiku scoring)        filters, sort)
```

**One-line data model:** every source normalizes to the same `listings` row,
passes the same filters, and (once scored) sorts by `fit_score` in one dashboard.

## Components

| File | Role |
|---|---|
| `db.py` | SQLite layer. Schema + idempotent migrations, upsert (dedup by URL, bumps `last_seen`/`times_seen`), read/update helpers. |
| `scraper.py` | Craigslist (HTML) + static PM sites (httpx) + JS PM sites (Playwright). All the filter functions live here and are reused everywhere. |
| `enrich.py` | Haiku scoring. Sends each unscored listing to Claude, gets a structured summary + 0–10 fit score, stores it. |
| `import_listings.py` | Manual ingestion for Facebook/Nextdoor — CLI (`--source x file.json`) and `quick_add()` (one pasted post). Reuses scraper filters. |
| `app.py` | Flask dashboard. Filters/sort/search, status + notes, the Quick Add box, staleness annotation. |
| `templates/index.html` | The dashboard UI. |
| `sources.yaml` | Source registry (Craigslist queries, static sites, Playwright sites). |
| `RUNBOOK_chrome.md` | Weekly procedure for the manual Facebook/Nextdoor channel. |

## How the original plan changed (and why)

| Planning doc said | What we built | Why |
|---|---|---|
| Google Sheets as the database | **Flask + SQLite** | User wanted a real status-tracking dashboard (review → interested → applying), not a spreadsheet. |
| Craigslist **RSS** (`&format=rss`) | Craigslist **HTML** search | Craigslist returns 403 on the RSS endpoint from this IP; the HTML search works and is parsed instead (fixtures guard the structure). |
| Auto-scrape Facebook + Nextdoor | **Quick Add** box (paste a post → filtered → scored) | FB group feeds are virtualized + text-obfuscated; reliable bulk scraping isn't feasible and risks the account. Nextdoor blocks agent navigation entirely. |
| Several PM sites via simple HTTP | Split into **static (httpx)** vs **JS (Playwright)** | Atari/Welcome Home/etc. are JavaScript-rendered; they return only nav skeleton to plain HTTP. |
| GitHub Actions cron scraper | Workflow present but **dormant** | GH runners are ephemeral — the SQLite file evaporates. Reactivate when moving to Railway + Postgres (see below). |

## Filters (shared across all sources)

All live in `scraper.py` and are applied at scrape time, after Craigslist detail
enrichment, and on manual ingest:

1. **`is_likely_listing`** — must look like a rental (price or ≥2 rental keywords).
2. **`classify_non_rental`** — drops jobs, personals, ISO (renter-seeking) posts,
   and commercial spaces (offices, salons, storage, event halls, etc.).
3. **`classify_out_of_scope`** — keeps Wauwatosa + ~2mi (West Allis, Story Hill,
   Washington Heights) plus explicit exceptions (Riverwest 53212, AmFam Field
   53214). Uses ZIP allow/deny + a street-name guard so "Greenfield Ave" (a
   Milwaukee street) isn't mistaken for Greenfield (a suburb).
4. **`classify_listing_type`** — `rental` (whole unit) vs `roommate` (room in a
   shared place).

## Scoring (`enrich.py`)

- Model: **claude-haiku-4-5** (chosen for cost; extraction + scoring is in its
  wheelhouse). ~$0.003/listing; only **unscored** listings are processed, so
  re-runs cost pennies.
- Structured output via `messages.parse(output_format=ListingEnrichment)`.
- `field_validator`s normalize model enum drift (e.g. `on_street` → `street`).
- **No prompt caching** — verified the system prompt is only ~530 tokens, well
  under Haiku's 2048-token cache minimum, so `cache_control` would be a no-op.
  The bulk of per-call tokens is the structured-output schema, which the SDK
  injects each call and isn't easily cacheable. Cost is already negligible.

## Staleness

`last_seen` is bumped every scrape for listings still present. The dashboard
derives "seen Nd ago" and flags **auto-scraped** listings not re-seen in 21+ days
as "likely gone" (with a hide filter). Manual sources (FB/Nextdoor) aren't
re-scraped, so their staleness is informational only.

## Running it

```bash
pip install -r requirements.txt
python -m playwright install chromium      # one-time, for JS PM sites
cp .env.example .env                        # add ANTHROPIC_API_KEY
python scraper.py                           # collect/refresh listings
python enrich.py                            # score new listings
PORT=5001 python app.py                     # dashboard at http://localhost:5001
```
(macOS note: port 5000 is taken by AirPlay; use 5001.)

## Deferred / future work

- **Chunk 5 — Gmail email-alert ingestion** (Zillow/Apartments/Realtor → same
  pipeline). National portals skew corporate; use the FRBO/by-owner searches.
- **Corporate-vs-independent classifier** — flag small-landlord listings
  (tell: Avail/TurboTenant application links, odd phone formatting).
- **Railway + Postgres deploy** — for sharing with a roommate; also reactivates
  the GitHub Actions cron (set `FLASK_ENV=production`, `SECRET_KEY`, and
  `BASIC_AUTH_USERNAME`/`PASSWORD`).
- **Stale → archive** — currently staleness is display-only; could auto-archive.
