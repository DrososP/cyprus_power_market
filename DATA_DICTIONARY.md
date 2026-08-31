# TSOC Cyprus — Data Dictionary

What every dataset published by the Cyprus Transmission System Operator
(tsoc.org.cy) contains, where it comes from, and what to watch out for.

**Confidence note.** Everything in the *Sources*, *URL patterns*, *coverage
dates* and *granularity* sections was read directly off the live TSOC pages.
The **field-level** descriptions of what sits inside each Excel workbook are
inferred from report titles and the Cyprus Trading & Settlement Rules — the
workbooks themselves were not opened while writing this. Run
`python tsoc_parse.py inspect <source_key>` after your first download to see
the real sheet and column names, and correct anything here that differs.

---

## 1. How the site is organised

TSOC publishes in two completely different ways, and the scraper treats them
as two different problems.

**(a) Report archives — Excel/PDF files on S3.**
Roughly 48 report types. Each has a TSOC listing page that renders *every*
month of history as one long list of links. The files live on a public S3
bucket, `s3-eu-central-1.amazonaws.com/tso-cy/`. This is the bulk of the data
and it is the authoritative record.

**(b) Interactive time-series pages — HTML tables only.**
Five pages that render a table (and chart) for a date range and offer **no
export button**. The only way to get history is to page through the range
parameters and stitch the tables together. This is where the DAM price curve
and the 15-minute system generation mix live.

### Filename anatomy

This is the single most important structural fact about the archive:

```
REP_TSO-018-MRP-I1-20260801-20260731164129.xlsx?v971791025
└────┬─────┘ └┬┘ └┬┘ └───┬──┘ └──────┬─────┘ └────┬────┘
  report    variant run  trade date  generated-at  cache-buster
   code                  (the day     timestamp     (changes every
                          the data     (when TSOC    page load —
                          describes)   produced it)  ignore it)
```

**You cannot construct these URLs.** The `generated-at` timestamp is not
predictable from the trade date, so the scraper must read the listing page and
harvest the hrefs. That is why `tsoc_scrape.py` does a discovery GET per source
rather than looping over dates.

Two further wrinkles:

- The S3 key is usually `<CODE>/<YYYY>/<MM>/<file>`, but some sources
  (e.g. `REP_TSO-011`) use `<CODE>/<YYYY>/<file>` with no month folder.
- The `?v…` suffix changes on every page render. The scraper strips it before
  deciding whether a file is already held, so re-runs don't re-download.

### Run variants (`I1`, `I2`, `IA`, `FF1`, `S`)

Several report types publish more than one file per day. These are successive
runs of the same process, not different data:

| Variant | Meaning |
|---|---|
| `I1`, `I2`, … | Sequential intraday scheduling runs during the trading day |
| `IA` | Final/adjusted run for the day |
| `FF1` | First forecast run |
| `S` | Standard/summary issue of a market activity report |

**When building a time series, pick one variant and stay with it.** Mixing
`I1` and `IA` for the same timestamp double-counts. `IA` is the right choice
for "what actually happened"; `I1` for "what was known at gate closure".
`tsoc_parse.py` keeps the filename in `source_file` so you can filter on it.

---

## 2. Time conventions — read this before any analysis

| Aspect | Value |
|---|---|
| Timezone | Cyprus local: **EET (UTC+2)** winter, **EEST (UTC+3)** summer |
| Settlement period | **30 minutes** — 48 periods per normal day |
| System/SCADA data | **15 minutes** |
| DST clock change | Last Sunday in March (spring forward) and October (fall back) |

Consequences that will bite you:

- **Timestamps are naive local time.** Nothing published carries a UTC offset.
  If you are joining to anything international, localise to `Europe/Nicosia`
  first, then convert.
