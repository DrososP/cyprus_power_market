"""
Loading and normalisation layer for the TSOC dashboard.

The scraped series CSVs come off tsoc.org.cy with Greek headers, naive local
timestamps and mixed resolutions (30-minute market data, 15-minute system
data). Everything in this module exists to turn that into English-labelled,
timestamp-indexed frames that can be resampled without silently corrupting the
numbers.

Two rules are enforced here rather than left to the caller:

  * intensive quantities (prices in EUR/MWh, power in MW) are averaged when
    resampled; extensive ones (energy in MWh) are summed. Averaging MWh across
    two half-hours halves your volumes.
  * percentage columns published by TSOC are never averaged. They are
    recomputed from the MW columns after resampling, which is the only way to
    get a correct share of a longer interval.

See DATA_DICTIONARY.md for what the underlying fields mean.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent / "data"
SERIES = DATA / "series"
PARQUET = DATA / "parquet"


def _source(name: str, csv: Path) -> tuple[Path, bool]:
    """
    Prefer the Parquet build when it is at least as fresh as the CSV.

    Typed, sorted and already normalised, so reading it skips the column
    translation entirely. The freshness guard matters: a Parquet file older
    than its CSV means a parser has run since the last `tsoc_build.py`, and
    silently serving the stale copy is exactly the failure this whole layer is
    supposed to prevent.
    """
    pq = PARQUET / f"{name}.parquet"
    if pq.exists() and (not csv.exists()
                        or pq.stat().st_mtime >= csv.stat().st_mtime):
        return pq, True
    return csv, False

TZ = "Europe/Nicosia"  # documentation only — frames stay naive local, as published


# --------------------------------------------------------------------------
# column translation
# --------------------------------------------------------------------------
# Keyed on the Greek header exactly as the scraper writes it. Unknown columns
# are kept under their original name rather than dropped, so a header change
# upstream shows up in the app instead of vanishing.

COLUMN_MAP: dict[str, str] = {
    # dam_prices_volumes
    "Τιμές Εκκαθάρισης Προ-Ημερήσιας Αγοράς (€/MWh)": "dam_price",
    "Εκκαθαρισθείσες Ποσότητες Ενέργειας (MWh)": "cleared_volume",
    # penetration_rates
    "Συνολική Ζήτηση (MW)": "demand",
    "Συμβατική Παραγωγή (MW)": "conventional",
    "Ποσοστό Συμβατικής Παραγωγής (%)": "conventional_pct",
    "Εκτίμηση Διεσπαρμένης Παραγωγής από ΦΒ και Βιομάζα (MW)": "distributed_pv",
    "Ποσοστό Εκτίμησης Διεσπαρμένης Παραγωγής από ΦΒ και Βιομάζα (%)": "distributed_pv_pct",
    "Αιολική Παραγωγή (MW)": "wind",
    "Ποσοστό Αιολικής Παραγωγής (%)": "wind_pct",
    # wind_solar_generation
    "Αιολική Παραγωγή": "wind_tx",
    "Εκτίμηση Διεσπαρμένης Παραγωγής": "distributed_solar",
}

# Human labels and units for anything the app puts on an axis or in a legend.
LABELS: dict[str, str] = {
    "dam_price": "Day-ahead clearing price",
    "cleared_volume": "Cleared volume",
    "demand": "Total system demand",
    "conventional": "Conventional generation",
    "conventional_pct": "Conventional share",
    "distributed_pv": "Distributed PV & biomass (est.)",
    "distributed_pv_pct": "Distributed PV & biomass share",
    "wind": "Wind generation",
    "wind_pct": "Wind share",
    "wind_tx": "Wind (transmission-connected)",
    "distributed_solar": "Distributed solar (est.)",
    "res": "Total RES",
    "res_pct": "RES share",
    "net_load": "Net load (demand − RES)",
    "vwap": "Volume-weighted price",
    # balancing market (tsoc_bm.py output)
    "price_up": "Balancing price, up",
    "price_down": "Balancing price, down",
    "activated_up": "Activated balancing energy, up",
    "activated_down": "Activated balancing energy, down",
    "reserves_total": "Total available reserves",
    "expost_load": "Ex-post system load",
    "gen_oil": "Oil", "gen_gas": "Gas", "gen_solar": "Solar",
    "gen_wind": "Wind", "gen_biomass": "Biomass",
    "offers_up": "Aggregated offers, up",
    "offers_down": "Aggregated offers, down",
    "fcr_price_up": "FCR up", "fcr_price_down": "FCR down",
    "afrr_price_up": "aFRR up", "afrr_price_down": "aFRR down",
    "mfrr_price_up": "mFRR up", "mfrr_price_down": "mFRR down",
    "rr_price_up": "RR up", "rr_price_down": "RR down",
    "spread_up": "Balancing up − day-ahead",
    "spread_down": "Balancing down − day-ahead",
}

UNITS: dict[str, str] = {
    "dam_price": "EUR/MWh",
    "vwap": "EUR/MWh",
    "cleared_volume": "MWh",
    "demand": "MW",
    "conventional": "MW",
    "distributed_pv": "MW",
    "wind": "MW",
    "wind_tx": "MW",
    "distributed_solar": "MW",
    "res": "MW",
    "net_load": "MW",
    "conventional_pct": "%",
    "distributed_pv_pct": "%",
    "wind_pct": "%",
    "res_pct": "%",
    # balancing market. Nothing in REP_TSO-001 states a unit; these are
    # inferred from magnitude and market context — see tsoc_bm.py.
    "price_up": "EUR/MWh", "price_down": "EUR/MWh",
    "spread_up": "EUR/MWh", "spread_down": "EUR/MWh",
    "activated_up": "MW", "activated_down": "MW",
    "reserves_total": "MW", "expost_load": "MW",
    "gen_oil": "MW", "gen_gas": "MW", "gen_solar": "MW",
    "gen_wind": "MW", "gen_biomass": "MW",
    "offers_up": "MW", "offers_down": "MW",
    "fcr_price_up": "EUR/MW", "fcr_price_down": "EUR/MW",
    "afrr_price_up": "EUR/MW", "afrr_price_down": "EUR/MW",
    "mfrr_price_up": "EUR/MW", "mfrr_price_down": "EUR/MW",
    "rr_price_up": "EUR/MW", "rr_price_down": "EUR/MW",
}

# How each column behaves under resampling.
#   mean  -> intensive (price, power)
#   sum   -> extensive (energy)
#   derived -> never aggregated directly; recomputed from MW after resampling
AGG: dict[str, str] = {
    "dam_price": "mean",
    "cleared_volume": "sum",
    "demand": "mean",
    "conventional": "mean",
    "distributed_pv": "mean",
    "wind": "mean",
    "wind_tx": "mean",
    "distributed_solar": "mean",
    "res": "mean",
    "net_load": "mean",
    "conventional_pct": "derived",
    "distributed_pv_pct": "derived",
    "wind_pct": "derived",
    "res_pct": "derived",
}


@dataclass(frozen=True)
class SeriesSpec:
    key: str
    title: str
    freq_minutes: int
    note: str = ""
    derived: tuple[str, ...] = field(default=())


SERIES_SPECS: dict[str, SeriesSpec] = {
    "dam_prices_volumes": SeriesSpec(
        key="dam_prices_volumes",
        title="Day-ahead market prices & volumes",
        freq_minutes=30,
        note="The headline price series. Half-hourly settlement periods.",
    ),
    "penetration_rates": SeriesSpec(
        key="penetration_rates",
        title="Demand & generation mix",
        freq_minutes=15,
        note=(
            "Demand, the full mix and RES share in one table. Distributed PV is "
            "an estimate — rooftop solar is not individually metered."
        ),
        derived=("res", "res_pct", "net_load"),
    ),
    "wind_solar_generation": SeriesSpec(
        key="wind_solar_generation",
        title="Wind & solar detail",
        freq_minutes=15,
        note="Transmission-connected wind only, plus estimated distributed solar.",
    ),
}


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def series_path(key: str) -> Path:
    """Whichever copy is authoritative right now — Parquet if built, else CSV."""
    return _source(key, SERIES / f"{key}.csv")[0]


def available_series() -> list[str]:
    return [k for k in SERIES_SPECS
            if (SERIES / f"{k}.csv").exists()
            or (PARQUET / f"{k}.parquet").exists()]


def file_signature(key: str) -> tuple[float, int]:
    """(mtime, size) of the file actually read, so a rebuild invalidates cache."""
    p = series_path(key)
    if not p.exists():
        return (0.0, 0)
    st = p.stat()
    return (st.st_mtime, st.st_size)


def load_series(key: str) -> pd.DataFrame:
    """
    One scraped series as a timestamp-indexed frame with English column names.

    Rows where every value column is empty are dropped — the scraper writes a
    full grid to the end of the last requested window, so the tail of a fresh
    scrape is usually blank.
    """
    path, is_parquet = _source(key, SERIES / f"{key}.csv")
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run:  python tsoc_scrape.py series --only {key}"
        )

    if is_parquet:
        # Already normalised at build time — columns translated, derivatives
        # computed. Re-running any of that would be wasted work.
        df = pd.read_parquet(path)
        return df.set_index("timestamp").sort_index()

    df = pd.read_csv(path, encoding="utf-8-sig")
    ts_col = df.columns[0]
    df[ts_col] = pd.to_datetime(df[ts_col], errors="coerce")
    df = df.dropna(subset=[ts_col])

    df = df.rename(columns={ts_col: "timestamp", **COLUMN_MAP})
    df = df.set_index("timestamp").sort_index()

    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.dropna(how="all")

    spec = SERIES_SPECS[key]
    if "res" in spec.derived:
        df = add_mix_derivatives(df)
    return df


def add_mix_derivatives(df: pd.DataFrame) -> pd.DataFrame:
    """RES, RES share and net load, from the penetration-rates columns."""
    df = df.copy()
    if {"wind", "distributed_pv"} <= set(df.columns):
        df["res"] = df["wind"] + df["distributed_pv"]
        if "demand" in df.columns:
            df["net_load"] = df["demand"] - df["res"]
            df["res_pct"] = (df["res"] / df["demand"].replace(0, pd.NA)) * 100
    return df


# --------------------------------------------------------------------------
# resampling
# --------------------------------------------------------------------------

FREQ_CHOICES: dict[str, str | None] = {
    "Native": None,
    "Hourly": "h",
    "Daily": "D",
    "Weekly": "W-MON",
    "Monthly": "MS",
}


# Index-like columns that must never be averaged into a meaningless number.
NON_VALUE = {"period", "interval", "trade_date", "source_file", "sheet",
             "unit", "variable", "product", "direction"}


def resample(df: pd.DataFrame, rule: str | None) -> pd.DataFrame:
    """
    Aggregate to a coarser interval, respecting intensive vs extensive columns.

    Percentage columns are recomputed from the resampled MW columns rather than
    averaged: the mean of a share is not the share of the mean unless every
    interval carries identical demand. Index and label columns are dropped
    rather than aggregated — the mean of a settlement-period number is noise.
    """
    if rule is None or df.empty:
        return df

    numeric = [c for c in df.columns
               if c not in NON_VALUE and pd.api.types.is_numeric_dtype(df[c])]
    if not numeric:
        return df
    df = df[numeric]

    how = {c: AGG.get(c, "mean") for c in df.columns}
    direct = {c: h for c, h in how.items() if h in ("mean", "sum")}
    if not direct:
        return df

    out = df.resample(rule).agg(direct)

    if "demand" in out.columns:
        denom = out["demand"].replace(0, pd.NA)
        for mw, pct in (
            ("conventional", "conventional_pct"),
            ("distributed_pv", "distributed_pv_pct"),
            ("wind", "wind_pct"),
            ("res", "res_pct"),
        ):
            if mw in out.columns and pct in df.columns:
                out[pct] = out[mw] / denom * 100

    return out.dropna(how="all")


def with_gaps(df: pd.DataFrame) -> pd.DataFrame:
    """
    Reindex onto a regular grid so that missing intervals become NaN.

    For plotting only. Without this a line chart joins the last point before a
    gap straight to the first point after it, drawing a clean trend through
    days that were never published — the most confident-looking way a chart can
    lie. Plotly leaves NaN unconnected, so the gap shows as a gap.

    Duplicate timestamps (the October DST hour) are averaged first, because a
    chart cannot render two points at one x position anyway. Use the unmodified
    frame for any arithmetic.
    """
    if df.empty or len(df) < 3:
        return df
    if df.index.has_duplicates:
        df = df.groupby(level=0).mean(numeric_only=True)
    step = pd.Series(df.index).diff().median()
    if pd.isna(step) or step <= pd.Timedelta(0):
        return df
    full = pd.date_range(df.index.min(), df.index.max(), freq=step)
    # Only worth doing if it actually reveals something.
    if len(full) <= len(df):
        return df
    return df.reindex(full)


def vwap(prices: pd.Series, volumes: pd.Series) -> float | None:
    """Volume-weighted average price. The honest average for a price series."""
    d = pd.concat([prices, volumes], axis=1).dropna()
    if d.empty or d.iloc[:, 1].sum() == 0:
        return None
    return float((d.iloc[:, 0] * d.iloc[:, 1]).sum() / d.iloc[:, 1].sum())


def slice_range(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """Inclusive date slice; `end` covers the whole of that day."""
    lo = pd.Timestamp(start)
    hi = pd.Timestamp(end) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    return df.loc[lo:hi]


def join_market_system(
    market: pd.DataFrame, system: pd.DataFrame
) -> pd.DataFrame:
    """
    Put the 30-minute price series and the 15-minute system series on one index.

    The system data is averaged up to 30 minutes rather than the price being
    interpolated down — the price is a discrete clearing outcome per settlement
    period and inventing intra-period values would be a fiction.
    """
    if market.empty or system.empty:
        return pd.DataFrame()
    sys30 = resample(system, "30min")
    return market.join(sys30, how="inner").dropna(how="all")


# --------------------------------------------------------------------------
# balancing market  (produced by tsoc_bm.py)
# --------------------------------------------------------------------------

TIDY = DATA / "tidy"

BM_FILES = {
    "energy": "bm_energy_5min.csv",       # 5-minute BM price + activated energy
    "system": "bm_system_30min.csv",      # half-hourly load, mix, reserve prices
    "bsp": "bm_bsp_results.csv",          # per-unit reserve awards, long
    "deviation": "bdl_system_deviation.csv",
}


def bm_path(kind: str) -> Path:
    return _source(f"bm_{kind}", TIDY / BM_FILES[kind])[0]


def bm_available() -> list[str]:
    return [k for k in BM_FILES
            if (TIDY / BM_FILES[k]).exists()
            or (PARQUET / f"bm_{k}.parquet").exists()]


def bm_signature(kind: str) -> tuple[float, int]:
    p = bm_path(kind)
    if not p.exists():
        return (0.0, 0)
    st = p.stat()
    return (st.st_mtime, st.st_size)


def load_bm(kind: str) -> pd.DataFrame:
    """
    One tidy balancing-market CSV, timestamp-indexed.

    The index is deliberately NOT unique: on the October fall-back day the
    published files really do repeat two wall-clock timestamps, and the
    `period` / `interval` column is what distinguishes them. Both are kept.
    """
    path, is_parquet = _source(f"bm_{kind}", TIDY / BM_FILES[kind])
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run:  python tsoc_bm.py parse"
        )
    df = pd.read_parquet(path) if is_parquet \
        else pd.read_csv(path, encoding="utf-8-sig")
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.dropna(subset=["timestamp"]).set_index("timestamp").sort_index()
    return df


def sentinel_share(energy: pd.DataFrame) -> dict[str, float]:
    """
    Fraction of intervals where the published balancing price is a marker
    rather than a price. High shares mean the mean below it is thin.
    """
    out = {}
    for side in ("up", "down"):
        raw, clean = f"price_{side}_raw", f"price_{side}"
        if raw in energy.columns and len(energy):
            out[side] = float(energy[clean].isna().mean()
                              if clean in energy else 0.0)
    return out


def bm_vs_dam(energy: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    """
    Put the 5-minute balancing price and the 30-minute day-ahead price on one
    index, and difference them.

    The balancing price is averaged up to the settlement period rather than
    the day-ahead price being spread down: the day-ahead price is one number
    per period by definition, and the two prices are only comparable at the
    resolution the slower one is actually set at.
    """
    if energy.empty or market.empty:
        return pd.DataFrame()
    cols = [c for c in ("price_up", "price_down", "activated_up",
                        "activated_down") if c in energy.columns]
    if not cols:
        return pd.DataFrame()
    # dropna after resampling: a sparse archive would otherwise be padded with
    # empty half-hours across every gap, and those would join happily to real
    # day-ahead rows and look like coverage that isn't there.
    bm30 = energy[cols].resample("30min").mean().dropna(how="all")
    out = market.join(bm30, how="inner")
    if "dam_price" in out:
        if "price_up" in out:
            out["spread_up"] = out["price_up"] - out["dam_price"]
        if "price_down" in out:
            out["spread_down"] = out["price_down"] - out["dam_price"]
    return out.dropna(how="all")


# --------------------------------------------------------------------------
# data quality
# --------------------------------------------------------------------------

def coverage(df: pd.DataFrame, freq_minutes: int) -> dict:
    """
    Describe how complete a series is over its own span.

    `expected` counts the intervals a gap-free series would hold between the
    first and last timestamp. DST days legitimately differ from 48/96 periods —
    the March day is short and the October day is long — so a small shortfall
    around a transition is expected, not a bug. See `dst_days`.
    """
    if df.empty:
        return dict(rows=0, first=None, last=None, expected=0, missing=0, pct=0.0)

    first, last = df.index.min(), df.index.max()
    span = (last - first).total_seconds() / 60
    expected = int(span / freq_minutes) + 1
    rows = len(df)
    return dict(
        rows=rows,
        first=first,
        last=last,
        expected=expected,
        missing=max(expected - rows, 0),
        pct=100.0 * rows / expected if expected else 0.0,
    )


def periods_per_day(df: pd.DataFrame) -> pd.Series:
    return df.groupby(df.index.date).size()


def transition_days(first: pd.Timestamp, last: pd.Timestamp) -> dict[date, str]:
    """
    The Cyprus DST transition days inside a span: last Sunday of March
    (spring forward) and of October (fall back).
    """
    out: dict[date, str] = {}
    for year in range(first.year, last.year + 1):
        for month, kind in ((3, "DST spring forward"), (10, "DST fall back")):
            d = pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)
            while d.dayofweek != 6:
                d -= pd.Timedelta(days=1)
            if first.normalize() <= d <= last.normalize():
                out[d.date()] = kind
    return out


def day_anomalies(df: pd.DataFrame, freq_minutes: int) -> pd.DataFrame:
    """
    Every day whose interval count is not what it should be — including the two
    DST days, which are checked explicitly rather than only when they stand out.

    Cyprus clocks change on the last Sunday of March and October. A correct
    archive holds n−(60/f) intervals on the March day and n+(60/f) on the
    October one. The October day is the one that bites: the repeated wall-clock
    hour de-duplicates against itself, so a scraper keyed on the timestamp
    string quietly stores a *normal-length* day and loses an hour of history.
    That failure is invisible unless you look for it, which is what this does.

    The final day of the archive is reported separately as partial rather than
    as a fault — it is simply where the last scrape stopped.
    """
    counts = periods_per_day(df)
    if counts.empty:
        return pd.DataFrame()

    normal = int(counts.mode().iloc[0])
    step = int(60 / freq_minutes)
    transitions = transition_days(df.index.min(), df.index.max())
    last_day = df.index.max().date()

    rows = []
    for day, n in counts.items():
        n = int(n)
        kind = transitions.get(day)
        if kind == "DST spring forward":
            expected = normal - step
        elif kind == "DST fall back":
            expected = normal + step
        else:
            expected, kind = normal, "gap"
        if day == last_day and n < expected:
            expected, kind = n, "partial (end of archive)"
        if n == expected:
            continue
        rows.append(
            dict(day=day, intervals=n, expected=expected, kind=kind,
                 missing=expected - n)
        )
    return pd.DataFrame(rows).sort_values("day") if rows else pd.DataFrame()


def gaps(df: pd.DataFrame, freq_minutes: int, top: int = 20) -> pd.DataFrame:
    """The largest holes in a series, longest first."""
    if len(df) < 2:
        return pd.DataFrame()
    idx = pd.Series(df.index)
    delta = idx.diff()
    step = pd.Timedelta(minutes=freq_minutes)
    big = delta[delta > step]
    if big.empty:
        return pd.DataFrame()
    out = pd.DataFrame(
        dict(
            gap_starts=idx[big.index - 1].values,
            resumes=idx[big.index].values,
            missing_intervals=(big / step - 1).round().astype(int).values,
        )
    )
    return out.sort_values("missing_intervals", ascending=False).head(top)
