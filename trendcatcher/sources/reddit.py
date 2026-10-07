"""Reddit rising posts via app-only OAuth. Needs REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET.

Unauthenticated .json endpoints return 403 these days, so this source is optional.
"""
import os
from datetime import datetime, timezone

from .base import Item, SourceSkipped, session

SUBREDDITS = ["all", "popular", "OutOfTheLoop", "memes", "TikTokCringe"]


def parse(payload: dict) -> list[Item]:
    items = []
    for rank, child in enumerate(payload.get("data", {}).get("children", []), start=1):
        p = child["data"]
        created = datetime.fromtimestamp(p["created_utc"], tz=timezone.utc)
        items.append(
            Item(
                source="reddit",
                key=p["id"],
                title=p["title"],
                period=created.date().isoformat(),
                value=float(p.get("score", 0)),
                aux=float(p.get("num_comments", 0)),
                rank=rank,
                category=p.get("subreddit", ""),
                url="https://www.reddit.com" + p.get("permalink", ""),
                extra={"created_utc": created.isoformat()},
            )
        )
    return items


class Reddit:
    name = "reddit"

    def __init__(self, subreddits=None):
        self.cid = os.environ.get("REDDIT_CLIENT_ID")
        self.secret = os.environ.get("REDDIT_CLIENT_SECRET")
        self.subreddits = subreddits or SUBREDDITS
        self.http = session()

    def fetch(self) -> list[Item]:
        if not (self.cid and self.secret):
            raise SourceSkipped("REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set")
        tok = self.http.post("https://www.reddit.com/api/v1/access_token", auth=(self.cid, self.secret),
                             data={"grant_type": "client_credentials"}, timeout=30)
        tok.raise_for_status()
        self.http.headers["Authorization"] = f"bearer {tok.json()['access_token']}"
        out = []
        for sub in self.subreddits:
            r = self.http.get(f"https://oauth.reddit.com/r/{sub}/rising", params={"limit": 100}, timeout=30)
            r.raise_for_status()
            out += parse(r.json())
        return out
