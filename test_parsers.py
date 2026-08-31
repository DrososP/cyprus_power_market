"""Offline tests for the scraper's parsing helpers. Run: python test_parsers.py"""
import tsoc_scrape as S

fails = []


def check(name, got, want):
    if got != want:
        fails.append(f"{name}\n   got:  {got!r}\n   want: {want!r}")
    else:
        print(f"  ok  {name}")


# ---- download URL discovery ------------------------------------------------
LISTING = """
<html><body>
<h2>08/2026</h2>
<a href="https://s3-eu-central-1.amazonaws.com/tso-cy/REP_MO-003/2026/08/REP_MO-003-S-en-20260816-20260815100059.xlsx?v959307599">REP_MO-003-S-en-20260816</a>
<a href="https://s3-eu-central-1.amazonaws.com/tso-cy/REP_MO-003/2026/08/REP_MO-003-S-en-20260816-20260815100059.xlsx?v111">duplicate cache-buster</a>
<h2>2025</h2>
<a href="https://s3-eu-central-1.amazonaws.com/tso-cy/REP_TSO-011/2025/REP_TSO-011-20250901-20251024134111.xlsx">no month folder</a>
<a href="https://s3-eu-central-1.amazonaws.com/tso-cy/Market/MustRunUnitsAuction/2026/Must_Run_Units_Auction-202601.pdf?v688146527">pdf</a>
<a href="https://tsoc.org.cy/files/market-system/market/REPORTS_ISSUES.pdf?v=1784748456">legacy host</a>
<a href="https://tsoc.org.cy/competitive-electricity-market/">nav link, must be ignored</a>
<a href="https://s3-eu-central-1.amazonaws.com/tso-cy/img/logo.png">image, must be ignored</a>
</body></html>
"""

urls = [u for u in S.DOWNLOAD_RE.findall(LISTING)]
kept, seen = [], set()
for u in urls:
    import os
    from urllib.parse import urlparse
    if os.path.splitext(urlparse(u).path)[1].lower() not in S.WANTED_EXT:
        continue
    f = S.clean_filename(u)
    if f in seen:
        continue
    seen.add(f)
    kept.append(u)

check("discovery keeps only real documents, de-duplicated", len(kept), 4)
check("nav links excluded", any("competitive-electricity-market/" in u for u in kept), False)
check("png excluded", any(u.endswith(".png") for u in kept), False)

# ---- filename / folder derivation -----------------------------------------
check("clean_filename strips ?v",
      S.clean_filename("https://s3-eu-central-1.amazonaws.com/tso-cy/REP_MO-003/2026/08/REP_MO-003-S-en-20260816-20260815100059.xlsx?v959307599"),
      "REP_MO-003-S-en-20260816-20260815100059.xlsx")

check("year/month from YYYY/MM path",
      S.year_month_from("https://s3-eu-central-1.amazonaws.com/tso-cy/REP_MO-003/2026/08/x.xlsx", "x.xlsx"),
      ("2026", "08"))

check("year-only path falls back to 00",
      S.year_month_from("https://s3-eu-central-1.amazonaws.com/tso-cy/REP_TSO-011/2025/REP_TSO-011-20250901-20251024134111.xlsx",
                        "REP_TSO-011-20250901-20251024134111.xlsx"),
      ("2025", "00"))

check("no path date -> filename date",
      S.year_month_from("https://s3-eu-central-1.amazonaws.com/tso-cy/X/DAM_FCST_20260816.xlsx",
                        "DAM_FCST_20260816.xlsx"),
      ("2026", "08"))

# ---- HTML table extraction -------------------------------------------------
TABLE_PAGE = """
<html><body>
<table><tr><th>Nav</th></tr><tr><td>not a series</td></tr></table>
<table>
  <tr><th>Timestamp</th><th>Clearing Price (EUR/MWh)</th><th>Cleared Quantity (MWh)</th></tr>
  <tr><td>04/07/2026 00:00</td><td>112.34</td><td>210.5</td></tr>
  <tr><td>04/07/2026 00:30</td><td>108.90</td><td>205.1</td></tr>
  <tr><td>04/07/2026 01:00</td><td>&nbsp;</td><td>199.0</td></tr>
</table>
</body></html>
"""
rows = S.pick_series_table(S.parse_tables(TABLE_PAGE))
check("picks the series table, not the nav table", rows[0][0], "Timestamp")
check("row count", len(rows), 4)
check("nbsp normalised", rows[3][1], "")
check("values parsed", rows[1], ["04/07/2026 00:00", "112.34", "210.5"])

check("rejects page with no dated table", S.pick_series_table(S.parse_tables(
    "<table><tr><th>a</th></tr><tr><td>b</td></tr></table>")), None)

# ---- Highcharts fallback ---------------------------------------------------
CHART_PAGE = """
<script>
Highcharts.chart('c', {
  xAxis: { categories: ['15/08/2026 00:00','15/08/2026 00:15','15/08/2026 00:30'] },
  series: [
    { name: 'Wind Generation', data: [12.4, 15.1, null] },
    { name: 'Solar Estimate',  data: [0, 0, 0] }
  ]});
</script>
"""
rows = S.extract_chart_series(CHART_PAGE)
check("chart fallback header", rows[0], ["timestamp", "Wind Generation", "Solar Estimate"])
check("chart fallback rows", len(rows), 4)
check("chart null -> empty", rows[3], ["15/08/2026 00:30", "", "0"])
check("chart fallback ignores non-chart pages", S.extract_chart_series("<p>hi</p>"), None)

# ---- series de-duplication / sorting ---------------------------------------
import csv, tempfile, datetime, pathlib
S.SERIES = pathlib.Path(tempfile.mkdtemp())
collected = {
    "04/07/2026 01:00": ["04/07/2026 01:00", "3"],
    "04/07/2026 00:00": ["04/07/2026 00:00", "1"],
    "04/07/2026 00:30": ["04/07/2026 00:30", "2"],
}


def sort_key(ts):
    for fmt in ("%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(ts.strip(), fmt)
        except ValueError:
            continue
    return datetime.datetime.max


check("chronological sort of DD/MM/YYYY timestamps",
      [sort_key(t).hour * 60 + sort_key(t).minute for t in sorted(collected, key=sort_key)],
      [0, 30, 60])

print()
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print(" -", f)
    raise SystemExit(1)
print("all parser tests passed")
