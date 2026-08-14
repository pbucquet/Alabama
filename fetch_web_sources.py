"""
fetch_web_sources.py — re-exports alabama_core.fetch_web_sources so local imports keep working.
"""
from alabama_core.fetch_web_sources import (  # noqa: F401
    load_sources,
    get_owned_urls,
    fetch_web_sources,
)
