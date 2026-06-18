"""
config.py — Category configuration parsed from environment variables.

STORY_CATEGORIES and STORY_SUBCATEGORIES are pipe-separated key:label pairs, e.g.:
  STORY_CATEGORIES=1:AI & Machine Learning|2:Blockchain & Crypto|3:Fintech|4:Consulting
  STORY_SUBCATEGORIES=A:New Products|B:Funding & M&A|C:Use Cases|D:Impact & Trends

Order matters: the LAST category in STORY_CATEGORIES is treated as the rarest
and gets the highest priority in LinkedIn story selection (tie-breaking).
"""

import os

_DEFAULT_CATEGORIES = [
    ("1", "AI & Machine Learning"),
    ("2", "Blockchain & Crypto"),
    ("3", "Fintech & Insurtech"),
    ("4", "Consulting & Professional Services"),
]

_DEFAULT_SUBCATEGORIES = [
    ("A", "New Products & Launches"),
    ("B", "Funding, M&A & Partnerships"),
    ("C", "Use Cases & Deployments"),
    ("D", "Business/Tech Impact & Trends"),
]


def _parse_kv_list(env_var: str, default: list[tuple[str, str]]) -> dict[str, str]:
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        return dict(default)
    result: dict[str, str] = {}
    for part in raw.split("|"):
        part = part.strip()
        if ":" in part:
            k, v = part.split(":", 1)
            k = k.strip()
            v = v.strip()
            if k:
                result[k] = v
    return result if result else dict(default)


def get_categories() -> dict[str, str]:
    """Returns ordered dict of {key: label} for parent categories."""
    return _parse_kv_list("STORY_CATEGORIES", _DEFAULT_CATEGORIES)


def get_subcategories() -> dict[str, str]:
    """Returns ordered dict of {key: label} for sub-categories."""
    return _parse_kv_list("STORY_SUBCATEGORIES", _DEFAULT_SUBCATEGORIES)


def get_all_codes() -> list[str]:
    """Returns all valid category codes, e.g. ['1A', '1B', ..., '4D']."""
    return [f"{c}{s}" for c in get_categories() for s in get_subcategories()]


def get_priority_order() -> tuple[str, ...]:
    """
    Returns category keys in descending priority for LinkedIn tie-breaking.
    The last category defined is considered the rarest and wins ties.
    Default: ('4', '3', '2', '1')
    """
    return tuple(reversed(list(get_categories().keys())))
