#!/usr/bin/env bash
# start_all.sh - start everything as just TWO processes: collector supervisor + app.
# The supervisor babysits tick_collector + dynamic_collector + level2_collector
# (each stays a separate child on its own clientId; it restarts any that die).
# Prerequisite: IB Gateway is running and logged in with API (port 7497) enabled.
set -u
cd "$(dirname "$0")"
PY=/Users/jhtae/.pyenv/versions/3.12.4/bin/python

echo "== starting =="

# 1) Collector supervisor (keeps the 3 collectors alive). caffeinate keeps the Mac awake.
#    L2 runs in dynamic mode (follows the ticker you view in the app).
#    Add --no-l2 until NASDAQ TotalView depth is active if you don't want the thin IEX book.
echo "  -> collectors_supervisor (tick + dynamic + L2)"
caffeinate -i nohup "$PY" collectors_supervisor.py >> logs_supervisor.log 2>&1 &

# 2) Dashboard (port 8050)
echo "  -> dash app (http://127.0.0.1:8050)"
nohup "$PY" -m app >> logs_app.log 2>&1 &

sleep 5
echo "== status after start =="
ps -eo pid,command | grep -iE "collectors_supervisor|ibkr\.|python -m app" | grep -v grep
echo ""
echo "Logs:"
echo "  tail -f logs_supervisor.log        # supervisor (which child restarted etc.)"
echo "  tail -f logs_tick_collector.log    # realtime ticks"
echo "  tail -f logs_dynamic_collector.log"
echo "  tail -f logs_level2.log            # L2 depth"
echo "  tail -f logs_app.log               # dashboard"
