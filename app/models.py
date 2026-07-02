from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

Platform = Literal["x", "threads", "instagram", "tiktok", "youtube"]
QueryType = Literal["keyword", "hashtag", "category", "username"]


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Account(CamelModel):
    id: str
    platform: Platform
    username: str
    display_name: str
    bio: str
    followers: int
    following: int
    posts_count: int
    engagement_rate: float
    is_verified: bool
    avatar_url: str
    profile_url: str
    category: str
    last_posted_at: str


class SearchFilters(CamelModel):
    followers_min: Optional[int] = None
    followers_max: Optional[int] = None
    engagement_min: Optional[float] = None
    verified_only: Optional[bool] = None
    category: Optional[str] = None


class SearchParams(CamelModel):
    platform: Platform
    query: str
    query_type: QueryType
    filters: SearchFilters