- **DST days are not 48 periods.** The March transition day has **46**
  half-hour periods; the October day has **50**, and two of them carry the same
  wall-clock label. Naive de-duplication on the timestamp string will silently
  drop an hour of October data every year. The scraper keys its series CSVs on
  the raw timestamp string, so *check the October transition day manually*.
- **Half-hourly ≠ hourly.** You asked for hourly granularity: the market's
  native resolution is finer. To get hourly, average prices (`EUR/MWh` is an
  intensive quantity) but **sum** energies (`MWh` is extensive). Averaging MWh
  across two half-hours halves your volumes.
- **Period 1 starts at 00:00.** Where a report gives only a period number,
  `timestamp = trade_date + (period - 1) × 30 minutes`.

---

## 3. Market structure in one paragraph

Cyprus runs a Forward Market (`REP_MO-002`), a Day-Ahead Market
(`REP_MO-003`), and a Balancing Market cleared through the **Integrated
Scheduling Process (ISP)** (`REP_TSO-008/009/018`). The ISP co-optimises
energy and reserve procurement, which is why reserve prices (`MRP`) and
capacity allocation (`RCA`) come out of the same run as the energy schedule
(`ISC`). Settlement happens monthly (`Market/MonthlySettlement`), is
aggregated (`AggSettlement`), and then reconciled once metering data firms up
(`RecSettlement`) — so **the same trading day has three successively more
accurate financial versions**. Use reconciled figures for anything financial.

---

## 4. Time-series sources (HTML pages, no export)

Scraped by `python tsoc_scrape.py series`. Output: `data/series/<key>.csv`.

> ### The `enddt` trap — verified 2026-08-15
>
> `enddt` is **not a date**. On the DAM price page it is a `<select>` whose only
> valid values are `+1days`, `+3days`, `+7days`:
>
> ```
> ?startdt=01-07-2026&enddt=%2B7days     ← works
> ?startdt=01-07-2026&enddt=07-07-2026   ← silently ignored
> ```
>
> Passing a date does **not** error. The server discards the whole range and
> renders its default page — tomorrow's data — with HTTP 200. Scraping a year
> of history that way yields a year of identical files, all showing one day.
>
> `startdt` *is* a real date, in `DD-MM-YYYY`.
>
> The scraper now sends the duration form, falls back to the date form if a page
> wants that instead, and **discards any row whose date falls outside the window
> that was requested**, logging a warning. If you write your own client against
> these pages, replicate that check — this failure is invisible without it.

### `dam_prices_volumes` — Day-Ahead Market prices and volumes
- **Page:** `/competitive-electricity-market/dam-volume-prices-graph/`
- **Granularity:** 30 minutes
- **Fields:** timestamp; market clearing price (**EUR/MWh**); cleared quantity (**MWh**)
- **This is the headline price series** — the number most analyses start from.

### `penetration_rates` — demand and generation mix
- **Page:** `/electrical-system/archive-penetration-rates/`
- **Granularity:** 15 minutes
- **Fields:** total system demand (MW); conventional generation (MW and % of
  demand); estimated distributed generation from PV and biomass (MW and %);
  wind generation (MW and %)
- **The most useful single system series** — it carries demand *and* the full
  mix *and* the RES share in one table, so it supersedes `system_generation`
  for most purposes.
- The PV figure is an **estimate**, not a measurement: distributed rooftop
  solar is not individually metered to the TSO. Treat it as indicative.

### `wind_solar_generation` — renewables detail
- **Page:** `/electrical-system/archive-total-daily-wind-and-solar-farm-generation/`
- **Granularity:** 15 minutes
- **Fields:** wind generation injected to the transmission system (MW);
  estimated distributed solar generation (MW)
- Wind here is transmission-connected wind only. Same estimation caveat on solar.

### `system_generation` — total generation on the transmission system
- **Page:** `/electrical-system/archive-total-daily-system-generation-on-the-transmission-system/`
- **Granularity:** 15 minutes
- **Fields:** conventional generation, wind, distributed generation (PV +
  biomass), total demand — all MW
