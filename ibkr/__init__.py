# ibkr — Interactive Brokers data collection package (Step 1-4 of IBKR transition)

import os

# Host running IB Gateway or TWS. Normally the same machine, but the API is a
# plain socket, so the collector can equally well dial a Gateway on another box:
#
#     IB_HOST=192.168.1.42 python start_all.py
#
# That matters because IBKR serves real-time market data to ONE session at a
# time. Logging a second Gateway in elsewhere silently knocks the first one off
# and its collection stops. Pointing a second COLLECTOR at the one existing
# Gateway does not — separate clientIds are ordinary concurrent API clients — so
# this is how to exercise the collector from another machine (a Windows box,
# say) without interrupting a run in progress.
#
# The Gateway must be told to accept it: API > Settings > untick "Allow
# connections from localhost only", and add the client's IP to Trusted IPs.
IB_HOST = os.environ.get("IB_HOST", "127.0.0.1")
