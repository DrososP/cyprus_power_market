#!/usr/bin/env python3
"""
Scraper for the Cyprus TSO (tsoc.org.cy) competitive electricity market.

Downloads every published report file and pages through every HTML time-series
table, into a local archive that can be re-run incrementally.

    python tsoc_scrape.py files                     # all report archives
    python tsoc_scrape.py series --start 2025-10-01 # all HTML time series
    python tsoc_scrape.py all --start 2025-10-01    # both

    python tsoc_scrape.py files  --only dam_daily_activity_en isp_clearing_mrp
    python tsoc_scrape.py series --only dam_prices_volumes --start 2026-01-01
    python tsoc_scrape.py list                      # show every source key

Layout produced:

    data/
      raw/<source_key>/<YYYY>/<MM>/<original-filename>   downloaded files
      html/<source_key>/<start>_<end>.html               cached page HTML
      series/<source_key>.csv                            stitched time series
      manifest.csv                                       every file ever fetched

Safe to interrupt and re-run: existing files are skipped unless --refresh.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import random
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from tsoc_sources import (
    FILE_SOURCES,
    FILE_SOURCES_BY_KEY,
    SERIES_SOURCES,
    SERIES_SOURCES_BY_KEY,
)

# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------

DATA = Path(__file__).resolve().parent / "data"
RAW = DATA / "raw"
HTML = DATA / "html"
SERIES = DATA / "series"
MANIFEST = DATA / "manifest.csv"

# TSOC returns 403 under load, so be polite. These are deliberately gentle.
DELAY = 1.2          # seconds between requests
JITTER = 0.6         # random extra delay, avoids a lockstep request pattern
MAX_RETRIES = 5
BACKOFF = 4.0        # seconds, doubled each retry
TIMEOUT = 90

# The site's own history begins here. Nothing is published before it.
EARLIEST = date(2025, 9, 1)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9,el;q=0.8",
}

# Files live on this bucket; a few older ones are served from tsoc.org.cy/files.
DOWNLOAD_RE = re.compile(
    r"""https?://(?:
            s3[-.]eu-central-1\.amazonaws\.com/tso-cy/
          | tso-cy\.s3[-.]eu-central-1\.amazonaws\.com/
          | tsoc\.org\.cy/files/
        )[^"'\s<>\\)]+""",
    re.VERBOSE | re.IGNORECASE,
)

WANTED_EXT = {".xlsx", ".xls", ".xlsm", ".csv", ".pdf", ".zip", ".docx", ".doc"}

session = requests.Session()
session.headers.update(HEADERS)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def polite_sleep() -> None:
    time.sleep(DELAY + random.random() * JITTER)


def fetch(url: str, *, binary: bool = False):
    """GET with retry/backoff. Returns response, or None if permanently failed."""
    delay = BACKOFF
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.get(url, timeout=TIMEOUT)
            if r.status_code == 200:
                return r
            if r.status_code in (403, 429, 500, 502, 503, 504):
                log(f"  HTTP {r.status_code}, retry {attempt}/{MAX_RETRIES} in {delay:.0f}s")
                time.sleep(delay)
                delay *= 2
                continue
            if r.status_code == 404:
                log(f"  HTTP 404 (not published): {url}")
                return None
            log(f"  HTTP {r.status_code}: {url}")
            return None
        except requests.RequestException as exc:
            log(f"  {type(exc).__name__}, retry {attempt}/{MAX_RETRIES} in {delay:.0f}s")
            time.sleep(delay)
            delay *= 2
    log(f"  GAVE UP: {url}")
    return None


def clean_filename(url: str) -> str:
    """Filename without the ?v… cache-buster, URL-decoded."""
    path = urlparse(url).path
    return unquote(os.path.basename(path))


