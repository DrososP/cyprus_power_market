# Cyprus power market — TSOC scraper

Pulls everything publicly published by the Cyprus TSO (tsoc.org.cy) into one
local archive: 48 report types from the S3 archive plus 5 interactive
time-series pages that have no export button.

See **[DATA_DICTIONARY.md](DATA_DICTIONARY.md)** for what each dataset contains,
the time conventions, and the gotchas. Read §2 (time conventions) before doing
any analysis — half-hourly vs 15-minute and the DST period count both bite.

## Install

```bash
pip install -r requirements.txt
```

## Use

```bash
# what's available
python tsoc_scrape.py list

# a small first run, to confirm everything works end to end (~5 minutes)
python tsoc_scrape.py series --only dam_prices_volumes --start 2026-07-01
python tsoc_scrape.py files  --only dam_forecast

# the full archive (hours — TSOC rate-limits, the scraper waits it out)
python tsoc_scrape.py all --start 2025-09-01

# Excel -> tidy CSV
python tsoc_parse.py inspect dam_forecast    # look before you trust
python tsoc_parse.py tidy
python tsoc_parse.py wide isp_clearing_mrp   # pivot one source to wide
```

Re-running is incremental: files already held are skipped, and series CSVs are
extended rather than rebuilt. Interrupt it whenever you like. Add `--refresh`
to force re-download.

## Dashboard

```bash
streamlit run dashboard.py
# or, if the streamlit shim isn't on PATH:
python -m streamlit run dashboard.py
```

Reads whatever is on disk — the Parquet build if you've run `tsoc_build.py`,
otherwise the CSVs directly. Six tabs:

| Tab | What's in it |
|---|---|
| Overview | Headline numbers, price, mix and daily RES share |
| Prices | Price and volume, duration curve, intraday shape by month, monthly stats |
| System & mix | Average-day duck curve, RES penetration distribution, monthly energy |
| Balancing market | Balancing vs day-ahead price and spread, activated energy, reserve prices by product, who provides reserve, system balance deviation |
| Price vs fundamentals | Price against net load and RES share, correlations |
| Data & quality | Coverage, DST-day check, gaps, and a CSV export of any view |

Scrape more history and the app picks it up on the next reload — hit **Reload
CSVs** in the sidebar, or just refresh the page.

Two things it does on your behalf, both of which are easy to get wrong by hand
(see DATA_DICTIONARY §2): resampling averages prices and MW but **sums** MWh,
and percentage columns are recomputed from the MW columns rather than averaged.
The Data & quality tab checks both DST transition days explicitly rather than
only flagging days that look unusual — the October fall-back loses an hour
silently, because the repeated wall-clock timestamps de-duplicate against each
other.

## Output

```
data/
  raw/<source>/<YYYY>/<MM>/*.xlsx    original files, untouched
  series/<source>.csv                stitched HTML time series
  tidy/<source>.csv                  parsed long-format: timestamp,variable,value,unit
  parquet/<name>.parquet             typed, sorted build output (tsoc_build.py)
  warehouse.duckdb                   catalog of views over data/parquet/
  html/<source>/*.html               cached raw pages (for debugging parsers)
  manifest.csv                       every file ever downloaded
```

Raw files are kept deliberately — the tidy layer is a best-effort parse across
workbooks with inconsistent shapes, so you always have the original to go back
to.

## Balancing market

```bash
python tsoc_scrape.py files --only bm_daily_activity_en isp_balancing_bdl
python tsoc_bm.py parse          # --full-reserves for per-unit detail too
```

`REP_TSO-001` is **transposed** — time runs left-to-right across the columns
and variables run down the rows in stacked blocks — so `tsoc_parse.py`, which
assumes a header row with a time column beneath it, cannot read it. `tsoc_bm.py`
handles that layout directly and writes four tidy CSVs:

| File | Grain | Contents |
|---|---|---|
| `bm_energy_5min.csv` | 5 min | Balancing energy marginal price up/down, activated energy, total available reserves |
| `bm_system_30min.csv` | 30 min | Ex-post load, generation by fuel, aggregated offers, FCR/aFRR/mFRR/RR marginal prices |
| `bm_bsp_results.csv` | 30 min, long | Reserve capacity awarded per unit, per product, per direction |
| `bdl_system_deviation.csv` | 30 min | System balance deviation from `REP_TSO-009-BDL` |

Two things worth knowing before using them:

- **`999999` and `25000` are markers, not prices.** They appear in a meaningful
  share of intervals. Both are excluded from `price_up` / `price_down` and kept
  verbatim in `price_up_raw` / `price_down_raw`, so an average is honest and
  nothing is thrown away.
