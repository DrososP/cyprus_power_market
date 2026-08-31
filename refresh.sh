#!/usr/bin/env bash
# One full refresh pass: scrape -> parse -> build.
#
# Safe to run repeatedly. The scraper skips files it already holds and extends
# the series CSVs rather than rebuilding them, so an interrupted run costs
# nothing but the time already spent.
#
# Deliberately NOT triggered by a page load. TSOC returns 403 under sustained
# requests, and letting visitor traffic drive the scraper would get the whole
# archive rate-limited.

set -uo pipefail

cd "$(dirname "$0")"

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%d %H:%M:%SZ')" "$*"; }

# Re-fetch a short trailing window rather than the whole archive: TSOC revises
# recent days, and the scraper's own de-duplication makes the overlap cheap.
LOOKBACK_DAYS="${LOOKBACK_DAYS:-10}"
START="$(date -u -d "${LOOKBACK_DAYS} days ago" '+%Y-%m-%d' 2>/dev/null \
        || date -u -v-"${LOOKBACK_DAYS}"d '+%Y-%m-%d')"

FILE_SOURCES="${FILE_SOURCES:-bm_daily_activity_en isp_balancing_bdl dam_forecast isp_forecast}"

log "refresh starting (series from ${START})"

log "1/4 series pages"
python tsoc_scrape.py series --start "${START}" || log "  series scrape returned $?"

log "2/4 report files"
# shellcheck disable=SC2086
python tsoc_scrape.py files --only ${FILE_SOURCES} || log "  file scrape returned $?"

log "3/4 parsing balancing market"
python tsoc_bm.py parse || log "  bm parse returned $?"

log "4/4 building parquet + catalog"
if python tsoc_build.py; then
  log "refresh complete"
else
  # A failed check is worth shouting about but must not leave the site down —
  # the previous build is still on disk and still served.
  log "BUILD REPORTED FAILED CHECKS — serving previous data, see output above"
fi
