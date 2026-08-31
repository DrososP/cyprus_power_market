"""
Registry of every scrapable data source on tsoc.org.cy (Cyprus TSO).

Two kinds of source:

  FILE_SOURCES  -- TSOC pages that render a flat list of links to Excel/PDF/CSV
                   files hosted on the public S3 bucket `tso-cy`. Every month
                   of history is rendered on the one page, so a single GET per
                   source is enough to discover the whole archive.

  SERIES_SOURCES -- TSOC pages that render an HTML table of a time series and
                    accept ?startdt=DD-MM-YYYY&enddt=DD-MM-YYYY. There is no
                    export button, so the table has to be paged through in
                    date windows and stitched together.

`key` is used as the folder / CSV name, so keep it filesystem-safe.
See DATA_DICTIONARY.md for what each source actually contains.
"""

BASE = "https://tsoc.org.cy"

# --------------------------------------------------------------------------
# 1. File-listing sources
# --------------------------------------------------------------------------
# s3_prefix is documentation only -- the scraper discovers URLs from the page
# rather than constructing them, because filenames embed an unpredictable
# report-generation timestamp (see DATA_DICTIONARY.md > "Filename anatomy").

FILE_SOURCES = [
    # ---- Forecasts -------------------------------------------------------
    dict(
        key="dam_forecast",
        name="DAM preliminary forecasts (demand, RES injection, reserves)",
        url=f"{BASE}/competitive-electricity-market/forecast-results/dam_forecast/",
        s3_prefix="DAM_FCST",
        group="forecast",
        cadence="daily",
    ),
    dict(
        key="isp_forecast",
        name="Load & RES forecast used in Integrated Scheduling Process",
        url=f"{BASE}/competitive-electricity-market/forecast-results/load_and_res_forecast/",
        s3_prefix="ISP_FCST",
        group="forecast",
        cadence="daily",
    ),

    # ---- Market activity reports ----------------------------------------
    dict(
        key="fm_daily_activity_en",
        name="Forward Market daily activity report (EN)",
        url=f"{BASE}/competitive-electricity-market/mms-reports/forward-market-daily-activity-reports-en/",
        s3_prefix="REP_MO-002",
        group="market",
        cadence="daily",
    ),
    dict(
        key="fm_daily_activity_el",
        name="Forward Market daily activity report (EL)",
        url=f"{BASE}/competitive-electricity-market/mms-reports/forward-market-daily-activity-reports-el/",
        s3_prefix="REP_MO-002",
        group="market",
        cadence="daily",
    ),
    dict(
        key="dam_daily_activity_en",
        name="Day-Ahead Market daily activity report (EN)",
        url=f"{BASE}/competitive-electricity-market/mms-reports/day-ahead-market-daily-activity-reports-en/",
        s3_prefix="REP_MO-003",
        group="market",
        cadence="daily",
    ),
    dict(
        key="dam_daily_activity_el",
        name="Day-Ahead Market daily activity report (EL)",
        url=f"{BASE}/competitive-electricity-market/mms-reports/day-ahead-market-daily-activity-reports-el/",
        s3_prefix="REP_MO-003",
        group="market",
        cadence="daily",
    ),
    dict(
        key="bm_daily_activity_en",
        name="Balancing Market daily activity report (EN)",
        url=f"{BASE}/competitive-electricity-market/mms-reports/balancing-market-daily-activity-reports-en/",
        s3_prefix="REP_TSO-001",
        group="balancing",
        cadence="daily",
    ),
    dict(
        key="bm_daily_activity_el",
        name="Balancing Market daily activity report (EL)",
        url=f"{BASE}/competitive-electricity-market/mms-reports/balancing-market-daily-activity-reports-el/",
        s3_prefix="REP_TSO-001",
        group="balancing",
        cadence="daily",
    ),
    dict(
        key="bm_monthly_activity_en",
        name="Balancing Market monthly activity report (EN)",
        url=f"{BASE}/competitive-electricity-market/mms-reports/balancing-market-monthly-activity-reports-en/",
        s3_prefix="REP_TSO-002",
        group="balancing",
        cadence="monthly",
    ),
    dict(
        key="bm_monthly_activity_el",
        name="Balancing Market monthly activity report (EL)",
        url=f"{BASE}/competitive-electricity-market/mms-reports/balancing-market-monthly-activity-reports-el/",
        s3_prefix="REP_TSO-002",
        group="balancing",
        cadence="monthly",
    ),

    # ---- RTBM ------------------------------------------------------------
    dict(
        key="rtbm_monthly",
        name="Monthly Real-Time Balancing Market data report",
        url=f"{BASE}/competitive-electricity-market/mms-reports/monthly-rtbm-data-reports/",
        s3_prefix="REP_TSO-011",
        group="balancing",
        cadence="monthly",
    ),
    dict(
        key="rtbm_following_month",
        name="Following-month RTBM data report",
        url=f"{BASE}/competitive-electricity-market/mms-reports/following-month-rtbm-data-reports/",
        s3_prefix="REP_TSO-012",
        group="balancing",
        cadence="monthly",
    ),

    # ---- Load / RES forecast reports -------------------------------------
    dict(
        key="true_net_load_res_fcst_en",
        name="True load, net load & RES injection forecast (EN)",
        url=f"{BASE}/competitive-electricity-market/mms-reports/true-load-net-load-res-injection-forecast-reports-en/",
        s3_prefix="REP_TSO-004",
        group="forecast",
        cadence="daily",
    ),
    dict(
        key="true_net_load_res_fcst_el",
        name="True load, net load & RES injection forecast (EL)",
        url=f"{BASE}/competitive-electricity-market/mms-reports/true-load-net-load-res-injection-forecast-reports-el/",
        s3_prefix="REP_TSO-004",
        group="forecast",
        cadence="daily",
    ),

    # ---- ISP forecast + clearing results ---------------------------------
    dict(
        key="isp_forecasted_data",
        name="ISP forecasted data report",
        url=f"{BASE}/competitive-electricity-market/mms-reports/isp-forecasted-data-reports/",
        s3_prefix="REP_TSO-008",
        group="isp",
        cadence="daily",
    ),
    dict(
        key="isp_clearing_com",
        name="ISP clearing results - commitment schedule (COM)",
        url=f"{BASE}/competitive-electricity-market/mms-reports/isp-clearing-results-reports-com/",
        s3_prefix="REP_TSO-018-COM",
        group="isp",
        cadence="daily",
    ),
    dict(
        key="isp_clearing_con",
        name="ISP clearing results - constraints (CON)",
        url=f"{BASE}/competitive-electricity-market/mms-reports/isp-clearing-results-reports-con/",
        s3_prefix="REP_TSO-018-CON",
        group="isp",
        cadence="daily",
    ),
    dict(
        key="isp_clearing_ids",
        name="ISP clearing results - intraday schedule (IDS)",
        url=f"{BASE}/competitive-electricity-market/mms-reports/isp-clearing-results-reports-ids/",
        s3_prefix="REP_TSO-018-IDS",
        group="isp",
        cadence="daily",
    ),
    dict(
        key="isp_clearing_isc",
        name="ISP clearing results - ISP schedule (ISC)",
        url=f"{BASE}/competitive-electricity-market/mms-reports/isp-clearing-results-reports-isc/",
        s3_prefix="REP_TSO-018-ISC",
        group="isp",
        cadence="daily",
    ),
    dict(
        key="isp_clearing_mrp",
        name="ISP clearing results - marginal reserve prices (MRP)",
        url=f"{BASE}/competitive-electricity-market/mms-reports/isp-clearing-results-reports-mrp/",
        s3_prefix="REP_TSO-018-MRP",
        group="isp",
        cadence="daily",
    ),
    dict(
        key="isp_clearing_pbe",
        name="ISP clearing results - price band energy (PBE)",
        url=f"{BASE}/competitive-electricity-market/mms-reports/isp-clearing-results-reports-pbe/",
        s3_prefix="REP_TSO-018-PBE",
        group="isp",
        cadence="daily",
    ),
    dict(
        key="isp_clearing_pbp",
        name="ISP clearing results - price band prices (PBP)",
        url=f"{BASE}/competitive-electricity-market/mms-reports/isp-clearing-results-reports-pbp/",
        s3_prefix="REP_TSO-018-PBP",
        group="isp",
        cadence="daily",
    ),
    dict(
        key="isp_clearing_rca",
        name="ISP clearing results - reserve capacity allocation (RCA)",
        url=f"{BASE}/competitive-electricity-market/mms-reports/isp-clearing-results-reports-rca/",
        s3_prefix="REP_TSO-018-RCA",
        group="isp",
        cadence="daily",
    ),

    # ---- ISP balancing data ----------------------------------------------
    dict(
        key="isp_balancing_bdl",
        name="BDL - ISP balancing data report (load side)",
        url=f"{BASE}/competitive-electricity-market/mms-reports/bdl-isp-balancing-data-reports/",
        s3_prefix="REP_TSO-009-BDL",
        group="balancing",
        cadence="daily",
    ),
    dict(
        key="isp_balancing_bdr",
        name="BDR - ISP balancing data report (resource side)",
        url=f"{BASE}/competitive-electricity-market/mms-reports/bdr-isp-balancing-data-reports/",
        s3_prefix="REP_TSO-009-BDR",
        group="balancing",
        cadence="daily",
    ),

    # ---- Auctions --------------------------------------------------------
    dict(
        key="auction_spec_black_start",
        name="Black Start auction specifications",
        url=f"{BASE}/competitive-electricity-market/mms-reports/black-start-auction-specifications/",
        s3_prefix="REP_TSO-013-BS",
        group="auction",
        cadence="irregular",
    ),
    dict(
        key="auction_spec_contingency_reserve",
        name="Contingency Reserve auction specifications",
        url=f"{BASE}/competitive-electricity-market/mms-reports/contingency-reserve-auction-specifications/",
        s3_prefix="REP_TSO-013-CR",
        group="auction",
        cadence="irregular",
    ),
    dict(
        key="auction_spec_replacement_reserve",
        name="Replacement Reserve auction specifications",
        url=f"{BASE}/competitive-electricity-market/mms-reports/replacement-reserve-auction-specifications/",
        s3_prefix="REP_TSO-013-RR",
        group="auction",
        cadence="irregular",
    ),
    dict(
        key="auction_results_bs",
        name="Auction results - Black Start",
        url=f"{BASE}/competitive-electricity-market/mms-reports/auction-results-reports-bs/",
        s3_prefix="REP_TSO-014-BS",
        group="auction",
        cadence="irregular",
    ),
    dict(
        key="auction_results_cr",
        name="Auction results - Contingency Reserve",
        url=f"{BASE}/competitive-electricity-market/mms-reports/auction-results-reports-cr/",
        s3_prefix="REP_TSO-014-CR",
        group="auction",
        cadence="irregular",
    ),
    dict(
        key="auction_results_rr",
        name="Auction results - Replacement Reserve",
        url=f"{BASE}/competitive-electricity-market/mms-reports/auction-results-reports-rr/",
        s3_prefix="REP_TSO-014-RR",
        group="auction",
        cadence="irregular",
    ),
    dict(
        key="must_run_results",
        name="Must-Run units auction results",
        url=f"{BASE}/competitive-electricity-market/must-run-results/",
        s3_prefix="Market/MustRunUnitsAuction",
        group="auction",
        cadence="monthly",
    ),

    # ---- Registry / availability ----------------------------------------
    dict(
        key="party_list",
        name="Registered market participants (party list)",
        url=f"{BASE}/competitive-electricity-market/mms-reports/party-list/",
        s3_prefix="REP_MO-001",
        group="reference",
        cadence="irregular",
    ),
    dict(
        key="non_availability_en",
        name="Non-availability declarations of resource objects (EN)",
        url=f"{BASE}/competitive-electricity-market/mms-reports/non-availability-of-resource-objects-en/",
        s3_prefix="REP_TSO-006",
        group="availability",
        cadence="daily",
    ),
    dict(
        key="non_availability_el",
        name="Non-availability declarations of resource objects (EL)",
        url=f"{BASE}/competitive-electricity-market/mms-reports/non-availability-of-resource-objects-el/",
        s3_prefix="REP_TSO-006",
        group="availability",
        cadence="daily",
    ),

    # ---- Settlement ------------------------------------------------------
    dict(
        key="settlement_calendars",
        name="Settlement calendars",
        url=f"{BASE}/competitive-electricity-market/data-info/settl-calendars/",
        s3_prefix="Market/Calendars",
        group="settlement",
        cadence="annual",
    ),
    dict(
        key="settlement_monthly",
        name="Monthly settlement results",
        url=f"{BASE}/competitive-electricity-market/data-info/monthly-settlement/",
        s3_prefix="Market/MonthlySettlement",
        group="settlement",
        cadence="monthly",
    ),
    dict(
        key="settlement_aggregate",
        name="Cumulative / aggregated settlement results",
        url=f"{BASE}/competitive-electricity-market/data-info/agg-settlement/",
        s3_prefix="Market/AggSettlement",
        group="settlement",
        cadence="monthly",
    ),
    dict(
        key="settlement_reconciliation",
        name="Reconciliation settlement calculations",
        url=f"{BASE}/competitive-electricity-market/data-info/reconciliation/",
        s3_prefix="Market/RecSettlement",
        group="settlement",
        cadence="monthly",
    ),

    # ---- Wider system data ----------------------------------------------
    dict(
        key="res_curtailments_reports",
        name="RES curtailment reports",
        url=f"{BASE}/information/res-curtailments-reports/",
        s3_prefix=None,
        group="res",
        cadence="irregular",
    ),
    dict(
        key="res_curtailments_monthly",
        name="RES curtailments - monthly",
        url=f"{BASE}/information/res-curtailments-monthly/",
        s3_prefix=None,
        group="res",
        cadence="monthly",
    ),
    dict(
        key="res_curtailments_annual",
        name="RES curtailments - annual",
        url=f"{BASE}/information/res-curtailments-annual/",
        s3_prefix=None,
        group="res",
        cadence="annual",
    ),
    dict(
        key="power_energy_marginal_cost",
        name="Avoided power & energy marginal cost",
        url=f"{BASE}/information/power-energy-marginal-cost/",
        s3_prefix=None,
        group="reference",
        cadence="irregular",
    ),
    dict(
        key="metering_data_monthly",
        name="Metering data monthly reports",
        url=f"{BASE}/electrical-system/metering-data-monthly-reports/",
        s3_prefix=None,
        group="system",
        cadence="monthly",
    ),
    dict(
        key="energy_generation_records",
        name="Energy generation records (peaks / milestones)",
        url=f"{BASE}/electrical-system/energy-generation-records/",
        s3_prefix=None,
        group="system",
        cadence="irregular",
    ),
    dict(
        key="guarantees_of_origin",
        name="Guarantees of origin of generation",
        url=f"{BASE}/electricity-market/guarantess-of-origin-of-generation/",
        s3_prefix=None,
        group="reference",
        cadence="irregular",
    ),
    dict(
        key="supplier_energy_mix",
        name="Supplier energy mix disclosure",
        url=f"{BASE}/electricity-market/supplier-energy-mix-disclosure-2/",
        s3_prefix=None,
        group="reference",
        cadence="annual",
    ),
    dict(
        key="long_term_forecast",
        name="Long-term generation adequacy forecast",
        url=f"{BASE}/electrical-system/electrical-energy-generation/long-term-forecast/",
        s3_prefix=None,
        group="forecast",
        cadence="annual",
    ),
]


