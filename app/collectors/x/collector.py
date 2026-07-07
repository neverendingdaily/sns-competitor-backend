from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Callable, Optional

from app import config
from app.collectors.base import BaseCollector
from app.collectors.common.quality_gate import passes_universal_quality_gate
from app.collectors.x import discovery_search_engine, discovery_togetter, profile_scraper
from app.collectors.x import session as x_session
from app.errors import AccountNotFoundError, UpstreamUnavailableError
from app.models import Account, SearchParams

logger = logging.getLogger(__name__)

# Xのデフォルト（未設定）アバター画像のURLに含まれる既知のマーカー。
# アイコン未設定のままの、いわゆる「タマゴアイコン」はスパム/放置アカウントの
# 強いシグナルであるため品質フィルタで除外する。
DEFAULT_AVATAR_MARKERS = ("default_profile_images", "default_profile_normal")


class XCollector(BaseCollector):
    platform = "x"

    def search(self, params: SearchParams) -> list[Account]:
        if params.max_results == 0:
            # 呼び出し元がこのプラットフォームの検索を明示的にスキップしたい場合
            # （フロントエンドのプラットフォーム別取得件数設定で0を指定した場合）。
            return []

        if params.query_type == "username":
            # ユーザー名が既知の場合はDiscovery（Togetter/DDG）を経由せず直接候補にする。
            # YouTubeの_lookup_by_handle相当（app/collectors/youtube.py参照）。
            username = params.query.strip().lstrip("@")
            candidates = [username] if username else []
        else:
            candidates = self._discover_candidates(params.query, params.max_results)
        if not candidates:
            return []

        session = x_session.get_session()
        accounts: list[Account] = []

        with ThreadPoolExecutor(max_workers=config.X_HYDRATE_CONCURRENCY) as executor:
            futures = {
                executor.submit(profile_scraper.fetch_profile, username, session): username
                for username in candidates
            }
            for future in as_completed(futures):
                username = futures[future]
                try:
                    account = future.result()
                except UpstreamUnavailableError:
                    logger.warning("skipping candidate %s: x.com unreachable", username)
                    continue

                if account is not None:
                    accounts.append(account)

                if len(accounts) >= config.X_SEARCH_TARGET_COUNT:
                    # 未着手のfutureのみキャンセルされる（実行中のものは最後まで走る）。
                    for pending in futures:
                        pending.cancel()
                    break

        accounts = [a for a in accounts if self._is_quality_account(a)]
        return self._apply_filters(accounts, params)

    def get_account(self, account_id: str) -> Account:
        session = x_session.get_session()
        account = profile_scraper.fetch_profile(account_id, session)
        if account is None:
            raise AccountNotFoundError(f"x account '{account_id}' not found")
        return account

    # -- discovery -----------------------------------------------------

    def _discover_candidates(self, query: str, max_results: Optional[int] = None) -> list[str]:
        limit = max_results if max_results is not None else config.X_DISCOVERY_MAX_CANDIDATES

        with ThreadPoolExecutor(max_workers=2) as executor:
            togetter_future = executor.submit(
                self._safe_discover, "togetter", discovery_togetter.discover_candidates, query, limit
            )
            search_engine_future = executor.submit(
                self._safe_discover,
                "search engine",
                discovery_search_engine.discover_candidates,
                query,
                limit,
            )
            togetter_candidates = togetter_future.result()
            search_engine_candidates = search_engine_future.result()

        candidates: list[str] = []
        seen: set[str] = set()
        for username in [*togetter_candidates, *search_engine_candidates]:
            if username in seen:
                continue
            seen.add(username)
            candidates.append(username)
            if len(candidates) >= limit:
                break

        return candidates

    @staticmethod
    def _safe_discover(
        source: str, discover_fn: Callable[[str, int], list[str]], query: str, limit: int
    ) -> list[str]:
        try:
            return discover_fn(query, limit)
        except Exception:
            logger.exception("%s discovery failed for query=%s", source, query)
            return []

    # -- quality filter --------------------------------------------------
    # アフィリエイトのモデリング対象として不適切な、スパム・放置・情報不足の
    # アカウントを検索結果から除外する（ユーザーが`filters`で明示的に指定する
    # `_apply_filters`とは別の、常時適用される足切り）。`get_account`（既知の
    # 1アカウントの詳細取得）には適用しない — 指定されたアカウントを見たいという
    # 意図を尊重し、品質判定で勝手に404扱いにはしない。

    def _is_quality_account(self, account: Account) -> bool:
        # 投稿数0・フォロワー数不足・FF比1.0未満・スパムキーワードは全プラット
        # フォーム共通の判定（app/collectors/common/quality_gate.py）。
        if not passes_universal_quality_gate(
            account, min_followers=config.X_MIN_FOLLOWERS, min_ff_ratio=config.X_MIN_FF_RATIO
        ):
            return False
        if not account.bio.strip():
            # プロフィール自己紹介が空＝プロフィールの充実度が低いスパム/放置アカウントの
            # 典型的なシグナル（X固有の追加チェック）。
            return False
        if not account.avatar_url or any(marker in account.avatar_url for marker in DEFAULT_AVATAR_MARKERS):
            # アバター未設定（デフォルトのタマゴアイコン、X固有の追加チェック）。
            return False
        if not self._is_recently_active(account.last_posted_at):
            return False
        return True

    @staticmethod
    def _is_recently_active(last_posted_at: str) -> bool:
        try:
            posted_at = datetime.fromisoformat(last_posted_at)
        except ValueError:
            # 解析できない場合は「活動中かどうか判断できない」だけであり、
            # 積極的に除外する根拠にはしない（フェイルソフト）。
            return True
        if posted_at.tzinfo is None:
            posted_at = posted_at.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - posted_at).days
        return age_days <= config.X_MAX_INACTIVE_DAYS

    # -- filters -------------------------------------------------------

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
