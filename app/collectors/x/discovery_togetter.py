from __future__ import annotations

import logging
import re
from urllib.parse import quote

from app import config
from app.collectors.common.net import polite_get

logger = logging.getLogger(__name__)

# 実サイトの構造を確認済み(2026-07時点):
# 検索結果ページ(togetter.com/search?q=...)自体には埋め込みツイートは無く、
# 個別まとめ記事ページ(togetter.com/li/{id})へのリンクのみが並ぶ。
# 埋め込みツイートの投稿者ユーザー名は記事ページ側にしかない。
ARTICLE_LINK_RE = re.compile(r"https://togetter\.com/li/\d+")
TWEET_AUTHOR_RE = re.compile(r"(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})/status/\d+")


def discover_candidates(query: str, limit: int) -> list[str]:
    article_urls = _collect_article_urls(query)
    if not article_urls:
        return []

    usernames: list[str] = []
    seen: set[str] = set()
    for article_url in article_urls[: config.X_TOGETTER_MAX_ARTICLES]:
        article_response = polite_get(article_url)
        if article_response is None or not article_response.ok:
            logger.warning("togetter article unavailable: %s", article_url)
            continue

        for username in TWEET_AUTHOR_RE.findall(article_response.text):
            if username in seen:
                continue
            seen.add(username)
            usernames.append(username)
            if len(usernames) >= limit:
                return usernames

    return usernames


def _collect_article_urls(query: str) -> list[str]:
    """検索結果ページを複数ページ走査し、記事URLを重複無しで集める。

    2ページ目以降は`?page=N`が付与されると想定している(実サイトでの検証を
    推奨 — 実装時点ではアクセスして確認できていない)。想定と異なるスキーム
    だった場合でも「新規リンクが見つからなければ停止」という設計のため、
    1ページ目のみの取得＝旧来の動作に自然に縮退する。
    """
    article_urls: list[str] = []
    seen: set[str] = set()

    for page in range(1, config.X_TOGETTER_MAX_PAGES + 1):
        if page == 1:
            search_url = f"https://togetter.com/search?q={quote(query)}"
        else:
            search_url = f"https://togetter.com/search?q={quote(query)}&page={page}"

        response = polite_get(search_url)
        if response is None or not response.ok:
            logger.warning("togetter search page %d unavailable for query=%s", page, query)
            break

        found_new = False
        for url in dict.fromkeys(ARTICLE_LINK_RE.findall(response.text)):
            if url in seen:
                continue
            seen.add(url)
            article_urls.append(url)
            found_new = True

        if not found_new:
            break
        if len(article_urls) >= config.X_TOGETTER_MAX_ARTICLES:
            break

    return article_urls
