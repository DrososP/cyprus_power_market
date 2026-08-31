#!/usr/bin/env bash
# Run refresh.sh once a day at REFRESH_AT_UTC, then sleep until the next one.
#
# A plain loop rather than cron: cron inside a container needs its environment
# reconstructed by hand and swallows stdout, which makes a failed refresh
# invisible. This logs to the container log like everything else, so
# `docker compose logs scheduler` tells you what happened.
#
# Default 05:00 UTC. TSOC generates the previous day's REP_TSO-001 at about
# 06:00 Cyprus local — 03:00 UTC in summer, 04:00 UTC in winter — so 05:00 UTC
# is clear of it in both halves of the year.

set -uo pipefail

cd "$(dirname "$0")"

REFRESH_AT_UTC="${REFRESH_AT_UTC:-05:00}"
RUN_ON_START="${RUN_ON_START:-1}"

log() { printf '[%s] scheduler: %s\n' "$(date -u '+%Y-%m-%d %H:%M:%SZ')" "$*"; }

seconds_until() {
  local target now next
  now=$(date -u +%s)
  next=$(date -u -d "today ${1}" +%s 2>/dev/null) || return 1
  if [ "${next}" -le "${now}" ]; then
    next=$(date -u -d "tomorrow ${1}" +%s)
  fi
  echo $((next - now))
}

if [ "${RUN_ON_START}" = "1" ]; then
  log "running once at startup"
  ./refresh.sh
fi

while true; do
  wait_for=$(seconds_until "${REFRESH_AT_UTC}") || { log "bad REFRESH_AT_UTC"; exit 1; }
  log "next refresh in $((wait_for / 3600))h $(((wait_for % 3600) / 60))m (${REFRESH_AT_UTC} UTC)"
  sleep "${wait_for}"
  ./refresh.sh
done
