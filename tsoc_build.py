#!/usr/bin/env python3
"""
Build the Parquet layer and the DuckDB catalog.

    python tsoc_build.py            # build everything, run the checks
    python tsoc_build.py --strict   # exit non-zero if any check fails

What this does and, more importantly, what it does not
------------------------------------------------------
It converts the *derived* layer — the stitched series CSVs and the tidy
balancing CSVs — into typed, sorted Parquet, then writes a DuckDB file that
holds **views over those Parquet files**, not copies of them.

`data/raw/` is untouched and stays the authoritative record. Every parser here
has been wrong at least once; the raw workbooks are what makes that
recoverable.

Because the catalog is views rather than tables, re-running a parser and
overwriting a Parquet file makes every view current with no reload step, and
the Parquet stays readable by pandas, Polars, R or anything else. DuckDB is a
convenience on top, not a place your data gets locked into.

The one materialised table is `panel_30min`: the day-ahead, system and
balancing series joined onto the settlement period. That join is expensive and
gets run constantly, so it is worth computing once.

Indexes are deliberately absent. DuckDB is columnar and builds zone maps
automatically, and Parquet carries row-group statistics, so a time filter
already skips whole chunks. Sort order on disk is the lever that matters, and
every file here is written sorted by timestamp.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

import tsoc_data as T

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
PARQUET = DATA / "parquet"
DB = DATA / "warehouse.duckdb"


# --------------------------------------------------------------------------
# collect
# --------------------------------------------------------------------------

def gather() -> dict[str, pd.DataFrame]:
    """Every derived dataset, normalised, keyed by the name it gets in the catalog."""
    out: dict[str, pd.DataFrame] = {}

    for key in T.SERIES_SPECS:
        p = T.SERIES / f"{key}.csv"
        if not p.exists():
            continue
        df = T.load_series(key)
        if not df.empty:
            out[key] = df.reset_index()

    for kind in T.BM_FILES:
        p = T.TIDY / T.BM_FILES[kind]
        if not p.exists():
            continue
        df = T.load_bm(kind)
        if not df.empty:
            out[f"bm_{kind}"] = df.reset_index()

    return out


def build_panel(sets: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """
    The half-hourly panel: day-ahead price, system mix and balancing on one row.

    Everything finer than 30 minutes is averaged up rather than the coarser
    series being interpolated down — the day-ahead price is a single clearing
    outcome per settlement period and inventing values inside it would be a
    fiction. MWh volumes are summed, MW and prices averaged.
    """
    if "dam_prices_volumes" not in sets:
        return pd.DataFrame()

    panel = sets["dam_prices_volumes"].set_index("timestamp").sort_index()

    if "penetration_rates" in sets:
        sysd = sets["penetration_rates"].set_index("timestamp").sort_index()
        panel = panel.join(T.resample(sysd, "30min"), how="left")

    if "bm_energy" in sets:
        bm = sets["bm_energy"].set_index("timestamp").sort_index()
        cols = [c for c in ("price_up", "price_down", "activated_up",
                            "activated_down", "reserves_total")
                if c in bm.columns]
        if cols:
            bm30 = bm[cols].resample("30min").mean().dropna(how="all")
            panel = panel.join(bm30, how="left")
            if "price_up" in panel:
                panel["spread_up"] = panel["price_up"] - panel["dam_price"]
            if "price_down" in panel:
                panel["spread_down"] = panel["price_down"] - panel["dam_price"]

    if "bm_system" in sets:
        s = sets["bm_system"].set_index("timestamp").sort_index()
        cols = [c for c in s.columns if c.endswith(("_price_up", "_price_down"))]
        cols += [c for c in ("expost_load", "offers_up", "offers_down") if c in s]
        if cols:
            # Duplicate timestamps exist on the October DST day; average them
            # for the panel and keep the period-indexed table for exact work.
            agg = s[cols].groupby(level=0).mean(numeric_only=True)
            panel = panel.join(agg, how="left")

    return panel.reset_index()


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def checks(sets: dict[str, pd.DataFrame], panel: pd.DataFrame) -> list[tuple[str, bool, str]]:
    """
    Assertions that make a bad parse loud instead of silent.

    These exist because the two real defects found in this dataset so far — an
    hour vanishing on the October DST day, and 999999 being loaded as a price —
    are both things a warehouse would have ingested happily.
    """
    res: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        res.append((name, ok, detail))

    for key, spec in T.SERIES_SPECS.items():
        if key not in sets:
            continue
        df = sets[key].set_index("timestamp")
        anom = T.day_anomalies(df, spec.freq_minutes)
        real = anom[anom["kind"] != "partial (end of archive)"] if not anom.empty \
            else anom
        check(f"{key}: day lengths",
              real.empty,
              "" if real.empty else
              f"{len(real)} odd day(s), worst {int(real['missing'].abs().max())} intervals")

    if "bm_energy" in sets:
        e = sets["bm_energy"]
        for side in ("up", "down"):
            raw, clean = f"price_{side}_raw", f"price_{side}"
            if raw not in e:
                continue
            share = float(e[clean].isna().mean())
            check(f"bm price_{side}: sentinels excluded",
                  e[clean].max(skipna=True) < 25000 if e[clean].notna().any() else True,
                  f"{share * 100:.1f}% of intervals unpriced")

        dupes = int(e["timestamp"].duplicated().sum())
        has_interval = "interval" in e.columns
        check("bm energy: DST duplicates disambiguated",
              has_interval,
              f"{dupes} repeated timestamp(s); 'interval' column "
              f"{'present' if has_interval else 'MISSING'}")

    if not panel.empty:
        check("panel: day-ahead price present",
              panel["dam_price"].notna().mean() > 0.99,
              f"{panel['dam_price'].notna().mean() * 100:.1f}% populated")
        for col in ("price_up", "res_pct"):
            if col in panel:
                cov = panel[col].notna().mean()
                check(f"panel: {col} coverage", cov > 0,
                      f"{cov * 100:.1f}% of rows")

    return res


# --------------------------------------------------------------------------
# write
# --------------------------------------------------------------------------

def write_parquet(sets: dict[str, pd.DataFrame], panel: pd.DataFrame) -> list[str]:
    PARQUET.mkdir(parents=True, exist_ok=True)
    names = []
    everything = dict(sets)
    if not panel.empty:
        everything["panel_30min"] = panel

    for name, df in everything.items():
        if df.empty:
            continue
        d = df.copy()
        if "timestamp" in d.columns:
            d = d.sort_values("timestamp")          # sort order is the index here
        dest = PARQUET / f"{name}.parquet"
        d.to_parquet(dest, index=False, compression="zstd")
        names.append(name)
        csv_equiv = d.memory_usage(deep=True).sum() / 1e6
        print(f"  {name:<26} {len(d):>9,} rows  "
              f"{dest.stat().st_size / 1e6:>6.1f} MB parquet "
              f"(~{csv_equiv:.0f} MB in memory)")
    return names


def write_catalog(names: list[str]) -> bool:
    try:
        import duckdb
    except ImportError:
        print("\nduckdb not installed — Parquet written, catalog skipped.\n"
              "  pip install duckdb", file=sys.stderr)
        return False

    if DB.exists():
        DB.unlink()          # the catalog is derived; rebuild it outright
    con = duckdb.connect(str(DB))
    for name in names:
        # A view, not a table: the Parquet file stays the single copy, and
        # re-running a parser makes this current with no reload.
        con.execute(
            f'CREATE OR REPLACE VIEW "{name}" AS '
            f"SELECT * FROM read_parquet('{(PARQUET / f'{name}.parquet').as_posix()}')"
        )
    con.close()
    return True


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--strict", action="store_true",
                   help="exit non-zero if any check fails")
    a = p.parse_args()

    print("collecting …")
    sets = gather()
    if not sets:
        print("nothing to build — run tsoc_scrape.py / tsoc_bm.py first",
              file=sys.stderr)
        return 1
    panel = build_panel(sets)

    print("\nwriting parquet …")
    names = write_parquet(sets, panel)

    print("\nchecks:")
    results = checks(sets, panel)
    failed = 0
    for name, ok, detail in results:
        mark = "ok  " if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"  [{mark}] {name}" + (f"  — {detail}" if detail else ""))
    if not results:
        print("  (none applicable)")

    if write_catalog(names):
        print(f"\ncatalog: {DB.relative_to(ROOT)}  "
              f"({len(names)} views over data/parquet/)")
        print("  duckdb data/warehouse.duckdb")
        print("  D SELECT * FROM panel_30min LIMIT 5;")

    if failed:
        print(f"\n{failed} check(s) failed — see above", file=sys.stderr)
        if a.strict:
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
