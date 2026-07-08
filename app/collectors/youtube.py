from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

import requests

from app import config
from app.collectors.base import BaseCollector
from app.collectors.common.quality_gate import passes_universal_quality_gate
from app.errors import AccountNotFoundError, UpstreamUnavailableError
from app.models import Account, SearchParams

logger = logging.getLogger(__name__)

API_BASE = "https://www.googleapis.com/youtube/v3"
MAX_SEARCH_CANDIDATES = 15

_ISO8601_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?$"
)


def _parse_iso8601_duration_seconds(duration: str) -> int:
    match = _ISO8601_DURATION_RE.match(duration or "")
    if not match:
        return 0
    parts = match.groupdict()
    days = int(parts["days"] or 0)
    hours = int(parts["hours"] or 0)
    minutes = int(parts["minutes"] or 0)
    seconds = int(parts["seconds"] or 0)
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


class YouTubeCollector(BaseCollector):
    platform = "youtube"

    def search(self, params: SearchParams) -> list[Account]:
        if params.max_results == 0:
            # 呼び出し元がこのプラットフォームの検索を明示的にスキップしたい場合
            # （フロントエンドのプラットフォーム別取得件数設定で0を指定した場合）。
            return []

        channel_ids = self._discover_channel_ids(params)
        if not channel_ids:
            return []

        hydrated = self._hydrate_channels(channel_ids)
        # 投稿ゼロ・チャンネル登録者不足等の全プラットフォーム共通の品質ゲート
        # （`_apply_filters`のユーザー指定条件とは別、常時適用）。YouTubeは
        # `following`概念が無くhardcoded 0のため、FF比チェックは実質的に無効。
        # さらにYouTube固有として、(1)直近の通常動画（ショート除く）の平均再生数が
        # 登録者数に対して一定比率未満のチャンネル、(2)生涯累計の総視聴回数が
        # 登録者数に対して極端に少ない「登録者数だけ多くて実際には見られていない
        # 死にチャンネル」も除外する（モデリング基準）。
        accounts = [
            account
            for account, avg_views, total_views in hydrated
            if passes_universal_quality_gate(
                account, min_followers=config.YOUTUBE_MIN_FOLLOWERS, min_ff_ratio=config.YOUTUBE_MIN_FF_RATIO
            )
            and self._passes_view_subscriber_ratio(account, avg_views)
            and self._passes_total_views_sanity(account, total_views)
        ]
        return self._apply_filters(accounts, params)

    def get_account(self, account_id: str) -> Account:
        hydrated = self._hydrate_channels([account_id])
        if not hydrated:
            raise AccountNotFoundError(f"youtube channel '{account_id}' not found")
        return hydrated[0][0]

    @staticmethod
    def _passes_view_subscriber_ratio(account: Account, avg_views: float) -> bool:
        # フォロワー数（登録者数）自体が品質ゲートで既に判定されているため、
        # ここでは比率のみを見る。登録者数0（品質ゲートで別途除外される想定）で
        # ゼロ除算しないようガードする。
        if account.followers <= 0:
            return True
        ratio = avg_views / account.followers
        return ratio >= config.YOUTUBE_MIN_VIEW_SUBSCRIBER_RATIO

    @staticmethod
    def _passes_total_views_sanity(account: Account, total_views: int) -> bool:
        # 生涯累計の総視聴回数(statistics.viewCount)が登録者数の一定倍未満なら
        # 「登録者数だけ多くて実際には見られていない死にチャンネル」とみなし除外する。
        if account.followers <= 0:
            return True
        ratio = total_views / account.followers
        return ratio >= config.YOUTUBE_MIN_TOTAL_VIEWS_PER_SUBSCRIBER

    # -- discovery ---------------------------------------------------

    def _discover_channel_ids(self, params: SearchParams) -> list[str]:
        if params.query_type == "username":
            channel_id = self._lookup_by_handle(params.query)
            if channel_id:
                return [channel_id]

        limit = params.max_results if params.max_results is not None else MAX_SEARCH_CANDIDATES
        data = self._get("/search", {
            "part": "snippet",
            "type": "channel",
            "q": params.query,
            "maxResults": limit,
        })
        return [
            item["id"]["channelId"]
            for item in data.get("items", [])
            if "channelId" in item.get("id", {})
        ]

    def _lookup_by_handle(self, handle: str) -> Optional[str]:
        clean_handle = handle.lstrip("@")

        data = self._get("/channels", {"part": "id", "forHandle": clean_handle})
        items = data.get("items", [])
        if items:
            return items[0]["id"]

        data = self._get("/channels", {"part": "id", "forUsername": clean_handle})
        items = data.get("items", [])
        if items:
            return items[0]["id"]

        return None

    # -- hydration -----------------------------------------------------

    def _hydrate_channels(self, channel_ids: list[str]) -> list[tuple[Account, float, int]]:
        channels_by_id: dict[str, dict] = {}
        for batch_start in range(0, len(channel_ids), 50):
            batch = channel_ids[batch_start: batch_start + 50]
            data = self._get("/channels", {
                "part": "snippet,statistics,contentDetails",
                "id": ",".join(batch),
            })
            for item in data.get("items", []):
                channels_by_id[item["id"]] = item

        results: list[tuple[Account, float, int]] = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(self._build_account, channel_id, channel): channel_id
                for channel_id, channel in channels_by_id.items()
            }
            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    results.append(result)
        return results

    def _build_account(self, channel_id: str, channel: dict) -> Optional[tuple[Account, float, int]]:
        try:
            snippet = channel.get("snippet", {})
            statistics = channel.get("statistics", {})
            content_details = channel.get("contentDetails", {})
            uploads_playlist_id = content_details.get("relatedPlaylists", {}).get("uploads")

            last_posted_at, engagement_rate, avg_views = self._recent_activity(uploads_playlist_id, statistics)
            # チャンネルの生涯累計の総視聴回数。直近動画の平均再生数(avg_views)とは
            # 別の「死にチャンネル」検知シグナルとして使う（search()参照）。
            total_views = int(statistics.get("viewCount", 0) or 0)

            custom_url = snippet.get("customUrl", "")
            username = custom_url.lstrip("@") if custom_url else channel_id

            account = Account(
                id=channel_id,
                platform="youtube",
                username=username,
                display_name=snippet.get("title", username),
                bio=snippet.get("description", ""),
                followers=int(statistics.get("subscriberCount", 0) or 0),
                following=0,
                posts_count=int(statistics.get("videoCount", 0) or 0),
                engagement_rate=engagement_rate,
                is_verified=False,
                avatar_url=snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
                profile_url=f"https://www.youtube.com/channel/{channel_id}",
                category="",
                last_posted_at=last_posted_at,
            )
            return account, avg_views, total_views
        except Exception:
            logger.exception("failed to build account for channel %s", channel_id)
            return None

    def _recent_activity(self, uploads_playlist_id: Optional[str], statistics: dict) -> tuple[str, float, float]:
        """直近動画の(最終投稿日時, engagement_rate, 平均再生数)を返す。

        engagement_rate・平均再生数の算出対象は、ショート動画を除いた「通常動画」に
        限定する（モデリング基準「直近の通常動画（ショート除く）」対応）。YouTube
        Data APIにショート専用フラグは無いため、`contentDetails.duration`が
        `YOUTUBE_SHORTS_MAX_DURATION_SECONDS`（既定60秒）以下の動画をショートの
        近似シグナルとして除外する。通常動画が1本も見つからない場合はこの判定を
        諦め、全動画にフォールバックする（ショート専業チャンネルを一律除外しない
        ためのフェイルソフト）。最終投稿日時は（ショートも含めた）実際の直近投稿を
        反映させるため、フィルタ前の全件から算出する。
        """
        default_last_posted = datetime.now(timezone.utc).isoformat()
        if not uploads_playlist_id:
            return default_last_posted, 0.0, 0.0

        try:
            playlist_data = self._get("/playlistItems", {
                "part": "contentDetails",
                "playlistId": uploads_playlist_id,
                "maxResults": config.YOUTUBE_RECENT_VIDEOS_SCAN,
            })
        except UpstreamUnavailableError:
            return default_last_posted, 0.0, 0.0

        video_ids = [
            item["contentDetails"]["videoId"]
            for item in playlist_data.get("items", [])
            if "videoId" in item.get("contentDetails", {})
        ]
        if not video_ids:
            return default_last_posted, 0.0, 0.0

        try:
            videos_data = self._get("/videos", {
                "part": "snippet,statistics,contentDetails",
                "id": ",".join(video_ids),
            })
        except UpstreamUnavailableError:
            return default_last_posted, 0.0, 0.0

        all_items = videos_data.get("items", [])
        if not all_items:
            return default_last_posted, 0.0, 0.0

        last_posted_at = max(
            (item["snippet"]["publishedAt"] for item in all_items if "publishedAt" in item.get("snippet", {})),
            default=default_last_posted,
        )

        regular_items = [
            item for item in all_items
            if _parse_iso8601_duration_seconds(item.get("contentDetails", {}).get("duration", ""))
            > config.YOUTUBE_SHORTS_MAX_DURATION_SECONDS
        ]
        sample = (regular_items or all_items)[: config.YOUTUBE_ENGAGEMENT_RECENT_POSTS]

        views = sum(int(item.get("statistics", {}).get("viewCount", 0) or 0) for item in sample)
        interactions = sum(
            int(item.get("statistics", {}).get("likeCount", 0) or 0)
            + int(item.get("statistics", {}).get("commentCount", 0) or 0)
            for item in sample
        )

        # 直近動画からの近似値であり、YouTube公式のエンゲージメント指標ではない
        engagement_rate = round((interactions / views) * 100, 2) if views else 0.0
        avg_views = views / len(sample) if sample else 0.0
        return last_posted_at, engagement_rate, avg_views

    # -- filters ---------------------------------------------------------

    def _apply_filters(self, accounts: list[Account], params: SearchParams) -> list[Account]:
        filters = params.filters
        result = accounts

        if filters.followers_min is not None:
            result = [a for a in result if a.followers >= filters.followers_min]
        if filters.followers_max is not None:
            result = [a for a in result if a.followers <= filters.followers_max]
        if filters.engagement_min is not None:
            result = [a for a in result if a.engagement_rate >= filters.engagement_min]
        if filters.verified_only:
            result = [a for a in result if a.is_verified]
        if filters.category:
            result = [a for a in result if a.category == filters.category]

        return result

    # -- http --------------------------------------------------------------

    def _get(self, path: str, params: dict) -> dict:
        if not config.YOUTUBE_API_KEY:
            raise UpstreamUnavailableError("YOUTUBE_API_KEY is not configured")

        request_params = {**params, "key": config.YOUTUBE_API_KEY}
        try:
            response = requests.get(f"{API_BASE}{path}", params=request_params, timeout=15)
        except requests.RequestException as exc:
            raise UpstreamUnavailableError(f"youtube api request failed: {exc}") from exc

        if not response.ok:
            raise UpstreamUnavailableError(f"youtube api returned {response.status_code}")

        return response.json()
