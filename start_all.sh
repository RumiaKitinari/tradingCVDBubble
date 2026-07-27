#!/usr/bin/env bash
# start_all.sh — the whole system is just TWO processes now:
#   1) the UNIFIED collector  (python -m ibkr.dynamic_collector)
#        ticks -> 1sec bars + one-time 1sec catch-up backfill + L2 depth,
#        purely on-demand: nothing is collected until you search a ticker in the
#        app; the queue keeps the most-recently-searched (up to 5 tick lines /
#        3 depth lines) and evicts the oldest.
#   2) the DASH app           (python -m app, http://127.0.0.1:8050)
#
# No supervisor, no separate tick/dynamic/level2 collectors — one connection,
# one clientId (40; backfills use 41). The collector reconnects on its own.
# Prerequisite: IB Gateway running + logged in, API enabled on port 7497.
set -u
cd "$(dirname "$0")"
PY=/Users/jhtae/.pyenv/versions/3.12.4/bin/python

echo "== starting =="

# 1) Unified collector. caffeinate keeps the Mac awake for the long run.
#    Add --no-l2 to skip depth; --pin with no names for a purely on-demand set.
echo "  -> unified collector (on-demand queue, up to 5 tickers, L2 depth)"
caffeinate -i nohup "$PY" -m ibkr.dynamic_collector >> logs_collector.log 2>&1 &

# 2) Dashboard (port 8050)
echo "  -> dash app (http://127.0.0.1:8050)"
nohup "$PY" -m app >> logs_app.log 2>&1 &

sleep 5
echo "== status after start =="
ps -eo pid,command | grep -iE "ibkr\.dynamic_collector|python -m app" | grep -v grep
echo ""
echo "Logs:"
echo "  tail -f logs_collector.log   # ticks + backfill + L2 depth (everything)"
echo "  tail -f logs_app.log         # dashboard"
