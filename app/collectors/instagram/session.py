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

# 【任意・既定無効の任意アップグレードティア】
# profile_fetch.py側の非認証`web_profile_info`呼び出しだけで十分実用的なデータ
# （フォロワー数・フォロー数・投稿数・bio・認証バッジ・アバター・直近投稿の
# いいね/コメント数によるengagement_rateまで）が取得できることを実サイトで確認済み
# （2026-07-02、詳細はprofile_fetch.py冒頭コメント参照）。そのため、このcookie認証
# ティアは「必須の欠落機能を埋める」ものではなく、(1) ブロック・レート制限の
# されにくさ、(2) フォロー中の非公開アカウントの閲覧、を目的とした任意の
# アップグレードとして位置づけている。
#
# 【重要】Instagram/Threadsは同一のMeta(Facebook)ログイン基盤を共有しており、
# 同じsessionid Cookieが両方で有効。THREADS_COOKIES_PATHを別途実装する場合も
# 同じCookieエクスポートファイルを指してよい（2回ログイン・エクスポートする
# 必要はない。README参照）。
#
# 【利用規約・アカウント凍結リスク（Xより慎重に扱うこと）】
# Meta（Instagram/Threads運営元）はXよりもスクレイピング行為に対して積極的に
# アカウント凍結・法的措置（Bright Data/hiQ訴訟等）を取ってきた実績がある。
# このtierはXの`X_COOKIES_PATH`と同じ「任意・既定無効・明示的リスク開示」の
# パターンを踏襲しているが、有効化する場合はXよりも一層の自己責任判断を推奨する
# （サブアカウント利用、控えめな同時実行数/間隔設定、自動投稿等へのスコープ拡大は
# 行わない）。


def build_authenticated_session() -> Optional[requests.Session]:
    """Cookie(sessionid)からInstagram(Meta)の認証済みセッションを構築する。
    必要な設定やCookieが欠けていれば例外を出さずNoneを返す
    （＝呼び出し元は非認証モードで継続する。非認証モードでも実データは取得できる点が
    Xとの違い）。
    """
    if not config.INSTAGRAM_COOKIES_PATH:
        return None

    cookies = load_cookies(config.INSTAGRAM_COOKIES_PATH, ["sessionid"])
    if cookies is None:
        return None

    session = build_session(
        cookies,
        domain=".instagram.com",
        headers={"x-ig-app-id": config.INSTAGRAM_IG_APP_ID},
    )

    logger.info(
        "Instagramの認証セッション(cookie)を構築しました。ブロック・レート制限の"
        "されにくさ向上と非公開フォロー中アカウントの閲覧を目的とした任意機能です"
        "（README「Instagramモジュールの制約」参照。X以上に慎重な利用を推奨）"
    )
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