- Overlaps heavily with `penetration_rates`; kept for cross-validation.

### `available_capacity` — available conventional capacity
- **Page:** `/electrical-system/daily-available-capacity/`
- **Granularity:** 15 minutes
- **Fields:** total available conventional generation capacity (MW)
- **Reference values as of Aug 2026:** installed conventional 1,478 MW; wind
  167.1 MW; solar 1,037.95 MW; biomass 12.4 MW. Available conventional on a
  typical day ≈ 1,244 MW.
- `available_capacity − demand` is your margin; pair with `non_availability_*`
  to explain why capacity is down on a given day.

> **If a series CSV comes out empty,** the page is rendering the table via
> JavaScript rather than server-side HTML. The scraper already falls back to
> reading Highcharts `categories`/`series.data` out of the inline script, and
> it keeps every raw page under `data/html/<key>/` so you can see exactly what
> was returned before writing a bespoke parser.

---

## 5. Report archives (Excel/PDF on S3)

Scraped by `python tsoc_scrape.py files`. Raw files land in
`data/raw/<key>/<YYYY>/<MM>/`; parsed output in `data/tidy/<key>.csv`.

### Forecasts

| Key | Code | Cadence | Coverage from | Contents |
|---|---|---|---|---|
| `dam_forecast` | `DAM_FCST` | Daily, pre-09:00 | Oct 2025 | Preliminary demand forecast, RES injection forecast, and system reserve requirements, published ahead of the day-ahead auction. **Column-by-column breakdown in §5a below** |
| `isp_forecast` | `ISP_FCST` | Daily, pre-14:00 D-1 | Oct 2025 | Load and RES forecasts feeding the Integrated Scheduling Process |
| `true_net_load_res_fcst_en` / `_el` | `REP_TSO-004` | Daily | May 2026 | **True load, net load and RES injection forecast.** Net load = demand − RES; this is the series conventional plant actually has to follow |
| `isp_forecasted_data` | `REP_TSO-008` | Daily (`FF1`) | May 2026 | Forecast inputs as actually used by the ISP run |
| `long_term_forecast` | — | Annual | — | Long-term generation adequacy outlook |

Comparing `dam_forecast` against `penetration_rates` outturn gives you forecast
error — one of the more tradeable signals in this dataset.

### 5a. `dam_forecast` (`DAM_FCST_YYYYMMDD.xlsx`) — verified against real files

The only workbook whose internals have been confirmed by opening actual
downloads (2025-10-01 and 2026-07-09). One sheet, `Προβλέψεις` ("Forecasts"),
49 rows × 17 columns: a header row plus **48 half-hour settlement periods**.
Column headers are **Greek only** — there is no English edition of this file.

| Col | Greek header | English | Unit |
|---|---|---|---|
| 0 | Περίοδος | Settlement period number, 1–48 | — |
| 1 | *(date value)* | Period **start** timestamp | — |
| 2 | Προκαταρκτική Πρόβλεψη για τις … | Title cell, values are all `-` | — |
| 3 | *(blank header)* | Period **end** timestamp | — |
| 4 | Πρόβλεψη Ολικής Ζήτησης στο κοινό σημείο Αναφοράς | Forecast total demand at the common reference point | MW |
| 5 | Πρόβλεψη ολικής ΦΒ Παραγωγής | Forecast total PV generation | MW |
| 6 | Πρόβλεψη ολικής αιολικής Παραγωγής | Forecast total wind generation | MW |
| 7 | Πρόβλεψη ολικής παραγωγής ΑΠΕ | Forecast total RES generation (= col 5 + col 6) | MW |
| 8 | Πρόβλεψη απαίτησης σε **ανοδική ΕΣΣ** | Forecast **upward** FCR requirement | MW |
| 9 | Πρόβλεψη απαίτησης σε ανοδική **αΕΑΣ** | Forecast upward **aFRR** requirement | MW |
| 10 | Πρόβλεψη απαίτησης σε ανοδική **χΕΑΣ** | Forecast upward **mFRR** requirement | MW |
| 11 | Πρόβλεψη απαίτησης σε **καθοδική ΕΣΣ** | Forecast **downward** FCR requirement | MW |
| 12 | Πρόβλεψη απαίτησης σε καθοδική αΕΑΣ | Forecast downward aFRR requirement | MW |
| 13 | Πρόβλεψη απαίτησης σε καθοδική χΕΑΣ | Forecast downward mFRR requirement | MW |
| 14 | Ελάχιστη απαίτηση ΥΕΜΠ | Minimum must-run requirement | MW |
| 15 | Εγκατεστημένη ΦΒ Ισχύς | Installed PV capacity | MWp |
| 16 | Εγκατεστημένη Αιολική Ισχύς | Installed wind capacity | MW |