def year_month_from(url: str, filename: str) -> tuple[str, str]:
    """
    Work out the archive folder. Prefer the /YYYY/MM/ segment of the S3 key;
    fall back to the first 8-digit date inside the filename; else 'undated'.
    """
    m = re.search(r"/((?:19|20)\d{2})/(0[1-9]|1[0-2])/", url)
    if m:
        return m.group(1), m.group(2)
    m = re.search(r"/((?:19|20)\d{2})/", url)
    if m:
        return m.group(1), "00"
    m = re.search(r"((?:19|20)\d{2})(0[1-9]|1[0-2])(?:[0-3]\d)?", filename)
    if m:
        return m.group(1), m.group(2)
    return "undated", "00"


def record(row: dict) -> None:
    """Append one line to the manifest."""
    DATA.mkdir(parents=True, exist_ok=True)
    new = not MANIFEST.exists()
    with MANIFEST.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=["fetched_at", "source_key", "filename", "path", "bytes", "url"],
        )
        if new:
            w.writeheader()
        w.writerow(row)


# --------------------------------------------------------------------------
# file sources
# --------------------------------------------------------------------------

def discover(source: dict) -> list[str]:
    """Return every distinct download URL linked from a source's listing page."""
    log(f"discovering {source['key']} …")
    r = fetch(source["url"])
    polite_sleep()
    if r is None:
        return []

    urls: list[str] = []

    # anchors first (authoritative), then a regex sweep for anything rendered
    # into inline JS or data attributes rather than a plain <a href>.
    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.find_all("a", href=True):
        urls.append(urljoin(source["url"], a["href"]))
    urls.extend(DOWNLOAD_RE.findall(r.text))

    seen, out = set(), []
    for u in urls:
        if not DOWNLOAD_RE.match(u):
            continue
        ext = os.path.splitext(urlparse(u).path)[1].lower()
        if ext not in WANTED_EXT:
            continue
        key = clean_filename(u)
        if key in seen:          # same file, different cache-buster
            continue
        seen.add(key)
        out.append(u)

    log(f"  {len(out)} files listed")
    return out


def scrape_files(source: dict, refresh: bool = False) -> None:
    urls = discover(source)
    got = skipped = failed = 0

    for url in urls:
        fname = clean_filename(url)
        yr, mo = year_month_from(url, fname)
        dest = RAW / source["key"] / yr / mo / fname

        if dest.exists() and dest.stat().st_size > 0 and not refresh:
            skipped += 1
            continue

        r = fetch(url, binary=True)
        polite_sleep()
        if r is None:
            failed += 1
            continue

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        got += 1
        record(
            dict(
                fetched_at=datetime.now().isoformat(timespec="seconds"),
                source_key=source["key"],
                filename=fname,
                path=str(dest.relative_to(DATA)),
                bytes=len(r.content),
                url=url,
            )
        )
        if got % 25 == 0:
            log(f"  … {got} downloaded")

    log(f"  {source['key']}: {got} new, {skipped} already held, {failed} failed")


# --------------------------------------------------------------------------
# series sources
# --------------------------------------------------------------------------

DATE_CELL_RE = re.compile(r"\d{2}[/-]\d{2}[/-]\d{4}|\d{4}-\d{2}-\d{2}")


def parse_tables(html: str) -> list[list[list[str]]]:
    """Every HTML table on the page, as a list of rows of cell strings."""
    soup = BeautifulSoup(html, "html.parser")
    tables = []
    for tbl in soup.find_all("table"):
        rows = []
        for tr in tbl.find_all("tr"):
            cells = [
                td.get_text(" ", strip=True).replace("\xa0", " ")
                for td in tr.find_all(["th", "td"])
            ]
            if cells:
                rows.append(cells)
        if len(rows) > 1:
            tables.append(rows)
    return tables


def pick_series_table(tables: list[list[list[str]]]) -> list[list[str]] | None:
    """
    Choose the table that actually holds the time series: the one with the most
    rows whose first cell looks like a date/timestamp.
    """
    best, best_score = None, 0
    for rows in tables:
        score = sum(1 for r in rows[1:] if r and DATE_CELL_RE.search(r[0]))
        if score > best_score:
            best, best_score = rows, score
    return best if best_score >= 2 else None


