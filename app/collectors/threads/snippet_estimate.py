from __future__ import annotations

import re

from app.collectors.common.snippet_signals import SnippetSignals, fetch_snippet_signals

_OWN_PROFILE_RE_CACHE: dict[str, "re.Pattern[str]"] = {}


def _own_profile_pattern(username: str) -> "re.Pattern[str]":
    pattern = _OWN_PROFILE_RE_CACHE.get(username)
    if pattern is None:
        pattern = re.compile(
            rf"^https?://(www\.)?threads\.(com|net)/@{re.escape(username)}/?(?:[?#]|$)",
            re.IGNORECASE,
        )
        _OWN_PROFILE_RE_CACHE[username] = pattern
    return pattern


def estimate(username: str) -> SnippetSignals:
    """Threadsは非ログインでは実在/非実在の判別すら不可能で、プロフィール取得
    （profile_fetch.fetch_profile）は常にfollowers/following/postsCountが0の
    スタブAccountしか返せない（同モジュールの冒頭コメント参照）。そのため本関数は
    「稀に効くフォールバック」ではなく実質的にThreadsの主要なデータ取得経路になる。

    実機確認済み(2026-07-08時点、@hikakin/@zuck): threads.com/@{username}自身の
    ページに対するBrave Search側のスニペットには
    「5.6M followers • 150 threads」のような形式で数値が含まれていることが多い。
    薄いラッパーで、実処理は`app/collectors/common/snippet_signals.py`（全
    プラットフォーム共通）に委譲する。
    """
    return fetch_snippet_signals(
        username=username,
        query=f"{username} threads.com followers following",
        own_profile_pattern=_own_profile_pattern(username),
        bucket="brave-search-api:threads-snippet-estimate",
    )
