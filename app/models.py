from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

Platform = Literal["x", "threads", "instagram", "tiktok", "youtube"]
QueryType = Literal["keyword", "hashtag", "category", "username"]


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Account(CamelModel):
    id: str = Field(..., description="アカウントの識別子（多くのプラットフォームではusernameをそのまま使用）")
    platform: Platform = Field(..., description="プラットフォーム種別（x/threads/instagram/tiktok/youtube）")
    username: str = Field(..., description="ユーザー名（@は含まない）")
    display_name: str = Field(..., description="表示名（プロフィールに設定されている名前）")
    bio: str = Field(..., description="プロフィール自己紹介文")
    followers: int = Field(..., description="フォロワー数。プラットフォーム・認証状態によっては0固定の場合がある")
    following: int = Field(..., description="フォロー数。プラットフォーム・認証状態によっては0固定の場合がある")
    posts_count: int = Field(..., description="投稿数")
    engagement_rate: float = Field(..., description="エンゲージメント率（近似値、算出方法はプラットフォームごとに異なる）")
    is_verified: bool = Field(..., description="認証バッジの有無。公開APIで取得不可なプラットフォームでは常にfalse")
    avatar_url: str = Field(..., description="アバター画像のURL")
    profile_url: str = Field(..., description="プロフィールページのURL")
    category: str = Field(..., description="アカウントのカテゴリ（取得できない場合は空文字）")
    last_posted_at: str = Field(..., description="最終投稿日時（ISO 8601形式）")


class SearchFilters(CamelModel):
    followers_min: Optional[int] = Field(None, description="フォロワー数の下限でアカウントを絞り込む")
    followers_max: Optional[int] = Field(None, description="フォロワー数の上限でアカウントを絞り込む")
    engagement_min: Optional[float] = Field(None, description="エンゲージメント率の下限でアカウントを絞り込む")
    verified_only: Optional[bool] = Field(None, description="trueの場合、認証バッジ付きアカウントのみに絞り込む")
    category: Optional[str] = Field(None, description="アカウントのカテゴリでアカウントを絞り込む")


class SearchParams(CamelModel):
    platform: Platform = Field(..., description="検索対象のプラットフォーム（x/threads/instagram/tiktok/youtube）")
    query: str = Field(..., description="検索語句。queryTypeに応じてキーワード・ハッシュタグ・カテゴリ名・ユーザー名として解釈される")
    query_type: QueryType = Field(..., description="検索方式（keyword/hashtag/category/username）")
    filters: SearchFilters = Field(..., description="検索結果を絞り込むための追加条件")
    max_results: Optional[int] = Field(
        None,
        ge=0,
        le=50,
        description=(
            "このリクエストに限り、発見（discovery）する候補数の上限を環境変数の既定値から上書きする。"
            "未指定の場合は環境変数の既定値を使用する。0を指定した場合はこのプラットフォームの検索処理自体を"
            "スキップし、空配列を即座に返す"
        ),
    )