def extract_chart_series(html: str) -> list[list[str]] | None:
    """
    Fallback for pages that render only a JS chart: pull the Highcharts-style
    `categories` (timestamps) and `series[].data` (values) out of the inline
    script and rebuild a table from them.
    """
    cats = re.search(r"categories\s*:\s*\[(.*?)\]", html, re.S)
    if not cats:
        return None
    labels = re.findall(r"['\"]([^'\"]+)['\"]", cats.group(1))
    if not labels:
        return None

    names = re.findall(r"name\s*:\s*['\"]([^'\"]+)['\"]", html)
    datasets = [
        [v for v in re.findall(r"-?\d+(?:\.\d+)?|null", blob)]
        for blob in re.findall(r"data\s*:\s*\[([^\]]*)\]", html)
    ]
    datasets = [d for d in datasets if len(d) == len(labels)]
    if not datasets:
        return None

    header = ["timestamp"] + (
        names[: len(datasets)]
        if len(names) >= len(datasets)
        else [f"series_{i + 1}" for i in range(len(datasets))]
    )
    rows = [header]
    for i, label in enumerate(labels):
        rows.append([label] + [("" if d[i] == "null" else d[i]) for d in datasets])
    return rows


def row_date(cell: str) -> date | None:
    """Date part of a timestamp cell, whatever separator/order it uses."""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", cell)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.search(r"(\d{2})[/-](\d{2})[/-](\d{4})", cell)
        if not m:
            return None
        d, mo, y = map(int, m.groups())
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def series_url(source: dict, start_d: date, days: int, mode: str) -> str:
    """
    Build a range URL.

    `enddt` is NOT a free date on these pages. On the DAM price page it is a
    <select> whose only valid values are '+1days', '+3days', '+7days'; passing
    a date makes the server silently ignore the whole range and serve its
    default (tomorrow). Other pages use relative keywords. So try the duration
    form first, and keep an absolute-date form as a fallback.
    """
    if mode == "duration":
        end_param = f"+{days}days"
    else:
        end_param = f"{start_d + timedelta(days=days - 1):%d-%m-%Y}"
    return (f"{source['url']}?startdt={start_d:%d-%m-%Y}"
            f"&enddt={quote(end_param, safe='')}")


