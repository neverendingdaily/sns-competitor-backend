from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from app import config
from app.collectors.common.discovery_brave import discover_via_brave
from app.collectors.common.discovery_search_engine import discover_via_search_engine
from app.collectors.common.discovery_serpapi import discover_via_serpapi

logger = logging.getLogger(__name__)

# 実サイト確認済み(2026-07-02時点): threads.net/threads.comのプロフィールURLは
# TikTok/Xと同じ「先頭パスセグメントが`@username`」型(`/@zuck`等)であり、
# Instagramの`/p/`・`/reel/`・`/explore/`のようなbare-pathの予約語とユーザー名が
# 衝突する構造ではない。検証スパイクで叩いた`/@zuck`・`/@mosseri`・存在しない
# ユーザー名のいずれのパスでも、`@`プレフィックスと衝突しうるシステム/ユーティリティ
# パス（`/@login`・`/@help`等）は見つからなかった。よってTikTok同様
# RESERVED_PATHSは空集合のままにしておく。
#
# DuckDuckGoのインデックスは歴史的ドメインである threads.net を指すリンクが
# 多いと想定されるため`site=threads.net`で検索するが、threads.netは実際には
# threads.comへ301リダイレクトされる(profile_fetch.py参照)。リンク抽出用の
# 正規表現は両ドメイン表記に対応させておく。
USERNAME_LINK_RE = re.compile(r"threads\.(?:net|com)/@([A-Za-z0-9_.]{1,30})")
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
        site="threads.net",
        username_pattern=USERNAME_LINK_RE,
        reserved_paths=RESERVED_PATHS,
        max_pages=config.THREADS_DDG_MAX_PAGES,
        limit=limit,
        proxies=proxies,
    )


def _discover_via_brave(query: str, limit: int) -> list[str]:
    # BRAVE_SEARCH_API_KEY未設定時はdiscover_via_brave自体が即座に空リストを
    # 返す（フェイルソフト、README参照）。
    return discover_via_brave(
        query,
        site="threads.net",
        username_pattern=USERNAME_LINK_RE,
        reserved_paths=RESERVED_PATHS,
        max_pages=config.THREADS_DDG_MAX_PAGES,
        limit=limit,
        api_key=config.BRAVE_SEARCH_API_KEY,
    )


def _discover_via_serpapi(query: str, limit: int) -> list[str]:
    # SERPAPI_API_KEY未設定時はdiscover_via_serpapi自体が即座に空リストを
    # 返す（フェイルソフト、README参照）。
    return discover_via_serpapi(
        query,
        site="threads.net",
        username_pattern=USERNAME_LINK_RE,
        reserved_paths=RESERVED_PATHS,
        max_pages=config.THREADS_DDG_MAX_PAGES,
        limit=limit,
        api_key=config.SERPAPI_API_KEY,
    )


def _safe_discover(source: str, discover_fn: Callable[[str, int], list[str]], query: str, limit: int) -> list[str]:
    try:
        return discover_fn(query, limit)
    except Exception:
        logger.exception("%s discovery failed for query=%s", source, query)
        return []


def discover_candidates(query: str, limit: int) -> list[str]:
    """DDG（既存）・Brave Search API（`BRAVE_SEARCH_API_KEY`設定時のみ）・SerpAPI
    （`SERPAPI_API_KEY`設定時のみ）を並列実行し、結果をdedupe-mergeする
    （`app/collectors/x/collector.py`の`_discover_candidates`と同じ複数ソース
    統合パターン）。いずれかが失敗・未設定でも他の結果で継続するフェイルソフト設計
    （README「トラブルシューティング」参照。Google CSEは新規プロジェクトで利用不能に
    なったため置き換え済み）。
    """
    with ThreadPoolExecutor(max_workers=3) as executor:
        ddg_future = executor.submit(_safe_discover, "ddg", _discover_via_ddg, query, limit)
        brave_future = executor.submit(_safe_discover, "brave", _discover_via_brave, query, limit)
        serpapi_future = executor.submit(_safe_discover, "serpapi", _discover_via_serpapi, query, limit)
        ddg_candidates = ddg_future.result()
        brave_candidates = brave_future.result()
        serpapi_candidates = serpapi_future.result()

    candidates: list[str] = []
    seen: set[str] = set()
    for username in [*ddg_candidates, *brave_candidates, *serpapi_candidates]:
        if username in seen:
            continue
        seen.add(username)
        candidates.append(username)
        if len(candidates) >= limit:
            break

    return candidates
