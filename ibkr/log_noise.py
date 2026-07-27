"""
ibkr/log_noise.py — drop benign IBKR / ib_async chatter from collector logs.

The IB API emits a steady stream of informational "errors" and warnings that are
not actionable for a market-data collector and, left alone, bury the lines that
DO matter (disconnects, subscribe failures):

  * data-farm connection status — 2104 / 2106 / 2119 / 2158
  * "account updates for <acct> request timed out" — we never subscribe to
    account data, so this timeout is expected and harmless
  * 321 "Group name cannot be null" — an account-group validation notice

install() attaches a filter to the root logger's HANDLERS (a filter on the
logger itself would not see records propagated up from ib_async's child
loggers). The match list is deliberately narrow so a genuine error is never
hidden — anything not explicitly listed passes through untouched.

Call install() once, AFTER logging.basicConfig() has created the root handler.
"""

import logging

# Substrings that mark a line as benign IBKR chatter. Narrow on purpose.
_BENIGN = (
    "Market data farm connection is OK",
    "HMDS data farm connection is OK",
    "Sec-def data farm connection is OK",
    "Market data farm is connecting",
    "account updates for",          # "... request timed out" — no account use
    "Group name cannot be null",    # error/warning 321 validation notice
)


class _BenignIBKRFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(s in msg for s in _BENIGN)


def install(logger: logging.Logger | None = None) -> None:
    """Attach the benign-chatter filter to every handler of `logger`
    (root by default). Idempotent: re-installing does not stack filters."""
    root = logger or logging.getLogger()
    for h in root.handlers:
        if not any(isinstance(f, _BenignIBKRFilter) for f in h.filters):
            h.addFilter(_BenignIBKRFilter())
