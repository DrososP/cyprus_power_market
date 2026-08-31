#!/usr/bin/env python3
"""
Parser for the Cyprus balancing market reports.

    python tsoc_bm.py inspect          # show the section layout of one file
    python tsoc_bm.py parse            # parse everything into data/tidy/
    python tsoc_bm.py parse --full-reserves   # also emit per-unit available reserves

Why this exists separately from `tsoc_parse.py`
-----------------------------------------------
`REP_TSO-001` (the balancing market daily activity report) is **transposed**:
time runs left-to-right across the columns and variables run top-to-bottom in
stacked blocks. `tsoc_parse.py` assumes the opposite — a header row with a time
column underneath — so it cannot read these files at all. Rather than bend the
generic parser into a shape that would make it worse at the reports it does
handle, this module reads the transposed layout directly.

The layout, confirmed against files from Sep 2025 through Aug 2026:

    sheet PT30M — 48 half-hourly settlement periods (46 / 50 on DST days)
      Expost System Load                  Cyprus
      Actual Generation per Technology    Oil, Gas, Solar, Wind, Biomass
      Aggregated Balancing Energy Offers  Up, Down
      FCR  Marginal Price / BSP Results   Up, Down / <unit> Up, <unit> Down
      aFRR Marginal Price / BSP Results   ditto
      mFRR Marginal Price / BSP Results   ditto
      RR   Marginal Price / BSP Results   ditto

    sheet PT5M — 288 five-minute intervals (276 / 300 on DST days)
      Activated Balancing Energy          Up, Down
      Balancing Energy Marginal Price     Up, Down     <-- the BM price
      Available Reserves                  <unit> …, Total

A section is identified by its first data cell being a datetime: that row is
the time axis for the block beneath it. The number of rows inside the BSP
Results blocks varies with how many units participated, so blocks are located
by scanning rather than by fixed row offsets.

Two things that will silently corrupt an analysis if ignored
------------------------------------------------------------
1. **Sentinel prices.** `999999` and `25000` appear in the balancing energy
   marginal price. `999999` is an unpriced / no-offer marker, not a price;
   `25000` sits at the technical cap. Both are excluded from the cleaned
   `price_*` columns and preserved in `price_*_raw`, so nothing is thrown away
   and no mean is destroyed.
2. **The October DST day genuinely repeats two timestamps.** Unlike the scraped
   HTML series, these files DO publish all 50 periods on the fall-back day —
   03:00 and 03:30 each appear twice. Every output therefore carries an
   `interval` / `period` index alongside the timestamp. Key on that, never on
   the timestamp alone, or you will drop an hour of history each October.

Units are not published anywhere in the workbook. Those recorded here are
inferred from magnitude and market context — MW for power, EUR/MWh for the
energy price, EUR/MW for reserve capacity prices. Confirm against the Trading
& Settlement Rules before quoting them formally.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import warnings
from pathlib import Path

import pandas as pd

# TSOC's workbooks carry no default cell style, so openpyxl warns once per
# file. Over 300 files that buries the actual output.
warnings.filterwarnings("ignore", message="Workbook contains no default style")

DATA = Path(__file__).resolve().parent / "data"
RAW = DATA / "raw"
TIDY = DATA / "tidy"

TSO001 = "bm_daily_activity_en"
BDL = "isp_balancing_bdl"

# Values that are markers, not prices. Kept in the *_raw columns.
SENTINELS = {999999.0, 25000.0}

FNAME_DATE = re.compile(r"(?<!\d)((?:20)\d{6})(?!\d)")


def trade_date_from(name: str) -> str | None:
    m = FNAME_DATE.search(name)
    if not m:
        return None
    try:
        return dt.datetime.strptime(m.group(1), "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


# --------------------------------------------------------------------------
# transposed-block reader
# --------------------------------------------------------------------------

def iter_sections(df: pd.DataFrame):
    """
    Yield (section_name, timestamps, [(row_label, values), …]) for each block.

    A row whose second cell is a datetime starts a section: that row is the
    block's time axis, and every row beneath it until the next such row is a
    data series.
    """
    def is_axis(i: int) -> bool:
        if df.shape[1] < 2:
            return False
        return isinstance(df.iat[i, 1], dt.datetime)

    starts = [i for i in range(len(df)) if is_axis(i)]
    for n, i in enumerate(starts):
        stop = starts[n + 1] if n + 1 < len(starts) else len(df)
        stamps = [v for v in df.iloc[i, 1:] if isinstance(v, dt.datetime)]
        if not stamps:
            continue
        rows = []
        for r in range(i + 1, stop):
            label = df.iat[r, 0]
            if label is None or (isinstance(label, float) and pd.isna(label)):
                continue
            rows.append((str(label).strip(),
                         list(df.iloc[r, 1:1 + len(stamps)])))
        yield str(df.iat[i, 0]).strip(), stamps, rows


def num(v):
    """Cell -> float, or None. Blank strings and text become None."""
    if v is None or isinstance(v, dt.datetime):
        return None
    if isinstance(v, (int, float)):
        return None if pd.isna(v) else float(v)
    s = str(v).strip().replace(",", "")
    if not s or s in {"-", "nan", "None"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# REP_TSO-001
# --------------------------------------------------------------------------

# section -> {row label: output column}
HALF_HOURLY = {
    "Expost System Load": {"Cyprus": "expost_load"},
    "Actual Generation per Technology": {
        "Oil": "gen_oil", "Gas": "gen_gas", "Solar": "gen_solar",
        "Wind": "gen_wind", "Biomass": "gen_biomass",
    },
    "Aggregated Balancing Energy Offers": {"Up": "offers_up", "Down": "offers_down"},
    "FCR Marginal Price": {"Up": "fcr_price_up", "Down": "fcr_price_down"},
    "aFRR Marginal Price": {"Up": "afrr_price_up", "Down": "afrr_price_down"},
    "mFRR Marginal Price": {"Up": "mfrr_price_up", "Down": "mfrr_price_down"},
    "RR Marginal Price": {"Up": "rr_price_up", "Down": "rr_price_down"},
}

FIVE_MIN = {
    "Activated Balancing Energy": {"Up": "activated_up", "Down": "activated_down"},
    "Balancing Energy Marginal Price": {"Up": "price_up", "Down": "price_down"},
}

BSP_SECTION = re.compile(r"^(FCR|aFRR|mFRR|RR)\s+BSP Results$")
UNIT_DIR = re.compile(r"^(.*?)\s+(Up|Down)$")


def parse_tso001(path: Path) -> dict[str, pd.DataFrame]:
    """One REP_TSO-001 workbook -> {'system','energy','bsp','reserves'} frames."""
    td = trade_date_from(path.name)
    book = pd.read_excel(path, sheet_name=None, header=None, dtype=object)
    out: dict[str, pd.DataFrame] = {}

    # ---- PT30M -----------------------------------------------------------
    if "PT30M" in book:
        wide: dict[str, list] = {}
        stamps: list[dt.datetime] = []
        bsp_rows = []
        for section, ts, rows in iter_sections(book["PT30M"]):
            if not stamps:
                stamps = ts
            mapping = HALF_HOURLY.get(section)
            if mapping:
                for label, values in rows:
                    col = mapping.get(label)
                    if col:
                        wide[col] = [num(v) for v in values]
                continue
            m = BSP_SECTION.match(section)
            if m:
                product = m.group(1)
                for label, values in rows:
                    um = UNIT_DIR.match(label)
                    if not um:
                        continue
                    unit, direction = um.group(1), um.group(2)
                    for k, v in enumerate(values):
                        val = num(v)
                        if val is None:
                            continue
                        bsp_rows.append((ts[k], k + 1, product, unit, direction, val))

        if stamps:
            sysdf = pd.DataFrame({"timestamp": stamps})
            sysdf.insert(1, "period", range(1, len(stamps) + 1))
            for col, vals in wide.items():
                sysdf[col] = (vals + [None] * len(stamps))[:len(stamps)]
            sysdf["trade_date"] = td
            sysdf["source_file"] = path.name
            out["system"] = sysdf

        if bsp_rows:
            b = pd.DataFrame(bsp_rows, columns=["timestamp", "period", "product",
                                                "unit", "direction", "mw"])
            b["trade_date"] = td
            out["bsp"] = b

    # ---- PT5M ------------------------------------------------------------
    if "PT5M" in book:
        wide = {}
        stamps = []
        res_rows = []
        for section, ts, rows in iter_sections(book["PT5M"]):
            if not stamps:
                stamps = ts
            mapping = FIVE_MIN.get(section)
            if mapping:
                for label, values in rows:
                    col = mapping.get(label)
                    if col:
                        wide[col] = [num(v) for v in values]
            elif section == "Available Reserves":
                for label, values in rows:
                    for k, v in enumerate(values):
                        val = num(v)
                        if val is None:
                            continue
                        res_rows.append((ts[k], k + 1, label, val))

        if stamps:
            e = pd.DataFrame({"timestamp": stamps})
            e.insert(1, "interval", range(1, len(stamps) + 1))
            for col, vals in wide.items():
                e[col] = (vals + [None] * len(stamps))[:len(stamps)]

            # Keep the published figure, and a cleaned copy with the sentinel
            # markers removed. Averaging the raw column would be meaningless.
            for side in ("up", "down"):
                col = f"price_{side}"
                if col in e:
                    e[f"{col}_raw"] = e[col]
                    e[col] = e[col].where(~e[col].isin(SENTINELS))

            e["trade_date"] = td
            e["source_file"] = path.name
            out["energy"] = e

        if res_rows:
            r = pd.DataFrame(res_rows, columns=["timestamp", "interval",
                                               "unit", "mw"])
            r["trade_date"] = td
            out["reserves"] = r

    return out


# --------------------------------------------------------------------------
# REP_TSO-009-BDL
# --------------------------------------------------------------------------

def parse_bdl(path: Path) -> pd.DataFrame:
    """
    One BDL workbook -> settlement-period system balance deviation.

    The file gives a trading-period number and a value; no timestamp. The
    timestamp is reconstructed as trade_date + (period - 1) x 30 minutes,
    which is the convention documented in DATA_DICTIONARY §2. On DST days that
    arithmetic drifts from wall-clock time, so `period` remains authoritative.
    """
    td = trade_date_from(path.name)
    book = pd.read_excel(path, sheet_name=None, header=0, dtype=object)
    frames = []
    for sheet, df in book.items():
        if df.shape[1] < 2 or df.empty:
            continue
        period = pd.to_numeric(df.iloc[:, 0], errors="coerce")
        value = pd.to_numeric(df.iloc[:, 1], errors="coerce")
        keep = period.notna() & value.notna()
        if not keep.any():
            continue
        name = str(df.columns[1])
        unit = ""
        um = re.search(r"\(([^)]+)\)\s*$", name)
        if um:
            unit = um.group(1)
            name = name[: um.start()].strip()
        f = pd.DataFrame({
            "period": period[keep].astype(int),
            "value": value[keep].astype(float),
        })
        f["variable"] = name
        f["unit"] = unit
        f["sheet"] = sheet
        frames.append(f)

    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    out["trade_date"] = td
    if td:
        out["timestamp"] = (pd.Timestamp(td)
                            + pd.to_timedelta((out["period"] - 1) * 30, unit="m"))
    else:
        out["timestamp"] = pd.NaT
    out["source_file"] = path.name
    return out[["timestamp", "period", "variable", "value", "unit",
                "sheet", "trade_date", "source_file"]]


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def excel_files(key: str) -> list[Path]:
    folder = RAW / key
    if not folder.exists():
        return []
    return sorted(f for f in folder.rglob("*.xls*") if not f.name.startswith("~"))


def concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    frames = [f for f in frames if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def cmd_parse(full_reserves: bool) -> int:
    TIDY.mkdir(parents=True, exist_ok=True)

    files = excel_files(TSO001)
    if not files:
        print(f"nothing under {RAW / TSO001} — run:\n"
              f"  python tsoc_scrape.py files --only {TSO001}", file=sys.stderr)
    else:
        print(f"parsing {len(files)} {TSO001} files …")
        parts: dict[str, list] = {"system": [], "energy": [], "bsp": [], "reserves": []}
        bad = 0
        for n, f in enumerate(files, 1):
            try:
                got = parse_tso001(f)
            except Exception as exc:
                bad += 1
                if bad <= 5:
                    print(f"  ! {f.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
                continue
            for k, v in got.items():
                if k == "reserves" and not full_reserves:
                    v = v[v["unit"].str.lower() == "total"]
                parts[k].append(v)
            if n % 50 == 0:
                print(f"  … {n}/{len(files)}", flush=True)

        system = concat(parts["system"])
        energy = concat(parts["energy"])

        # The 'Total' row of Available Reserves belongs on the 5-minute frame;
        # per-unit detail, when asked for, goes to its own file.
        reserves = concat(parts["reserves"])
        if not reserves.empty and not energy.empty:
            total = reserves[reserves["unit"].str.lower() == "total"]
            if not total.empty:
                energy = energy.merge(
                    total[["timestamp", "interval", "mw"]]
                    .rename(columns={"mw": "reserves_total"}),
                    on=["timestamp", "interval"], how="left")

        write(system.sort_values(["trade_date", "period"]), "bm_system_30min.csv")
        write(energy.sort_values(["trade_date", "interval"]), "bm_energy_5min.csv")
        write(concat(parts["bsp"]).sort_values(["trade_date", "period"]),
              "bm_bsp_results.csv")
        if full_reserves and not reserves.empty:
            write(reserves[reserves["unit"].str.lower() != "total"]
                  .sort_values(["trade_date", "interval"]),
                  "bm_available_reserves.csv")
        if bad:
            print(f"  {bad} file(s) unreadable", file=sys.stderr)

        if not energy.empty:
            for side in ("up", "down"):
                raw, clean = f"price_{side}_raw", f"price_{side}"
                if raw in energy:
                    n_sent = int(energy[raw].isin(SENTINELS).sum())
                    pct = 100 * n_sent / max(len(energy), 1)
                    print(f"  price_{side}: {n_sent:,} of {len(energy):,} intervals "
                          f"({pct:.1f}%) are sentinel markers, excluded from "
                          f"'{clean}' and kept in '{raw}'")

    files = excel_files(BDL)
    if not files:
        print(f"nothing under {RAW / BDL}", file=sys.stderr)
    else:
        print(f"parsing {len(files)} {BDL} files …")
        frames, bad = [], 0
        for n, f in enumerate(files, 1):
            try:
                frames.append(parse_bdl(f))
            except Exception as exc:
                bad += 1
                if bad <= 5:
                    print(f"  ! {f.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            if n % 100 == 0:
                print(f"  … {n}/{len(files)}", flush=True)
        write(concat(frames).sort_values(["trade_date", "period"]),
              "bdl_system_deviation.csv")
        if bad:
            print(f"  {bad} file(s) unreadable", file=sys.stderr)

    return 0


def write(df: pd.DataFrame, name: str) -> None:
    if df.empty:
        print(f"  {name}: nothing to write")
        return
    dest = TIDY / name
    df.to_csv(dest, index=False, encoding="utf-8-sig")
    span = ""
    if "trade_date" in df:
        span = f"  {df['trade_date'].min()} → {df['trade_date'].max()}"
    print(f"  {name:<28} {len(df):>9,} rows x {df.shape[1]:>2} cols{span}")


def cmd_inspect(key: str, n: int) -> int:
    files = excel_files(key)
    if not files:
        print(f"no files under {RAW / key}")
        return 1
    print(f"{len(files)} file(s); showing {min(n, len(files))}\n")
    for f in files[:n]:
        print("=" * 72)
        print(f.name, f"  trade_date={trade_date_from(f.name)}")
        book = pd.read_excel(f, sheet_name=None, header=None, dtype=object)
        for sheet, df in book.items():
            print(f"\n  sheet {sheet!r} shape={df.shape}")
            for section, ts, rows in iter_sections(df):
                step = ((ts[1] - ts[0]).total_seconds() / 60) if len(ts) > 1 else None
                labels = ", ".join(l for l, _ in rows[:6])
                more = f" … +{len(rows) - 6}" if len(rows) > 6 else ""
                print(f"    {section:<36} {len(ts):>4} x {step}min  "
                      f"[{labels}{more}]")
        print()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("parse", help="parse both BM sources into data/tidy/")
    pp.add_argument("--full-reserves", action="store_true",
                    help="also write per-unit available reserves (large)")

    ip = sub.add_parser("inspect", help="show the section layout")
    ip.add_argument("key", nargs="?", default=TSO001)
    ip.add_argument("-n", type=int, default=1)

    a = p.parse_args()
    if a.cmd == "parse":
        return cmd_parse(a.full_reserves)
    return cmd_inspect(a.key, a.n)


if __name__ == "__main__":
    raise SystemExit(main())
