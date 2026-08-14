# Re-export async TBA helpers from the unified TBA client module.
#
# All TBA functionality lives in ``app.tba.client``.  This shim keeps
# existing ``from app.services.clients.tba import get_event`` imports
# working without a cross-module search-and-replace.

from app.tba.client import async_get_event as get_event  # noqa: F401
