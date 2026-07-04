from fastapi import APIRouter, Depends

from app.auth import optional_bearer
from app.collectors.registry import get_collector
from app.models import Account, Platform, SearchParams

router = APIRouter()


@router.post(
    "/api/v1/accounts/search",
    response_model=list[Account],
    summary="アカウント検索",
    description=(
        "指定したプラットフォーム（X・YouTube・Instagram・TikTok・Threads）で、"
        "キーワード・ハッシュタグ・カテゴリ・ユーザー名のいずれかの条件に一致する"
        "アカウントを検索し、一覧で返します。フォロワー数やエンゲージメント率などの"
        "絞り込み条件（filters）も指定できます。"
    ),
)
def search_accounts(params: SearchParams, _auth: None = Depends(optional_bearer)) -> list[Account]:
    collector = get_collector(params.platform)
    return collector.search(params)


@router.get(
    "/api/v1/accounts/{platform}/{account_id}",
    response_model=Account,
    summary="アカウント詳細取得",
    description=(
        "指定したプラットフォームの単一アカウントについて、フォロワー数・投稿数・"
        "エンゲージメント率などの詳細プロフィール情報を取得します。"
    ),
)
def get_account(platform: Platform, account_id: str, _auth: None = Depends(optional_bearer)) -> Account:
    collector = get_collector(platform)
    return collector.get_account(account_id)
