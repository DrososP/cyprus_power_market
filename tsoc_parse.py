#!/usr/bin/env python3
"""
Turn the downloaded Excel archive into tidy, timestamp-keyed CSVs.

    python tsoc_parse.py inspect dam_daily_activity_en   # show sheet structure
    python tsoc_parse.py tidy                            # parse everything
    python tsoc_parse.py tidy --only isp_clearing_mrp isp_balancing_bdl
    python tsoc_parse.py wide isp_clearing_mrp           # pivot one to wide

Output goes to data/tidy/<source_key>.csv in long format:

    timestamp, period, variable, value, unit, sheet, source_file, trade_date

Long format is the default because the TSOC workbooks differ in shape from one
report type to the next, and several put multiple logical tables on one sheet.
Long format survives that; a fixed wide schema would not. Use `wide` to pivot a
single source once you know it has a stable column set.

Start with `inspect` on any source before trusting its tidy output.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, time as dtime
from pathlib import Path

import pandas as pd

from tsoc_sources import FILE_SOURCES, FILE_SOURCES_BY_KEY

DATA = Path(__file__).resolve().parent / "data"
RAW = DATA / "raw"
TIDY = DATA / "tidy"

EXCEL_EXT = {".xlsx", ".xls", ".xlsm"}

# A column is the time index if its name looks like one of these.
TIME_HINTS = re.compile(
    r"date|time|timestamp|period|interval|ημερ|ώρα|ωρα|περίοδ|περιοδ|διάστημα",
    re.IGNORECASE,
)
# Settlement-period / interval numbering, e.g. "SP", "Period", "Interval No".
PERIOD_HINTS = re.compile(
    r"^\s*(sp|period|interval|per\.?|no\.?|α/α|περίοδος|περιοδος|διάστημα|διαστημα)\s*$",
    re.IGNORECASE,
)

UNIT_RE = re.compile(r"[\(\[]\s*([^\)\]]{1,20}?)\s*[\)\]]\s*$")

# Trade date lives in the filename: …-YYYYMMDD-YYYYMMDDHHMMSS.xlsx
FNAME_DATES = re.compile(r"(?<!\d)((?:20)\d{6})(?!\d)")


def trade_date_from(filename: str) -> str | None:
    """First 8-digit date in the filename = the day the report is about."""
    m = FNAME_DATES.search(filename)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def split_unit(col: str) -> tuple[str, str]:
    """'Clearing Price (EUR/MWh)' -> ('Clearing Price', 'EUR/MWh')"""
    col = str(col).strip()
    m = UNIT_RE.search(col)
    if m:
        return UNIT_RE.sub("", col).strip(), m.group(1)
    return col, ""


def find_header_row(df: pd.DataFrame, scan: int = 25) -> int | None:
    """
    TSOC workbooks often carry a title block above the table. The header row is
    the first row in the top `scan` rows with several distinct non-numeric,
    non-empty cells followed by data underneath.
    """
    best, best_score = None, 0
    for i in range(min(scan, len(df))):
        row = df.iloc[i]
        vals = [str(v).strip() for v in row if str(v).strip() not in ("", "nan", "NaT")]
        if len(vals) < 2:
            continue
        texty = sum(1 for v in vals if not re.fullmatch(r"-?[\d.,]+", v))
        score = texty + len(set(vals)) * 0.5
        if texty >= 2 and score > best_score and i + 1 < len(df):
            best, best_score = i, score
    return best


TIME_ONLY_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")


def coerce_timestamp(series: pd.Series, trade_date: str | None = None) -> pd.Series:
    """
    Parse a column to datetime, preferring day-first (TSOC uses DD/MM/YYYY).

    Deliberately refuses to parse plain integer columns. Settlement-period
    numbering ("Period" 1..48) otherwise gets silently read as nanoseconds
    since the epoch and produces 1970 timestamps that look real.
    """
    nat = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    nonnull = series.dropna()
    if nonnull.empty:
        return nat

    # already datetimes (openpyxl gives real datetimes for formatted cells)
    if pd.api.types.is_datetime64_any_dtype(series):
        return pd.to_datetime(series, errors="coerce")

    # datetime.time cells -> need the trade date to become absolute
    if all(isinstance(v, dtime) for v in nonnull.head(20)):
        if not trade_date:
            return nat
        base = pd.Timestamp(trade_date)
        return series.map(
            lambda v: base + pd.Timedelta(hours=v.hour, minutes=v.minute,
                                          seconds=v.second)
            if isinstance(v, dtime) else pd.NaT
        )

    text = nonnull.astype(str).str.strip()

    # numeric column: only a date if it genuinely looks like YYYYMMDD
    numeric = pd.to_numeric(nonnull, errors="coerce")
    if numeric.notna().mean() > 0.9:
        if not ((numeric >= 19000101) & (numeric <= 21001231)).all():
            return nat
        parsed = pd.to_datetime(numeric.astype("int64").astype(str),
                                format="%Y%m%d", errors="coerce")
        return parsed.reindex(series.index)

    # "HH:MM" time-of-day column -> anchor on the report's trade date
    if text.str.match(TIME_ONLY_RE).mean() > 0.8:
        if not trade_date:
            return nat
        base = pd.Timestamp(trade_date)
        padded = text.where(text.str.count(":") == 2, text + ":00")
        return (base + pd.to_timedelta(padded, errors="coerce")).reindex(series.index)

    out = pd.to_datetime(series, errors="coerce", dayfirst=True, format="mixed")
    if out.notna().mean() < 0.5:
        out = pd.to_datetime(series, errors="coerce")
    return out


def tidy_sheet(df: pd.DataFrame, sheet: str, src_file: str,
               trade_date: str | None) -> pd.DataFrame | None:
    """Reshape one sheet into long format. Returns None if it holds no table."""
    hdr = find_header_row(df)
    if hdr is None:
        return None

    header = [str(c).strip() for c in df.iloc[hdr]]
    body = df.iloc[hdr + 1:].copy()
    body.columns = pd.Index(header)
    body = body.loc[:, [c for c in body.columns if c not in ("", "nan", "None")]]
    body = body.dropna(how="all")
    if body.empty or body.shape[1] < 2:
        return None
    body = body.loc[:, ~body.columns.duplicated()]

    # --- locate the settlement-period and time index columns --------------
    period_col = next((c for c in body.columns if PERIOD_HINTS.match(str(c))), None)

    # Score every column; a name that hints at time breaks ties. Picking the
    # best coverage rather than the first match matters when a sheet has both
    # a "Period" number and a "Delivery Period" clock time.
    ts_col, best, best_score = None, None, 0.0
    for c in body.columns:
        parsed = coerce_timestamp(body[c], trade_date)
        cover = parsed.notna().mean()
        if cover < 0.5:
            continue
        score = cover + (0.5 if TIME_HINTS.search(str(c)) else 0.0)
        if score > best_score:
            ts_col, best, best_score = c, parsed, score

    if ts_col is not None:
        body["_ts"] = best
        if period_col == ts_col:
            period_col = None

    value_cols = [c for c in body.columns
                  if c not in {ts_col, period_col, "_ts"}]
    if not value_cols:
        return None

    out = body.melt(
        id_vars=[c for c in ["_ts", period_col] if c is not None and c in body.columns],
        value_vars=value_cols,
        var_name="variable",
        value_name="value",
    )
    out = out.rename(columns={"_ts": "timestamp", period_col: "period"}
                     if period_col else {"_ts": "timestamp"})
    if "timestamp" not in out:
        out["timestamp"] = pd.NaT
    if "period" not in out:
        out["period"] = pd.NA

    out["value"] = pd.to_numeric(
        out["value"].astype(str).str.replace(",", "", regex=False).str.strip(),
        errors="coerce",
    )
    out = out.dropna(subset=["value"])
    if out.empty:
        return None

    names_units = out["variable"].map(split_unit)
    out["variable"] = [n for n, _ in names_units]
    out["unit"] = [u for _, u in names_units]
    out["sheet"] = sheet
    out["source_file"] = src_file
    out["trade_date"] = trade_date

    return out[["timestamp", "period", "variable", "value", "unit",
                "sheet", "source_file", "trade_date"]]


def parse_source(key: str, limit: int | None = None) -> pd.DataFrame:
    folder = RAW / key
    if not folder.exists():
        print(f"  {key}: nothing downloaded yet", file=sys.stderr)
        return pd.DataFrame()

    files = sorted(f for f in folder.rglob("*") if f.suffix.lower() in EXCEL_EXT)
    if limit:
        files = files[:limit]
    if not files:
        print(f"  {key}: no Excel files (PDF-only source?)", file=sys.stderr)
        return pd.DataFrame()

    frames, bad = [], 0
    for n, f in enumerate(files, 1):
        td = trade_date_from(f.name)
        try:
            book = pd.read_excel(f, sheet_name=None, header=None, dtype=object)
        except Exception as exc:
            bad += 1
            if bad <= 3:
                print(f"  ! {f.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        for sheet, df in book.items():
            try:
                tidied = tidy_sheet(df, sheet, f.name, td)
            except Exception as exc:
                print(f"  ! {f.name}[{sheet}]: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
                continue
            if tidied is not None and not tidied.empty:
                frames.append(tidied)
        if n % 100 == 0:
            print(f"  … {n}/{len(files)} files", flush=True)

    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values(["timestamp", "variable"], na_position="last")
    print(f"  {key}: {len(files)} files -> {len(out):,} rows"
          + (f", {bad} unreadable" if bad else ""))
    return out


def cmd_inspect(key: str, n: int = 2) -> None:
    folder = RAW / key
    files = sorted(f for f in folder.rglob("*") if f.suffix.lower() in EXCEL_EXT)
    if not files:
        print(f"no Excel files under {folder}")
        return
    print(f"{len(files)} file(s); showing {min(n, len(files))}\n")
    for f in files[:n]:
        print("=" * 72)
        print(f.relative_to(RAW), f"   trade_date={trade_date_from(f.name)}")
        book = pd.read_excel(f, sheet_name=None, header=None, dtype=object)
        for sheet, df in book.items():
            hdr = find_header_row(df)
            print(f"\n  sheet {sheet!r}  shape={df.shape}  header_row={hdr}")
            with pd.option_context("display.max_columns", 30, "display.width", 200):
                print(df.head(8).to_string(max_colwidth=22))
        print()


def cmd_tidy(keys: list[str], limit: int | None) -> None:
    TIDY.mkdir(parents=True, exist_ok=True)
    summary = []
    for key in keys:
        print(f"parsing {key} …")
        df = parse_source(key, limit)
        if df.empty:
            continue
        dest = TIDY / f"{key}.csv"
        df.to_csv(dest, index=False, encoding="utf-8-sig")
        summary.append((key, len(df), df["variable"].nunique(), dest))
    print("\n--- tidy output ---")
    for key, rows, nvar, dest in summary:
        print(f"{key:<34} {rows:>10,} rows  {nvar:>4} variables  -> {dest.name}")
    if not summary:
        print("nothing parsed; run tsoc_scrape.py first")


def cmd_wide(key: str) -> None:
    src = TIDY / f"{key}.csv"
    if not src.exists():
        print(f"{src} not found; run `tidy` first")
        return
    df = pd.read_csv(src, parse_dates=["timestamp"])
    wide = df.pivot_table(index="timestamp", columns="variable",
                          values="value", aggfunc="mean")
    dest = TIDY / f"{key}_wide.csv"
    wide.to_csv(dest, encoding="utf-8-sig")
    print(f"{len(wide):,} timestamps x {wide.shape[1]} columns -> {dest}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("inspect", help="print sheet structure of sample files")
    i.add_argument("key")
    i.add_argument("-n", type=int, default=2)

    t = sub.add_parser("tidy", help="parse Excel archive into long CSV")
    t.add_argument("--only", nargs="+", metavar="KEY")
    t.add_argument("--limit", type=int, help="max files per source (for a quick trial)")

    w = sub.add_parser("wide", help="pivot one tidy CSV to wide form")
    w.add_argument("key")

    a = p.parse_args()

    if a.cmd == "inspect":
        cmd_inspect(a.key, a.n)
    elif a.cmd == "tidy":
        keys = a.only or [s["key"] for s in FILE_SOURCES]
        unknown = set(keys) - set(FILE_SOURCES_BY_KEY)
        if unknown:
            print(f"unknown key(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        cmd_tidy(keys, a.limit)
    elif a.cmd == "wide":
        cmd_wide(a.key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
