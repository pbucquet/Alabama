"""
config.py — re-exports alabama_core.config so local imports keep working.
"""
from alabama_core.config import (  # noqa: F401
    get_categories,
    get_subcategories,
    get_all_codes,
    get_priority_order,
)
