from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from app import config
from app.collectors.common.discovery_google_cse import discover_via_google_cse
from app.collectors.common.discovery_search_engine import discover_via_search_engine

logger = logging.getLogger(__name__)

# instagram.com/{username} はTikTok(/@username)やX(/username, ただしreserved_pathsが
# 少数)と違い「@プレフィックスの無い、かつ予約済みトップレベルパスが多いユーザー名空間」
# のため、reserved_pathsを空にすると/p/や/reel/のような偽ユーザー名を候補として拾って
# しまう（実サイト確認済み・2026-07-02: これらのパスは実際にInstagram側の200を返す
# システムページであり、ユーザー名ではない）。
#
# 【重要な既知の制約】本実装時点でhtml.duckduckgo.com（DuckDuckGo html-lite、
# discover_via_search_engineが使う検索エンジン）はサーバー証明書が期限切れで接続不能
# だった（`app/collectors/x/`のDiscoveryセクションでも同じ問題が既に報告されている、
# この環境固有の問題でこちらの実装の問題ではない）。そのため、以下のRESERVED_PATHSは
# 実際のDDG検索結果からではなく、Instagram公式の既知URL構造（ヘルプページ・
# ブラウザでの実際のナビゲーション）から列挙したものであり、将来DDGが復旧して
# 実際に偽ユーザー名が混入するケースを観測した場合は追記すること。
# discover_via_search_engine自体はfail-soft設計のため、DDGが落ちていれば
# 空リストを返すだけで検索全体は失敗しない。
RESERVED_PATHS: set[str] = {
    "p", "reel", "reels", "explore", "stories", "accounts", "tv", "direct",
    "about", "developer", "legal", "privacy", "terms", "web", "graphql",
    "oauth", "challenge", "session", "login", "logout", "embed", "ads", "help",
    "api", "static", "data", "channel", "channels", "creators", "business",
    "download", "lite", "nametag", "topics", "guide", "guides", "locations",
    "hashtag", "emails", "faq", "support", "press", "jobs", "safety",
    "billing", "activity", "notifications", "invites", "settings",
    "your_activity", "audience_control", "consent", "privacy_checkup",
    "terms_agreement", "microsite", "brand", "developers", "management",
    "creation", "live", "guardian",
}

USERNAME_LINK_RE = re.compile(r"instagram\.com/([A-Za-z0-9_.]{1,30})")


def _discover_via_ddg(query: str, limit: int) -> list[str]:
    # DDG探索バケットはあえて明示指定しない（TikTokと同じデフォルトのホストバケットに
    # 相乗り）。プラットフォームをまたいだDDG探索の直列化は既知のトレードオフとして
    # 許容している設計判断（README参照）。将来体感速度が問題になった場合のみ
    # bucket="html.duckduckgo.com:instagram"のように分離を検討する。
    # DISCOVERY_PROXY_URL設定時のみ、DDG宛のこの呼び出しだけがプロキシを経由する
    # （他プラットフォームの通信には一切影響しない。README参照）。
    proxies = (
        {"http": config.DISCOVERY_PROXY_URL, "https": config.DISCOVERY_PROXY_URL}
        if config.DISCOVERY_PROXY_URL
        else None
    )
    return discover_via_search_engine(
        query,
        site="instagram.com",
        username_pattern=USERNAME_LINK_RE,
        reserved_paths=RESERVED_PATHS,
        max_pages=config.INSTAGRAM_DDG_MAX_PAGES,
        limit=limit,
        proxies=proxies,
    )


def _discover_via_cse(query: str, limit: int) -> list[str]:
    # GOOGLE_CSE_API_KEY/GOOGLE_CSE_CX未設定時はdiscover_via_google_cse自体が
    # 即座に空リストを返す（フェイルソフト、README参照）。
    return discover_via_google_cse(
        query,
        site="instagram.com",
        username_pattern=USERNAME_LINK_RE,
        reserved_paths=RESERVED_PATHS,
        max_pages=config.INSTAGRAM_DDG_MAX_PAGES,
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
