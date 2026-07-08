from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

from app import config
from app.collectors.common import net
from app.collectors.common.snippet_signals import merge_into_account
from app.collectors.tiktok import cache, snippet_estimate
from app.errors import UpstreamUnavailableError
from app.models import Account

BUCKET = "tiktok.com"

# 実サイト確認済み(2026-07時点): TikTokのプロフィールページ
# (https://www.tiktok.com/@{username}) はAkamai/SlardarのWAFチャレンジで
# 保護されており、非ログインの`requests.get`では「Please wait...」という
# 中身の無いHTMLしか返らず、SSR埋め込みJSON(SIGI_STATE等)は取得できない。
# ブラウザ相当のヘッダーを付与しても変化なし。
#
# 一方、TikTok公式のoEmbedエンドポイント(埋め込みカード用、非ログイン・
# 非WAF)は動作し、アカウントの存在確認(200 vs 400)と表示名(author_name)
# だけは取得できることを実サイトで確認した。フォロワー数・投稿数・
# エンゲージメント等の統計情報はoEmbedのレスポンスに含まれないため、
# 今回のMVPでは0/空のまま返す（Xの非認証フォールバックでfollowers=0に
# なるのと同じ位置づけ）。
#
# Cookie認証(TIKTOK_COOKIES_PATH)を使えばWAFを回避してSSRページから
# 実データが取れる可能性はあるが、実際のTikTok Cookieが無いと検証できず
# 未実装（README「今後の拡張」参照）。


def fetch_profile(username: str) -> Optional[tuple[Account, Optional[int]]]:
    """usernameのプロフィールを取得する。存在しない場合はNone、接続不能な場合は
    例外を送出する。oEmbed経由のため取得できるのは表示名・存在確認のみで、
    followers等の統計情報は0/空のまま返す（README既知の制約参照）。

    戻り値は`(Account, 推測できた総いいね数(推測不可ならNone))`のタプル
    （`app/collectors/tiktok/collector.py`のいいね数÷フォロワー数の足切りで使う。
    YouTube収集の平均再生数と同じ「Account本体のスキーマは汚さず、フィルタ用の
    追加シグナルを別途返す」パターン）。
    """
    cached = cache.get(username)
    if cached is not None:
        return cached

    profile_url = f"https://www.tiktok.com/@{username}"
    oembed_url = f"https://www.tiktok.com/oembed?url={quote(profile_url)}"

    response = net.polite_get(
        oembed_url,
        bucket=BUCKET,
        max_concurrency=config.TIKTOK_HYDRATE_CONCURRENCY,
        interval_range=(config.TIKTOK_JITTER_MIN, config.TIKTOK_JITTER_MAX),
    )
    if response is None:
        raise UpstreamUnavailableError(f"tiktok.com is unreachable while fetching '{username}'")
    if response.status_code == 400:
        # 実サイトで確認済み: 存在しないアカウントはoEmbedが400を返す
        return None
    if not response.ok:
        raise UpstreamUnavailableError(f"tiktok.com oEmbed returned {response.status_code} for '{username}'")

    try:
        payload = response.json()
    except ValueError as exc:
        raise UpstreamUnavailableError(f"tiktok.com oEmbed returned a non-JSON response for '{username}'") from exc

    display_name = payload.get("author_name") or username

    account = Account(
        id=username,
        platform="tiktok",
        username=username,
        display_name=display_name,
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

    # oEmbedはfollowers等の統計情報を一切返さないため、Brave Searchのスニペット
    # 解析（app/collectors/tiktok/snippet_estimate.py）がこのプラットフォームでは
    # 実質的に主要なデータ取得経路になる。スニペットに「ページが見つかりません」
    # 等の記述があればリンク切れとみなしNoneを返す。
    signals = snippet_estimate.estimate(username)
    if signals.not_found:
        return None
    account = merge_into_account(account, signals)

    result = (account, signals.likes)
    cache.set(username, account, signals.likes)
    return result