- **These files handle DST correctly** — 50 periods on the October fall-back
  day, where the scraped HTML series silently loses an hour. The repeated
  wall-clock timestamps are disambiguated by the `period` / `interval` column,
  so key on that, not on the timestamp alone.

Units are not published anywhere in `REP_TSO-001`; those recorded in
`tsoc_data.py` are inferred from magnitude and market context.

## Parquet + DuckDB layer

```bash
python tsoc_build.py             # after any scrape or parse
python tsoc_build.py --strict    # non-zero exit if a check fails (for CI/cron)
```

Converts the derived layer to typed, timestamp-sorted Parquet and writes
`data/warehouse.duckdb`. `data/raw/` is untouched and remains the authoritative
record — every parser here has been wrong at least once, and the raw workbooks
are what makes that recoverable.

The catalog holds **views over the Parquet files, not copies of them**. Re-run a
parser, overwrite a Parquet file, and every view is current with no reload step;
the Parquet also stays readable by pandas, Polars or R, so nothing is locked in.
The one materialised table is `panel_30min` — day-ahead, system mix and
balancing joined onto the settlement period — because that join is expensive and
gets run constantly.

```bash
duckdb data/warehouse.duckdb
```
```sql
SELECT date_trunc('month', timestamp) AS month,
       round(avg(dam_price), 1)  AS dam,
       round(avg(price_up), 1)   AS balancing_up,
       round(avg(spread_up), 1)  AS spread
FROM panel_30min GROUP BY 1 ORDER BY 1;
```

No indexes, deliberately. DuckDB is columnar and builds zone maps
automatically, and Parquet carries row-group statistics, so a time filter
already skips whole chunks — sort order on disk is the lever that matters, and
every file is written sorted. Open the catalog `read_only=True` from the
dashboard: DuckDB allows a single writer, and a build running while the app
holds the file open would otherwise collide.

`tsoc_data.py` prefers Parquet automatically, but **only when it is at least as
fresh as the CSV it came from** — a parser run since the last build falls back
to CSV rather than silently serving a stale copy.

The build also runs data-quality assertions and prints a pass/fail line for
each. They exist because the two real defects found in this dataset so far — an
hour vanishing on the October DST day, and `999999` loading as a price — are
both things a database would have ingested without complaint.

## Deploying it

See **[DEPLOY.md](DEPLOY.md)**. `docker compose up -d --build` gives you two
containers off one image — the dashboard, and a scheduler that runs
scrape → parse → build once a day — sharing a volume. The app binds to
localhost only; a Cloudflare Tunnel or Tailscale provides access, so the host
never opens a port.

## Files

| File | Purpose |
|---|---|
| `tsoc_sources.py` | Registry of all 53 sources — add new ones here |
| `tsoc_scrape.py` | Downloader: discovers S3 links, pages the HTML series |
| `tsoc_parse.py` | Excel → tidy CSV, plus `inspect` for exploring workbooks |
| `tsoc_bm.py` | Balancing market parser — reads the transposed `REP_TSO-001` layout |
| `tsoc_build.py` | Parquet layer + DuckDB catalog + data-quality assertions |
| `dashboard.py` | Streamlit dashboard over the scraped series |
| `tsoc_data.py` | Loading layer for the dashboard: Greek→English columns, safe resampling, data-quality checks |
| `test_parsers.py` | Offline tests for the parsing helpers (`python test_parsers.py`) |
| `Dockerfile`, `docker-compose.yml` | Container build; app + daily scheduler sharing a volume |
| `refresh.sh`, `scheduler.sh` | One pipeline pass, and the once-a-day loop that runs it |
| `DEPLOY.md` | How to host it privately with a self-updating archive |
| `DATA_DICTIONARY.md` | What everything means |

## A caution on the tidy layer

`tsoc_parse.py` finds the header row and time index heuristically, because the
53 report types don't share a schema. It has been validated end-to-end against
real `DAM_FCST` downloads (48 periods, Greek headers, units parsed, period
index recovered) — but that is **one report type out of 48**.

So: run `python tsoc_parse.py inspect <source>` on each source you care about,
check the header row and column names it reports look right, and fix
`find_header_row` / `TIME_HINTS` / `PERIOD_HINTS` in `tsoc_parse.py` if a
report type is shaped unusually. The raw files and `manifest.csv` are
unaffected either way.

Headers are **Greek in some report types even in the "EN" editions** — see
DATA_DICTIONARY §5a for the `DAM_FCST` translation table.