# --------------------------------------------------------------------------
# 2. Paged HTML time-series sources
# --------------------------------------------------------------------------
# `window_days` is the largest range the page reliably renders in one request.
# `freq_minutes` documents the native resolution of the published series.

SERIES_SOURCES = [
    dict(
        key="dam_prices_volumes",
        name="Day-Ahead Market clearing prices and cleared volumes",
        url=f"{BASE}/competitive-electricity-market/dam-volume-prices-graph/",
        window_days=7,
        freq_minutes=30,
        group="market",
    ),
    dict(
        key="penetration_rates",
        name="System demand and generation mix with RES penetration %",
        url=f"{BASE}/electrical-system/archive-penetration-rates/",
        window_days=7,
        freq_minutes=15,
        group="system",
    ),
    dict(
        key="wind_solar_generation",
        name="Wind farm output and estimated distributed solar generation",
        url=f"{BASE}/electrical-system/archive-total-daily-wind-and-solar-farm-generation/",
        window_days=7,
        freq_minutes=15,
        group="system",
    ),
    dict(
        key="system_generation",
        name="Total daily system generation on the transmission system",
        url=f"{BASE}/electrical-system/archive-total-daily-system-generation-on-the-transmission-system/",
        window_days=7,
        freq_minutes=15,
        group="system",
    ),
    dict(
        key="available_capacity",
        name="Daily available conventional generation capacity",
        url=f"{BASE}/electrical-system/daily-available-capacity/",
        window_days=7,
        freq_minutes=15,
        group="system",
    ),
]


FILE_SOURCES_BY_KEY = {s["key"]: s for s in FILE_SOURCES}
SERIES_SOURCES_BY_KEY = {s["key"]: s for s in SERIES_SOURCES}
ALL_KEYS = list(FILE_SOURCES_BY_KEY) + list(SERIES_SOURCES_BY_KEY)
