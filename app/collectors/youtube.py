from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

import requests

from app import config
from app.collectors.base import BaseCollector
from app.errors import AccountNotFoundError, UpstreamUnavailableError
from app.models import Account, SearchParams

logger = logging.getLogger(__name__)

API_BASE = "https://www.googleapis.com/youtube/v3"
MAX_SEARCH_CANDIDATES = 15


class YouTubeCollector(BaseCollector):
    platform = "youtube"

    def search(self, params: SearchParams) -> list[Account]:
        channel_ids = self._discover_channel_ids(params)
        if not channel_ids:
            return []

        accounts = self._hydrate_channels(channel_ids)
        return self._apply_filters(accounts, params)

    def get_account(self, account_id: str) -> Account:
        accounts = self._hydrate_channels([account_id])
        if not accounts:
            raise AccountNotFoundError(f"youtube channel '{account_id}' not found")
        return accounts[0]

    # -- discovery ---------------------------------------------------

    def _discover_channel_ids(self, params: SearchParams) -> list[str]:
        if params.query_type == "username":
            channel_id = self._lookup_by_handle(params.query)
            if channel_id:
                return [channel_id]

        data = self._get("/search", {
            "part": "snippet",
            "type": "channel",
            "q": params.query,
            "maxResults": MAX_SEARCH_CANDIDATES,
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

    def _hydrate_channels(self, channel_ids: list[str]) -> list[Account]:
        channels_by_id: dict[str, dict] = {}
        for batch_start in range(0, len(channel_ids), 50):
            batch = channel_ids[batch_start: batch_start + 50]
            data = self._get("/channels", {
                "part": "snippet,statistics,contentDetails",
                "id": ",".join(batch),
            })
            for item in data.get("items", []):
                channels_by_id[item["id"]] = item

        accounts: list[Account] = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(self._build_account, channel_id, channel): channel_id
                for channel_id, channel in channels_by_id.items()
            }
            for future in as_completed(futures):
                account = future.result()
                if account is not None:
                    accounts.append(account)
        return accounts

    def _build_account(self, channel_id: str, channel: dict) -> Optional[Account]:
        try:
            snippet = channel.get("snippet", {})
            statistics = channel.get("statistics", {})
            content_details = channel.get("contentDetails", {})
            uploads_playlist_id = content_details.get("relatedPlaylists", {}).get("uploads")

            last_posted_at, engagement_rate = self._recent_activity(uploads_playlist_id, statistics)

            custom_url = snippet.get("customUrl", "")
            username = custom_url.lstrip("@") if custom_url else channel_id

            return Account(
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
        except Exception:
            logger.exception("failed to build account for channel %s", channel_id)
            return None

    def _recent_activity(self, uploads_playlist_id: Optional[str], statistics: dict) -> tuple[str, float]:
        default_last_posted = datetime.now(timezone.utc).isoformat()
        if not uploads_playlist_id:
            return default_last_posted, 0.0

        try:
            playlist_data = self._get("/playlistItems", {
                "part": "contentDetails",
                "playlistId": uploads_playlist_id,
                "maxResults": 5,
            })
        except UpstreamUnavailableError:
            return default_last_posted, 0.0

        video_ids = [
            item["contentDetails"]["videoId"]
            for item in playlist_data.get("items", [])
            if "videoId" in item.get("contentDetails", {})
        ]
        if not video_ids:
            return default_last_posted, 0.0

        try:
            videos_data = self._get("/videos", {
                "part": "snippet,statistics",
                "id": ",".join(video_ids),
            })
        except UpstreamUnavailableError:
            return default_last_posted, 0.0

        items = videos_data.get("items", [])
        if not items:
            return default_last_posted, 0.0

        last_posted_at = max(
            (item["snippet"]["publishedAt"] for item in items if "publishedAt" in item.get("snippet", {})),
            default=default_last_posted,
        )

        views = sum(int(item.get("statistics", {}).get("viewCount", 0) or 0) for item in items)
        interactions = sum(
            int(item.get("statistics", {}).get("likeCount", 0) or 0)
            + int(item.get("statistics", {}).get("commentCount", 0) or 0)
            for item in items
        )

        # 直近動画からの近似値であり、YouTube公式のエンゲージメント指標ではない
        engagement_rate = round((interactions / views) * 100, 2) if views else 0.0
        return last_posted_at, engagement_rate

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
