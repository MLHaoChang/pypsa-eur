#!/usr/bin/env bash
# S6 Results shots, the whole set. BEFORE (master worktree Vite on 5174) is the default:
#   tools/s6-run.sh
# AFTER (the working tree's Vite):
#   BASE_URL=http://127.0.0.1:5173 PREFIX=s6-after tools/s6-run.sh
# Needs the projects s6-single, s6-micro, s6-multi, s6-ipp, s6-ieee14 solved in this API process
# (after an API restart: tools/s6-api.mjs <state> <project> 'POST /api/simulation/queue {"project_id":"<project>"}').
# The API must allow the origin: start uvicorn with CORS_ALLOWED_ORIGINS listing http://127.0.0.1:5174 too.
# Run one target only: tools/s6-run.sh prices   (prices|loadflow|investment|multi|loadshape|curtailment|reserve)
set -uo pipefail
cd "$(dirname "$0")"
export BASE_URL=${BASE_URL:-http://127.0.0.1:5174} PREFIX=${PREFIX:-s6-before} OUT=${OUT:-../s6}
STATE=${STATE:-/tmp/claude-0/-home-user-pypsa-eur/f3d66623-bbcd-5602-92f3-4ebdf524c8cd/scratchpad/state.json}
[ -f "$STATE" ] || node login-state.mjs s6-single "$STATE"   # signs in; its create/queue calls 409 harmlessly when s6-single exists
mkdir -p "$OUT"
run() { local t=$1; shift; [ -z "${ONLY:-}" ] || [ "$ONLY" = "$t" ] || return 0; echo "== $t"; "$@"; }
ONLY=${1:-}
# 1 Prices, 2 Asset economics
run prices      node s6-shots.mjs s6-single "$STATE" g::capacity ct::costs_prices click::Prices pages::prices "click::Asset economics" pages::economics
# 3 Load flow with AC power flow (IEEE 14-bus)
run loadflow    env MAXPAGES=8 node s6-shots.mjs s6-ieee14 "$STATE" g::grid ct::load_flow pages::loadflow
# 4 Investment case > Participants: the Money flows Sankey
run investment  node s6-shots.mjs s6-ipp "$STATE" g::commercial ct::investment_case wait::3000 pages::investment
# 5 Aggregated overview, 6 the Power heatmap (StorageHeatmap, shown only with one period picked), 10 Energy flow with Aggregated
run multi       node s6-shots.mjs s6-multi "$STATE" g::capacity ct::build click::Overview click::Aggregated pages::overview \
                  ct::operation click::Dispatch click::2027 "at::dispatch-storage::Power heatmap" \
                  "click::Energy flow" click::Aggregated vh::1700 pages::energyflow-agg
# 7 Load shape (the tab has no scroller of its own, so a tall viewport)
run loadshape   node s6-shots.mjs s6-ipp "$STATE" g::capacity ct::operation "click::Load shape" vh::2000 wait::2000 pages::loadshape
# 8 Curtailment
run curtailment node s6-shots.mjs s6-micro "$STATE" g::capacity ct::curtailment pages::curtailment
# 9 Reserve commitments (in Storage cycling; the payload is mocked, see MOCK_RESERVES in s6-shots.mjs)
run reserve     env MOCK_RESERVES=1 node s6-shots.mjs s6-single "$STATE" g::capacity ct::operation "click::Storage cycling" pages::reserve
# Extras (dark only, plain names; run one at a time: parallel runs switch the session's active project).
#   tools/s6-run.sh extras            AFTER extras on BASE_URL
#   BASE_URL=http://127.0.0.1:5174 PREFIX=s6-before tools/s6-run.sh guided
x() { THEMES=dark "$@"; }
tmp() { mv "$OUT/$PREFIX-$1tmp-1-dark.png" "$OUT/$PREFIX-$1-dark.png"; }
run extras      x env VW=900 VH=1300 MOCK_RESERVES=1 node s6-shots.mjs s6-single "$STATE" g::capacity ct::operation "click::Storage cycling" wait::3000 "at::reserve-w900tmp::Reserve products" && { [ "$ONLY" != extras ] || tmp reserve-w900; }
run extras      x env VW=900 VH=1300 node s6-shots.mjs s6-multi "$STATE" g::capacity ct::build click::Overview click::Aggregated "at::overview-w900tmp::Generation · energy mix per period" && { [ "$ONLY" != extras ] || tmp overview-w900; }
# ChartZero: s6-multi has no curtailment (real data).
run extras      x node s6-shots.mjs s6-multi "$STATE" g::capacity ct::curtailment "at::chartzerotmp::Curtailment over time" && { [ "$ONLY" != extras ] || tmp chartzero; }
# Guided reaches Results > Investment from Hub design > Results > "The whole picture" (an ADVANCED tab).
[ "$ONLY" = extras ] || [ "$ONLY" = guided ] && { echo "== guided"; x env MODE=guided node s6-shots.mjs s6-ipp "$STATE" click::Results tid::hub-chapter-investment-open wait::3000 shot::investment-guided; }
