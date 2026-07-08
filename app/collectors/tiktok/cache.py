from __future__ import annotations

from typing import Optional

from app import config
from app.collectors.common.cache import TTLCache
from app.models import Account

# Accountに加えて、Brave Searchスニペットから推測した総いいね数（推測不可なら
# None）も一緒にキャッシュする（app/collectors/tiktok/collector.pyの
# いいね数÷フォロワー数の足切りで再利用するため、キャッシュヒット時も
# 毎回Brave Search APIを叩き直さずに済むようにする）。
_cache: TTLCache[tuple[Account, Optional[int]]] = TTLCache(config.TIKTOK_PROFILE_CACHE_TTL)


def get(username: str) -> Optional[tuple[Account, Optional[int]]]:
    return _cache.get(username)


def set(username: str, account: Account, likes: Optional[int]) -> None:
    _cache.put(username, (account, likes))