**Acronyms.** `ΑΠΕ` = RES (renewables). `ΦΒ` = photovoltaic. `ανοδική` =
upward, `καθοδική` = downward. The reserve acronyms map to the standard
European products — `ΕΣΣ` → FCR, `αΕΑΣ` → automatic FRR, `χΕΑΣ` → manual FRR —
*by strong inference from the Greek expansions and the observed values, not
from a published TSOC glossary.* Confirm against the Trading & Settlement Rules
before quoting them in anything formal. `ΥΕΜΠ` (must-run) is the least certain.

**Observed behaviour worth knowing:**

- **The reserve requirements are mostly formulas, not forecasts.** Measured on
  2026-07-09, all 48 periods:

  | Col | Product | Behaviour that day |
  |---|---|---|
  | 8 | upward FCR | constant **36** |
  | 9 | upward aFRR | constant **9** |
  | 10 | upward mFRR | **= wind (col 6) + a step**, the step taking only 4 distinct values in 5–35 MW |
  | 11 | downward FCR | only two values, **4** or **14** |
  | 12 | downward aFRR | constant **10** |
  | 13 | downward mFRR | only two values, **16** or **26** |
  | 14 | must-run | constant **178** |

  So the only genuinely continuous reserve series is **upward mFRR, and it is
  driven by wind — not by total RES.** Solar carries no upward-reserve
  requirement in this formulation, which is a notable modelling choice given
  1,038 MWp of installed PV. Don't treat these seven columns as seven
  independent signals; six of them are step functions.

  Verified on one day only. Check whether the constants shift seasonally
  before relying on them.
- Col 7 = col 5 + col 6 exactly (RES = PV + wind), confirmed to 1e-6.
- Cols 15 and 16 are installed-capacity constants repeated on every row
  (1038 MWp solar, 170 MW wind as of Jul 2026). Useful as a capacity time
  series *across* files, redundant within one.
- Col 5 (PV) is 0 overnight, as you'd expect. Col 4 (demand) on 2026-07-09 runs
  ~578 MW at 04:30 to ~1062 MW at midday.
- **Headers changed between Oct 2025 and Jul 2026** — the two files do not have
  identical header rows. Don't assume a fixed column order across the archive;
  key on the header text, which is what `tsoc_parse.py` does.

### Day-ahead and forward markets

| Key | Code | Cadence | Coverage from | Contents |
|---|---|---|---|---|
| `dam_daily_activity_en` / `_el` | `REP_MO-003` | Daily (`S`) | Oct 2025 | Day-ahead market daily activity: cleared volumes, prices, bid/offer summary by settlement period |
| `fm_daily_activity_en` / `_el` | `REP_MO-002` | Daily (`S`) | Dec 2025 | Forward market daily activity and registered forward positions |

The English and Greek files are the same data. Scrape one; the `_el` sources
exist only if you want the Greek field labels.

### Balancing market and ISP

