#!/usr/bin/env python
"""
collectors_supervisor.py
------------------------
One process that launches and babysits all IBKR collectors, so operationally you
run just TWO things: this supervisor + the dash app.

It keeps three child processes alive, each on its own IB clientId, and restarts
any that exit (with backoff). The processes stay SEPARATE (a crash in one never
takes down the others, and each keeps its own log) — the supervisor only unifies
their lifecycle.

Children:
  - tick_collector   : realtime ticks+quotes for the research trio (clientId 40)
  - dynamic_collector: on-demand ticks for other tickers viewed in the app
  - level2_collector : L2 depth on the research trio (clientId 63), so OBI is
                       captured for exactly the grid tickers. Pass --l2-dynamic
                       to instead follow the app's viewed tickers (LRU --l2-max),
                       or --no-l2 to skip.

Run (kept awake + backgrounded, logs to file):
  caffeinate -i nohup python collectors_supervisor.py >> logs_supervisor.log 2>&1 &

Stop: Ctrl-C (foreground) or `pkill -f collectors_supervisor` — children are
terminated with it.

Flags:
  --tickers NVDA SOFI RUM   research trio for tick_collector (default)
  --l2-max 3                dynamic L2: max concurrent depth lines (default 3)
  --no-l2                   don't start the L2 depth collector
  --port 7497               IB Gateway port
"""

import argparse
import logging
import os
import signal
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [supervisor] %(message)s",
)
log = logging.getLogger("supervisor")

# Restart backoff: if a child dies more than MAX_FAST_RESTARTS times within
# FAST_WINDOW seconds, wait BACKOFF_LONG before the next attempt (avoids a hot
# crash-loop hammering the gateway, e.g. when the gateway itself is down).
MAX_FAST_RESTARTS = 5
FAST_WINDOW = 120
BACKOFF_SHORT = 5
BACKOFF_LONG = 60


class Child:
    def __init__(self, name, cmd, logfile):
        self.name = name
        self.cmd = cmd
        self.logfile = logfile
        self.proc = None
        self.restarts = []  # recent restart timestamps

    def start(self):
        f = open(self.logfile, "a", buffering=1)
        f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} supervisor start: {' '.join(self.cmd)} =====\n")
        self.proc = subprocess.Popen(
            self.cmd, cwd=REPO_ROOT, stdout=f, stderr=subprocess.STDOUT,
            start_new_session=False,  # share our process group so signals reach them
        )
        log.info(f"started {self.name} (pid {self.proc.pid}) -> {self.logfile}")

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def backoff(self):
        now = time.time()
        self.restarts = [t for t in self.restarts if now - t < FAST_WINDOW]
        self.restarts.append(now)
        if len(self.restarts) > MAX_FAST_RESTARTS:
            log.warning(f"{self.name} restarting too fast ({len(self.restarts)}x/{FAST_WINDOW}s); "
                        f"waiting {BACKOFF_LONG}s (is the gateway up?)")
            return BACKOFF_LONG
        return BACKOFF_SHORT

    def terminate(self):
        if self.alive():
            log.info(f"stopping {self.name} (pid {self.proc.pid})")
            self.proc.terminate()


def build_children(args):
    children = [
        Child("tick_collector",
              [PY, "-m", "ibkr.tick_collector", "--ticker", *args.tickers,
               "--port", str(args.port), "--client-id", "40"],
              "logs_tick_collector.log"),
        Child("dynamic_collector",
              [PY, "-m", "ibkr.dynamic_collector"],
              "logs_dynamic_collector.log"),
    ]
    if not args.no_l2:
        if args.l2_dynamic:
            # Follow whatever tickers the app is showing (heatmap for arbitrary
            # views), LRU-capped at the IBKR depth-line limit.
            l2_cmd = [PY, "-m", "ibkr.level2_collector", "--dynamic",
                      "--max", str(args.l2_max), "--interval", "0.5",
                      "--port", str(args.port), "--client-id", "63"]
        else:
            # Persistent depth on the SAME research trio as the tick collector, so
            # OBI can be computed on exactly the grid tickers (default). The trio
            # is 3 lines = the IBKR depth cap, so this and --l2-dynamic are
            # mutually exclusive in practice.
            l2_cmd = [PY, "-m", "ibkr.level2_collector", "--tickers", *args.tickers,
                      "--interval", "0.5", "--port", str(args.port),
                      "--client-id", "63"]
        children.append(Child("level2_collector", l2_cmd, "logs_level2.log"))
    return children


def main():
    ap = argparse.ArgumentParser(description="Babysit all IBKR collectors as one process")
    ap.add_argument("--tickers", nargs="+", default=["NVDA", "SOFI", "RUM"])
    ap.add_argument("--l2-max", type=int, default=3,
                    help="dynamic L2: max concurrent depth lines (IBKR line limit)")
    ap.add_argument("--l2-dynamic", action="store_true",
                    help="L2 follows the app's viewed tickers instead of the fixed "
                         "research trio (default: persistent depth on --tickers)")
    ap.add_argument("--no-l2", action="store_true")
    ap.add_argument("--port", type=int, default=7497)
    args = ap.parse_args()

    children = build_children(args)
    stopping = {"flag": False}

    def on_signal(signum, frame):
        log.info(f"got signal {signum}; shutting down children")
        stopping["flag"] = True
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    for c in children:
        c.start()
        time.sleep(1.0)  # stagger connects so the gateway isn't hit all at once

    log.info(f"supervising {len(children)} collector(s). Ctrl-C to stop all.")
    try:
        while not stopping["flag"]:
            for c in children:
                if stopping["flag"]:
                    break
                if not c.alive():
                    code = c.proc.returncode if c.proc else "?"
                    wait = c.backoff()
                    log.warning(f"{c.name} exited (code {code}); restarting in {wait}s")
                    time.sleep(wait)
                    if not stopping["flag"]:
                        c.start()
            time.sleep(2)
    finally:
        for c in children:
            c.terminate()
        # give them a moment, then hard-kill stragglers
        deadline = time.time() + 8
        for c in children:
            if c.proc:
                remaining = max(0, deadline - time.time())
                try:
                    c.proc.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    log.warning(f"force-killing {c.name}")
                    c.proc.kill()
        log.info("all children stopped. bye.")


if __name__ == "__main__":
    main()
