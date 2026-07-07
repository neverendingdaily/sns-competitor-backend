from __future__ import annotations

import re

from app.collectors.common.snippet_signals import SnippetSignals, fetch_snippet_signals

_OWN_PROFILE_RE_CACHE: dict[str, "re.Pattern[str]"] = {}


def _own_profile_pattern(username: str) -> "re.Pattern[str]":
    pattern = _OWN_PROFILE_RE_CACHE.get(username)
    if pattern is None:
        pattern = re.compile(
            rf"^https?://(www\.)?tiktok\.com/@{re.escape(username)}/?(?:[?#]|$)",
            re.IGNORECASE,
        )
        _OWN_PROFILE_RE_CACHE[username] = pattern
    return pattern


def estimate(username: str) -> SnippetSignals:
    """TikTokは非ログインではoEmbed経由の存在確認・表示名取得しかできず
    （profile_fetch.py冒頭コメント参照）、followers/following/postsCountは
    常に0のまま返る。そのため本関数はThreadsと同様、実質的に主要なデータ取得
    経路になる。

    実機確認済み(2026-07-08時点): tiktok.com/@{username}自身のページに対する
    Brave Search側のスニペットには「3676Following · 2.3MFollowers」のように
    数値とラベルの間に空白すら無い形式で含まれることが多い（動画数は含まれない
    ことが多く、その場合postsCountは0のままフェイルソフトする）。薄いラッパーで、
    実処理は`app/collectors/common/snippet_signals.py`（全プラットフォーム共通）
    に委譲する。
    """
    return fetch_snippet_signals(
        username=username,
        query=f"{username} tiktok.com followers following",
        own_profile_pattern=_own_profile_pattern(username),
        bucket="brave-search-api:tiktok-snippet-estimate",
    )