| Key | Code | Cadence | Coverage from | Contents |
|---|---|---|---|---|
| `bm_daily_activity_en` / `_el` | `REP_TSO-001` | Daily | Sep 2025 | Balancing market daily activity — imbalance volumes and prices |
| `bm_monthly_activity_en` / `_el` | `REP_TSO-002` | Monthly | — | Monthly balancing market summary |
| `rtbm_monthly` | `REP_TSO-011` | Monthly | Sep 2025 | Real-time balancing market data. **Note: stops Dec 2025** — superseded by the ISP reports |
| `rtbm_following_month` | `REP_TSO-012` | Monthly | — | Following-month RTBM data |
| `isp_balancing_bdl` | `REP_TSO-009-BDL` | Daily | Dec 2025 | ISP balancing data, **load side** — imbalance quantities and prices per settlement period |
| `isp_balancing_bdr` | `REP_TSO-009-BDR` | Daily | Dec 2025 | ISP balancing data, **resource side** — per-generator balancing volumes |

### ISP clearing results (`REP_TSO-018-*`)

All daily, from **May–Jun 2026**, published as `I1`/`I2`/`IA` runs. These are
the richest half-hourly market data available.

| Key | Suffix | Contents |
|---|---|---|
| `isp_clearing_isc` | `ISC` | **ISP schedule** — the cleared energy schedule per resource per period. The core output |
| `isp_clearing_com` | `COM` | **Commitment schedule** — which units are on/off, start-up and shut-down decisions |
| `isp_clearing_con` | `CON` | **Constraints** — binding network/security constraints in the clearing run. Explains anomalous prices |
| `isp_clearing_ids` | `IDS` | **Intraday schedule** — revisions after the day-ahead position |
| `isp_clearing_mrp` | `MRP` | **Marginal reserve prices** — clearing price per reserve product (EUR/MW) |
| `isp_clearing_pbe` | `PBE` | **Price band energy** — cleared energy per offer price band (MWh) |
| `isp_clearing_pbp` | `PBP` | **Price band prices** — the price band definitions (EUR/MWh) |
| `isp_clearing_rca` | `RCA` | **Reserve capacity allocation** — reserve volume awarded per resource (MW) |

`PBE` + `PBP` together reconstruct the **supply curve** per period. That is the
most analytically valuable thing in the whole archive and has no equivalent on
the interactive pages.

### Auctions and availability

| Key | Code | Cadence | Contents |
|---|---|---|---|
| `auction_spec_black_start` | `REP_TSO-013-BS` | Irregular | Black start auction specifications |
| `auction_spec_contingency_reserve` | `REP_TSO-013-CR` | Irregular | Contingency reserve auction specifications |
| `auction_spec_replacement_reserve` | `REP_TSO-013-RR` | Irregular | Replacement reserve auction specifications |
| `auction_results_bs` / `_cr` / `_rr` | `REP_TSO-014-*` | Irregular | Cleared auction volumes and prices for each reserve product |
| `must_run_results` | `Market/MustRunUnitsAuction` | Monthly | Must-run units auction results. **PDF, not Excel** — a historical CSV archive is also linked on the page |
| `non_availability_en` / `_el` | `REP_TSO-006` | Daily | Declared unavailability of resource objects — planned and forced outages. Join to `available_capacity` to attribute margin changes |
| `party_list` | `REP_MO-001` | Irregular | Registered market participants and their EIC codes. Your **entity lookup table** |

### Settlement

| Key | Prefix | Contents |
|---|---|---|
| `settlement_calendars` | `Market/Calendars` | Settlement timetable — tells you when each version of a month becomes available |
| `settlement_monthly` | `Market/MonthlySettlement` | Monthly settlement results per participant |
| `settlement_aggregate` | `Market/AggSettlement` | Aggregated settlement. Includes *System Trend and Marginal Balancing Energy Prices per RTU*, *marginal settlement prices per settlement period*, and *uplift account data*, each in **Initial** and **Final** versions |
| `settlement_reconciliation` | `Market/RecSettlement` | Reconciliation run once firm metering lands — `…_RecFinal-YYYYMM.xlsx`. **The authoritative financial figures** |

