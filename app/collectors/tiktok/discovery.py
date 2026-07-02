from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from app import config
from app.collectors.common.discovery_google_cse import discover_via_google_cse
from app.collectors.common.discovery_search_engine import discover_via_search_engine

logger = logging.getLogger(__name__)

USERNAME_LINK_RE = re.compile(r"tiktok\.com/@([A-Za-z0-9_.]{1,24})")
RESERVED_PATHS: set[str] = set()


def _discover_via_ddg(query: str, limit: int) -> list[str]:
    # DISCOVERY_PROXY_URL設定時のみ、DDG宛のこの呼び出しだけがプロキシを経由する
    # （他プラットフォームの通信には一切影響しない。README参照）。
    proxies = (
        {"http": config.DISCOVERY_PROXY_URL, "https": config.DISCOVERY_PROXY_URL}
        if config.DISCOVERY_PROXY_URL
        else None
    )
    return discover_via_search_engine(
        query,
        site="tiktok.com",
        username_pattern=USERNAME_LINK_RE,
        reserved_paths=RESERVED_PATHS,
        max_pages=config.TIKTOK_DDG_MAX_PAGES,
        limit=limit,
        proxies=proxies,
    )


def _discover_via_cse(query: str, limit: int) -> list[str]:
    # GOOGLE_CSE_API_KEY/GOOGLE_CSE_CX未設定時はdiscover_via_google_cse自体が
    # 即座に空リストを返す（フェイルソフト、README参照）。
    return discover_via_google_cse(
        query,
        site="tiktok.com",
        username_pattern=USERNAME_LINK_RE,
        reserved_paths=RESERVED_PATHS,
        max_pages=config.TIKTOK_DDG_MAX_PAGES,
        limit=limit,
        api_key=config.GOOGLE_CSE_API_KEY,
        cx=config.GOOGLE_CSE_CX,
    )


def _safe_discover(source: str, discover_fn: Callable[[str, int], list[str]], query: str, limit: int) -> list[str]:
    try:
        return discover_fn(query, limit)
    except Exception:
        logger.exception("%s discovery failed for query=%s", source, query)
        return []


def discover_candidates(query: str, limit: int) -> list[str]:
    """DDG（既存）とGoogle CSE（`GOOGLE_CSE_API_KEY`/`GOOGLE_CSE_CX`設定時のみ）を
    並列実行し、結果をdedupe-mergeする（`app/collectors/x/collector.py`の
    `_discover_candidates`と同じ複数ソース統合パターン）。片方が失敗・未設定でも
    もう片方の結果で継続するフェイルソフト設計（README「トラブルシューティング」参照）。
    """
    with ThreadPoolExecutor(max_workers=2) as executor:
        ddg_future = executor.submit(_safe_discover, "ddg", _discover_via_ddg, query, limit)
        cse_future = executor.submit(_safe_discover, "google_cse", _discover_via_cse, query, limit)
        ddg_candidates = ddg_future.result()
        cse_candidates = cse_future.result()

    candidates: list[str] = []
    seen: set[str] = set()
    for username in [*ddg_candidates, *cse_candidates]:
        if username in seen:
            continue
        seen.add(username)
        candidates.append(username)
        if len(candidates) >= limit:
            break

    return candidates
