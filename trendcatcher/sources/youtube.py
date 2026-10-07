"""YouTube Data API v3 most-popular chart. Needs YOUTUBE_API_KEY (free, 10k units/day)."""
import os
from datetime import datetime, timezone

from ..config import YOUTUBE_REGIONS
from .base import Item, SourceSkipped, session

URL = "https://www.googleapis.com/youtube/v3/videos"
# Standard YouTube video category IDs (US)
CATEGORIES = {
    "1": "Film & Animation", "2": "Autos & Vehicles", "10": "Music", "15": "Pets & Animals", "17": "Sports",
    "19": "Travel & Events", "20": "Gaming", "22": "People & Blogs", "23": "Comedy", "24": "Entertainment",
    "25": "News & Politics", "26": "Howto & Style", "27": "Education", "28": "Science & Technology",
    "29": "Nonprofits & Activism",
}


def parse(payload: dict, region: str, now: datetime | None = None) -> list[Item]:
    now = now or datetime.now(timezone.utc)
    items = []
    for rank, v in enumerate(payload.get("items", []), start=1):
        sn, st = v["snippet"], v.get("statistics", {})
        published = datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00"))
        items.append(
            Item(
                source="youtube",
                key=v["id"],
                title=sn["title"],
                period=now.date().isoformat(),
                value=float(st.get("viewCount", 0)),
                aux=float(st.get("likeCount", 0)) if "likeCount" in st else None,
                rank=rank,
                region=region,
                category=CATEGORIES.get(str(sn.get("categoryId", "")), str(sn.get("categoryId", ""))),
                url=f"https://www.youtube.com/watch?v={v['id']}",
                extra={"channel": sn.get("channelTitle"), "published_at": published.isoformat(),
                       "tags": (sn.get("tags") or [])[:10]},
            )
        )
    return items


class YouTube:
    name = "youtube"

    def __init__(self, regions=None):
        self.key = os.environ.get("YOUTUBE_API_KEY")
        self.regions = regions or YOUTUBE_REGIONS
        self.http = session()

    def fetch(self) -> list[Item]:
        if not self.key:
            raise SourceSkipped("YOUTUBE_API_KEY not set")
        out = []
        for region in self.regions:
            r = self.http.get(URL, params={"part": "snippet,statistics", "chart": "mostPopular",
                                           "regionCode": region, "maxResults": 50, "key": self.key}, timeout=30)
            r.raise_for_status()
            out += parse(r.json(), region)
        return out
