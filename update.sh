#!/bin/bash
# Pull the latest VCT results from vlr.gg and rebuild the forecasts when anything changed.
# Safe to run on a schedule (see scheduler/install.sh): runs never overlap, each run costs
# ~10 requests to vlr.gg, and the models are only refit when the data actually changed.
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd -P)"
cd "$DIR"
PY="$DIR/.venv/bin/python"
LOCK="$DIR/cache/update.lock"
FP="$DIR/cache/fingerprint"
mkdir -p "$DIR/cache" "$DIR/site/data"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

[ -x "$PY" ] || { log "no venv: uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt"; exit 1; }

# one run at a time; a lock older than 2 hours is from a crashed run
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +120 2>/dev/null)" ]; then
    log "removing stale lock"; rm -rf "$LOCK"; mkdir "$LOCK"
  else
    log "another update is running; skipping"; exit 0
  fi
fi
trap 'rm -rf "$LOCK"' EXIT

status() {  # heartbeat the site reads: when vlr.gg was last checked and whether it worked
  printf '{"checked_at":"%s","ok":%s,"message":"%s"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" > "$DIR/site/data/status.json"
}

YEARS="$(date +%Y)"
[ "$(date +%m)" = "01" ] && YEARS="$(( $(date +%Y) - 1 )) $YEARS"   # finish last season's events in January

log "checking vlr.gg ($YEARS)"
if ! "$PY" -m pipeline.scrape --refresh --years $YEARS; then
  log "scrape failed"; status false "Could not reach vlr.gg"; exit 1
fi

# fingerprint: completed results + scheduled pairings; refit only when it changes
NEW_FP="$(sqlite3 "$DIR/data/vct.db" "SELECT match_id, status, team1_id, team2_id, score1, score2, date FROM matches
          WHERE status != 'completed' OR match_id IN (SELECT match_id FROM econ_done) ORDER BY match_id" | shasum | cut -c1-16)"
if [ -f "$FP" ] && [ "$(cat "$FP")" = "$NEW_FP" ] && [ -f "$DIR/site/data/meta.json" ]; then
  log "no changes; forecasts are current"
  status true "No new results"
  exit 0
fi

log "data changed; rebuilding forecasts"
if "$PY" -m pipeline.export; then
  echo "$NEW_FP" > "$FP"
  status true "Forecasts rebuilt"
  log "done"
else
  log "export failed; keeping previous forecasts"
  status false "Rebuild failed; showing previous forecasts"
  exit 1
fi
