from __future__ import annotations

import logging
import threading
from typing import Optional

import requests

from app import config
from app.collectors.common.session import build_session, load_cookies

logger = logging.getLogger(__name__)

_session_lock = threading.Lock()
_session_built = False
_cached_session: Optional[requests.Session] = None


def build_authenticated_session() -> Optional[requests.Session]:
    """Cookieレコード(auth_token/ct0)からXの認証済みセッションを構築する。
    必要な設定やCookieが欠けていれば例外を出さずNoneを返す
    （＝呼び出し元は非認証モードにフォールバックする）。"""
    if not config.X_WEB_BEARER or not config.X_GRAPHQL_USERBYSCREENNAME_ID:
        logger.info(
            "X_WEB_BEARER/X_GRAPHQL_USERBYSCREENNAME_IDが未設定のため、"
            "Xは非認証モード（フォロワー数等は取得不可）で動作します"
        )
        return None

    cookies = load_cookies(config.X_COOKIES_PATH, ["auth_token", "ct0"])
    if cookies is None:
        return None

    session = build_session(
        cookies,
        domain=".x.com",
        headers={
            "authorization": f"Bearer {config.X_WEB_BEARER}",
            "x-csrf-token": cookies["ct0"],
            "x-twitter-active-user": "yes",
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-client-language": "ja",
        },
    )

    logger.info("Xの認証セッションを構築しました（認証済みGraphQL経由でのプロフィール取得を試みます）")
    return session


def get_session() -> Optional[requests.Session]:
    """プロセス内で一度だけ認証セッションを構築してキャッシュする（単純なシングルトン）。
    Cookieを更新した場合、反映にはバックエンドの再起動が必要（README参照）。"""
    global _session_built, _cached_session
    with _session_lock:
        if not _session_built:
            _cached_session = build_authenticated_session()
            _session_built = True
        return _cached_session
