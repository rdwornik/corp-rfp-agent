"""
anonymization package
Clean architecture for data anonymization.
"""

from .core import anonymize, deanonymize, check
from .config import (
    load_config,
    get_blocklist,
    get_session,
    set_session_customer,
    add_to_blocklist,
    get_settings,
)
from .middleware import AnonymizationMiddleware

__all__ = [
    # Core functions
    "anonymize",
    "deanonymize",
    "check",
    # Config functions
    "load_config",
    "get_blocklist",
    "get_session",
    "set_session_customer",
    "add_to_blocklist",
    "get_settings",
    # Middleware
    "AnonymizationMiddleware",
]
