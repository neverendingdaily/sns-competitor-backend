from __future__ import annotations

from typing import Optional

from app import config
from app.collectors.common.cache import TTLCache
from app.models import Account

_cache = TTLCache(config.THREADS_PROFILE_CACHE_TTL)


def get(username: str) -> Optional[Account]:
    return _cache.get(username)


def set(username: str, account: Account) -> None:
    _cache.put(username, account)
