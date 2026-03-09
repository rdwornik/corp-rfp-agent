"""Taxonomy helpers -- wraps corp-os-meta if available, falls back to local data.

# TODO: Integrate with corp-os-meta package when available
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Try importing corp-os-meta taxonomy
try:
    from corp_os_meta import taxonomy as _meta_taxonomy
    _HAS_CORP_OS_META = True
    logger.info("corp-os-meta taxonomy loaded")
except ImportError:
    _HAS_CORP_OS_META = False

# Local fallback: family display names from family_config.json
_FAMILY_DISPLAY_NAMES = {
    "planning": "Cognitive Planning",
    "wms": "Warehouse Management",
    "logistics": "Logistics (TMS)",
    "scpo": "Supply Chain Planning and Optimization (SCPO)",
    "catman": "Category Management",
    "workforce": "Workforce Management",
    "commerce": "Commerce",
    "flexis": "Flexis",
    "network": "Network",
    "doddle": "Doddle (Returns)",
    "aiml": "AI/ML",
}


def get_family_display_name(family_code: str) -> str:
    """Get display name for a product family code."""
    if _HAS_CORP_OS_META:
        try:
            return _meta_taxonomy.get_family_display_name(family_code)
        except (AttributeError, KeyError):
            pass
    return _FAMILY_DISPLAY_NAMES.get(family_code, family_code)


def get_all_families() -> dict[str, str]:
    """Return all family codes and their display names."""
    if _HAS_CORP_OS_META:
        try:
            return _meta_taxonomy.get_all_families()
        except (AttributeError, KeyError):
            pass
    return dict(_FAMILY_DISPLAY_NAMES)


def is_valid_family(family_code: str) -> bool:
    """Check if a family code is valid."""
    return family_code in _FAMILY_DISPLAY_NAMES