def scrape_series(source: dict, start: date, end: date, refresh: bool = False) -> None:
    key = source["key"]
    days = source["window_days"]
    window = timedelta(days=days)
    out_csv = SERIES / f"{key}.csv"
    cache_dir = HTML / key
    cache_dir.mkdir(parents=True, exist_ok=True)

    collected: dict[str, list[str]] = {}   # timestamp -> row, de-duplicates overlaps
    header: list[str] | None = None

    # keep anything previously stitched so re-runs extend rather than replace
    if out_csv.exists() and not refresh:
        with out_csv.open(encoding="utf-8-sig", newline="") as fh:
            rdr = csv.reader(fh)
            existing = list(rdr)
        if existing:
            header = existing[0]
            for row in existing[1:]:
                if row:
                    collected[row[0]] = row

    modes = ["duration", "date"]     # reordered once we learn which one works
    cursor, pages, empty, offrange = start, 0, 0, 0

    while cursor <= end:
        stop = min(cursor + window - timedelta(days=1), end)
        wanted = {cursor + timedelta(days=i) for i in range((stop - cursor).days + 1)}
        rows_in_window: list[list[str]] = []
        hdr: list[str] | None = None

        for mode in modes:
            url = series_url(source, cursor, days, mode)
            cache = cache_dir / f"{cursor:%Y%m%d}_{stop:%Y%m%d}_{mode}.html"

            if cache.exists() and not refresh:
                html = cache.read_text(encoding="utf-8", errors="replace")
            else:
                r = fetch(url)
                polite_sleep()
                if r is None:
                    continue
                html = r.text
                cache.write_text(html, encoding="utf-8", errors="replace")

            rows = pick_series_table(parse_tables(html)) or extract_chart_series(html)
            if not rows:
                continue

            hdr = rows[0]
            # Only keep rows that actually fall inside the window we asked for.
            # Without this the page's silent fallback to "tomorrow" gets written
            # to the CSV as if it were the requested history.
            rows_in_window = [
                r for r in rows[1:]
                if r and (rd := row_date(r[0])) is not None and rd in wanted
            ]
            if rows_in_window:
                if modes[0] != mode:      # this mode works; use it from now on
                    modes.remove(mode)
                    modes.insert(0, mode)
                    log(f"  {key}: using enddt mode {mode!r}")
                break

        pages += 1
        if not rows_in_window:
            if hdr is None:
                empty += 1
            else:
                offrange += 1
        else:
            if header is None:
                header = hdr
            for row in rows_in_window:
                collected[row[0]] = row

        if pages % 10 == 0:
            log(f"  … {key}: {pages} windows, {len(collected)} rows")

        cursor = stop + timedelta(days=1)

    if offrange:
        log(f"  ! {key}: {offrange}/{pages} windows returned data outside the "
            f"requested dates and were discarded — history may not go back this far")

    if not collected or header is None:
        log(f"  {key}: no rows parsed. Raw HTML kept in {cache_dir} for inspection.")
        return

    SERIES.mkdir(parents=True, exist_ok=True)
    width = max(len(header), max(len(r) for r in collected.values()))
    header = header + [f"col_{i}" for i in range(len(header) + 1, width + 1)]

    def sort_key(ts: str):
        for fmt in ("%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M", "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%d %H:%M", "%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(ts.strip(), fmt)
            except ValueError:
                continue
        return datetime.max

    with out_csv.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for ts in sorted(collected, key=sort_key):
            row = collected[ts]
            w.writerow(row + [""] * (width - len(row)))

    log(f"  {key}: {len(collected)} rows -> {out_csv.relative_to(DATA.parent)}"
        + (f" ({empty} windows returned nothing)" if empty else ""))


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def parse_day(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main() -> int:
    global DELAY

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=["files", "series", "all", "list"])
    p.add_argument("--only", nargs="+", metavar="KEY",
                   help="restrict to these source keys")
    p.add_argument("--start", type=parse_day, default=EARLIEST,
                   help="series start date, YYYY-MM-DD (default 2025-09-01)")
    p.add_argument("--end", type=parse_day, default=date.today(),
                   help="series end date, YYYY-MM-DD (default today)")
    p.add_argument("--refresh", action="store_true",
                   help="re-download files and re-fetch pages already held")
    p.add_argument("--delay", type=float, default=DELAY,
                   help="seconds between requests (default 1.2)")
    args = p.parse_args()
    DELAY = args.delay

    if args.mode == "list":
        print(f"{'KEY':<34} {'GROUP':<12} {'CADENCE':<10} NAME")
        for s in FILE_SOURCES:
            print(f"{s['key']:<34} {s['group']:<12} {s['cadence']:<10} {s['name']}")
        for s in SERIES_SOURCES:
            print(f"{s['key']:<34} {s['group']:<12} {'series':<10} {s['name']}")
        return 0

    DATA.mkdir(parents=True, exist_ok=True)
    only = set(args.only) if args.only else None

    if only:
        unknown = only - set(FILE_SOURCES_BY_KEY) - set(SERIES_SOURCES_BY_KEY)
        if unknown:
            print(f"unknown source key(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            print("run `python tsoc_scrape.py list` to see valid keys", file=sys.stderr)
            return 2

    if args.mode in ("files", "all"):
        targets = [s for s in FILE_SOURCES if not only or s["key"] in only]
        log(f"=== {len(targets)} file source(s) ===")
        for s in targets:
            scrape_files(s, refresh=args.refresh)

    if args.mode in ("series", "all"):
        targets = [s for s in SERIES_SOURCES if not only or s["key"] in only]
        log(f"=== {len(targets)} series source(s), {args.start} to {args.end} ===")
        for s in targets:
            log(f"paging {s['key']} …")
            scrape_series(s, args.start, args.end, refresh=args.refresh)

    log("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