Version precedence, lowest to highest confidence:
`Initial → Final → RecFinal`. Always prefer `RecFinal` where it exists.

### Wider system and reference data

| Key | Contents |
|---|---|
| `res_curtailments_reports` / `_monthly` / `_annual` | RES curtailment volumes — how much renewable output was ordered off. Essential for explaining gaps between available RES and delivered RES |
| `metering_data_monthly` | Monthly metering summaries |
| `energy_generation_records` | Record peaks and milestones (max demand, max RES penetration) |
| `power_energy_marginal_cost` | Avoided power and energy marginal cost |
| `guarantees_of_origin` | Guarantees of origin issued for renewable generation |
| `supplier_energy_mix` | Annual supplier fuel-mix disclosure |

---

## 6. Tidy output schema

`data/tidy/<source_key>.csv`, long format:

| Column | Type | Meaning |
|---|---|---|
| `timestamp` | datetime | Local Cyprus time, start of the interval |
| `period` | int / blank | Settlement period number where the report gives one |
| `variable` | str | Column name from the workbook, unit stripped off |
| `value` | float | Numeric value; non-numeric cells are dropped |
| `unit` | str | Unit parsed out of the header, e.g. `EUR/MWh`, `MW`, `MWh` |
| `sheet` | str | Source sheet name |
| `source_file` | str | Original filename — **use this to filter run variants** |
| `trade_date` | date | Day the data describes, taken from the filename |

Long format is deliberate: TSOC workbooks differ in shape between report types
and some put several logical tables on one sheet, so a fixed wide schema would
break. Pivot per source once you know its columns are stable:

```bash
python tsoc_parse.py wide isp_clearing_mrp
```

---

## 7. Known gaps and gotchas

1. **Coverage start dates vary a lot** — Sep 2025 for balancing, but only
   May/Jun 2026 for the ISP clearing reports. There is no single date from
   which everything is available. Check per source before building a panel.
2. **`rtbm_monthly` stops in Dec 2025.** It is superseded by the ISP reports;
   don't treat the gap as missing data.
3. **The market is young.** Cyprus's competitive market started trading in
   late 2025, and early months include transitional arrangements and dry-run
   artefacts. Treat Oct–Dec 2025 as a settling-in period, not a normal sample.
4. **TSOC rate-limits.** Sustained requests return HTTP 403. The scraper waits
   ~1.2 s between requests and backs off exponentially. Don't lower `--delay`
   below ~1 s; a full first run takes a few hours and that is expected.
5. **Distributed solar is estimated, not metered.** Any RES penetration figure
   inherits that estimation error.
6. **Half-hourly vs 15-minute mismatch.** Market data is 30-minute, system data
   is 15-minute. Resample deliberately — average intensive quantities
   (prices, MW), sum extensive ones (MWh).
7. **October DST day has duplicate wall-clock timestamps.** See §2.
8. **No formal API and no terms-of-use guarantee.** These are public pages;
   the structure can change without notice. If a source suddenly returns zero
   files, check the listing page by hand before assuming an outage.

---

## 8. Where to start

For most price-and-fundamentals analysis you need four sources, not 53:

```bash
python tsoc_scrape.py series --only dam_prices_volumes penetration_rates --start 2025-10-01
python tsoc_scrape.py files  --only dam_forecast isp_clearing_isc
python tsoc_parse.py tidy    --only dam_forecast isp_clearing_isc
```

That gives you outturn prices, the demand/mix outturn, the day-ahead forecast
to difference against it, and the cleared schedule underneath. Add
`isp_clearing_pbe` + `isp_clearing_pbp` when you want supply curves, and
`settlement_reconciliation` when you need money rather than energy.
