from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import quote

from app.collectors.common import net

logger = logging.getLogger(__name__)

# DuckDuckGo html-liteの1ページあたりの結果数（想定値）。ページネーションは
# `s`オフセットパラメータで行われると想定している(実サイトでの検証を推奨)。
# 想定と異なっていても「新規結果が見つからなければ停止」という設計のため、
# 1ページ目のみの取得に自然に縮退する。
RESULTS_PER_PAGE = 30


def discover_via_search_engine(
    query: str,
    *,
    site: str,
    username_pattern: "re.Pattern[str]",
    reserved_paths: set[str],
    max_pages: int,
    limit: int,
    bucket: Optional[str] = None,
    interval_range: Optional[tuple[float, float]] = None,
    max_concurrency: int = 1,
    proxies: Optional[dict[str, str]] = None,
) -> list[str]:
    """DuckDuckGo html-lite(`site:{site} {query}`)をページネーションしながら
    検索し、`username_pattern`（`group(1)`がusername）でユーザー名を抽出する。
    プラットフォームを問わない共通の発見(discovery)ロジック。フェイルソフト
    （検索エンジン側の障害は空リストで返し、呼び出し元の検索全体は失敗させない）。

    `proxies`はISP等によるduckduckgo.comへの接続ブロックを回避するための任意の
    緊急退避手段（呼び出し元が`DISCOVERY_PROXY_URL`設定時のみ組み立てて渡す。
    README「Discovery専用プロキシ」参照）。未指定ならこれまで通り直接接続する。
    """
    # DuckDuckGoのHTML lite版はJSなしクライアントを前提としており、
    # Google/Bingより単純なGETリクエストへの許容度が高くAPIキーも不要なため採用。
    usernames: list[str] = []
    seen: set[str] = set()

    for page in range(max_pages):
        offset = page * RESULTS_PER_PAGE
        search_url = f"https://html.duckduckgo.com/html/?q={quote(f'site:{site} {query}')}"
        if offset:
            search_url += f"&s={offset}"

        response = net.polite_get(
            search_url,
            bucket=bucket,
            interval_range=interval_range,
            max_concurrency=max_concurrency,
            proxies=proxies,
        )
        if response is None or not response.ok:
            logger.warning(
                "search engine discovery unavailable for site=%s query=%s (offset=%d)",
                site,
                query,
                offset,
            )
            break

        found_new = False
        for match in username_pattern.finditer(response.text):
            username = match.group(1)
            if username.lower() in reserved_paths or username in seen:
                continue
            seen.add(username)
            usernames.append(username)
            found_new = True
            if len(usernames) >= limit:
                return usernames

        if not found_new:
            break

    return usernames
