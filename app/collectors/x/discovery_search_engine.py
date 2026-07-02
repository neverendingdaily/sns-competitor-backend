from __future__ import annotations

import re

from app import config
from app.collectors.common.discovery_search_engine import discover_via_search_engine

USERNAME_LINK_RE = re.compile(r"(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})(?:[/?\"']|$)")
RESERVED_PATHS = {"i", "home", "search", "hashtag", "intent", "share"}


def discover_candidates(query: str, limit: int) -> list[str]:
    return discover_via_search_engine(
        query,
        site="x.com",
        username_pattern=USERNAME_LINK_RE,
        reserved_paths=RESERVED_PATHS,
        max_pages=config.X_DDG_MAX_PAGES,
        limit=limit,
    )
