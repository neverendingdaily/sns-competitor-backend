from app.collectors.base import BaseCollector
from app.collectors.instagram.collector import InstagramCollector
from app.collectors.threads.collector import ThreadsCollector
from app.collectors.tiktok.collector import TikTokCollector
from app.collectors.x.collector import XCollector
from app.collectors.youtube import YouTubeCollector
from app.models import Platform

_COLLECTORS: dict[Platform, BaseCollector] = {
    "youtube": YouTubeCollector(),
    "x": XCollector(),
    "instagram": InstagramCollector(),
    "tiktok": TikTokCollector(),
    "threads": ThreadsCollector(),
}


def get_collector(platform: Platform) -> BaseCollector:
    return _COLLECTORS[platform]
