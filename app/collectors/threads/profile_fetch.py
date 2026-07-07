from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from app import config
from app.collectors.common import net
from app.collectors.common.snippet_signals import merge_into_account
from app.collectors.threads import cache, snippet_estimate
from app.errors import UpstreamUnavailableError
from app.models import Account

logger = logging.getLogger(__name__)

BUCKET = "threads.com"

# Threads/Instagram型のusername構文チェック（英数字・ピリオド・アンダースコア、
# 1〜30文字、先頭/末尾はピリオド以外）。構文的に妥当でなければネットワークに
# 出る前にNoneを返す＝これが本モジュールで唯一「確認可能な非実在」ケースになる
# （理由は下記コメント参照：非ログインでは実在/非実在をHTTPレスポンスから
# 判別できないため）。
USERNAME_RE = re.compile(r"^[A-Za-z0-9_](?:[A-Za-z0-9_.]{0,28}[A-Za-z0-9_])?$")

# 実サイト確認済み(2026-07-02時点、@zuck / @mosseri / 意図的に存在しないユーザー名で確認):
#
# 1. https://www.threads.net/@{username} は https://www.threads.com/@{username} へ
#    HTTP 301でリダイレクトされる（threads.comが実サービスドメイン）。本モジュールは
#    リダイレクトの往復を避けるため threads.com に直接リクエストする。
# 2. 非ログインで https://www.threads.com/@{username} にGETすると、実在アカウント
#    (@zuck, @mosseri)・意図的に存在しないユーザー名のいずれも一律 HTTP 200 を返す。
#    レスポンス本文はどちらも約26万バイトのほぼ同一サイズで、<title>は常に
#    "Threads" のみ（アカウント名は入らない）。og:title/og:description/og:image
#    等のOGPメタタグは完全に不在（`og:`という文字列自体が0回）。canonical linkタグも無い。
#    決定的な確認として、リクエストした username 文字列（"zuck"/"mosseri"）自体が
#    レスポンスHTML中に**1回も出現しない**ことを確認した＝アカウント固有の
#    サーバーサイドレンダリングは一切行われておらず、全リクエストに対して
#    完全に同一の汎用SPAシェルが返っている。
# 3. `<script type="application/json">`ブロックは29個埋め込まれているが、中身は
#    "BarcelonaLoggedOutGating"（Web版Threadsの内部コードネームが"Barcelona"と
#    判明）等のfeatureフラグ・ロケール・CSP関連のブートストラップ設定のみで、
#    プロフィール固有の統計値（フォロワー数・bio等）は含まれない。
#    レスポンスヘッダも`cache-control: private, no-cache, no-store, must-revalidate`
#    であり、個別ページとしてキャッシュされない汎用シェル運用と整合する。
# 4. 上記の結果、非ログインでは実在/非実在を判別する手段が一切ない
#    （TikTokはoEmbedで200/400の判別が可能、Xはog:titleの有無で判別可能だったが、
#    Threadsはそのどちらの保証も持たない、実質的に最も強いログイン壁）。
#
# → 本実装は「構文的に妥当なusernameは疑わしきは実在として扱う」保証フロア層に
# とどめる。接続不能・非2xxのみ例外を送出し、200が返る限り常にスタブAccount
# （followers等の統計は0/空）を生成する。存在しないusernameを指定しても404では
# なくスタブAccountが返ってしまう点はTikTok/Xより弱い既知の制約
# （README「Threadsモジュールの制約」参照）。
#
# Cookie認証（Meta/Instagramと共有のsessionid、`INSTAGRAM_COOKIES_PATH`と同じ
# ファイルを指せる想定）を使えばこのログイン壁を通過できる可能性はあるが、
# 実際のMeta Cookieが無い環境のため検証できておらず未実装
# （TikTokのCookie認証と同じ扱い。README「今後の拡張」参照）。


def fetch_profile(username: str) -> Optional[Account]:
    """usernameのプロフィールを取得する。

    構文的に不正なusernameのみNoneを返す(=確認可能な「存在しない」唯一のケース)。
    threads.comは非ログインでは実在/非実在を判別できるシグナルを一切返さないため
    (実サイト確認済み、モジュール冒頭コメント参照)、構文が妥当な場合は接続確認の
    みを行い、display_name等が空のスタブAccountを返す。接続不能時のみ例外を送出する。
    """
    if not USERNAME_RE.match(username):
        return None

    cached = cache.get(username)
    if cached is not None:
        return cached

    profile_url = f"https://www.threads.com/@{username}"
    response = net.polite_get(
        profile_url,
        bucket=BUCKET,
        max_concurrency=config.THREADS_HYDRATE_CONCURRENCY,
        interval_range=(config.THREADS_JITTER_MIN, config.THREADS_JITTER_MAX),
    )
    if response is None:
        raise UpstreamUnavailableError(f"threads.com is unreachable while fetching '{username}'")
    if response.status_code == 404:
        # 実サイト確認済みの挙動ではないが(常に200を確認)、将来threads.com側の
        # 挙動が変わった場合に備えた防御的な分岐。
        return None
    if not response.ok:
        raise UpstreamUnavailableError(f"threads.com returned {response.status_code} for '{username}'")

    # レスポンス本文は実在/非実在を問わず汎用シェルのため(上記コメント参照)、
    # display_name等の実データは取得できない。usernameをそのままdisplay_nameとして使う。
    account = Account(
        id=username,
        platform="threads",
        username=username,
        display_name=username,
        bio="",
        followers=0,
        following=0,
        posts_count=0,
        engagement_rate=0.0,
        is_verified=False,
        avatar_url="",
        profile_url=profile_url,
        category="",
        last_posted_at=datetime.now(timezone.utc).isoformat(),
    )

    # 上記の通りThreadsは非ログインでは統計値が一切取れないため、Brave Search
    # のスニペット解析（app/collectors/threads/snippet_estimate.py）がこの
    # プラットフォームでは実質的に主要なデータ取得経路になる。スニペットに
    # 「ページが見つかりません」等の記述があればリンク切れとみなしNoneを返す。
    signals = snippet_estimate.estimate(username)
    if signals.not_found:
        return None
    account = merge_into_account(account, signals)

    cache.set(username, account)
    return account
