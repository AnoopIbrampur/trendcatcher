"""TikTok Creative Center trending hashtags (public, unauthenticated endpoint)."""
import time
from datetime import datetime, timezone

from ..config import BROWSER_UA, TIKTOK_COUNTRIES, TIKTOK_INDUSTRIES
from .base import Item, session

URL = "https://ads.tiktok.com/CreativeOne/KnowledgeAPI/GetHashtagList"
HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/plain, */*",
    "agw-js-conv": "str",
    "origin": "https://ads.tiktok.com",
    "referer": "https://ads.tiktok.com/creative/creativeCenter/trends/hashtag",
}


def parse(payload: dict, region: str, category: str, time_range: int) -> list[Item]:
    status = payload.get("BaseResp", {}).get("StatusCode", 0)
    if status != 0:
        raise RuntimeError(f"TikTok API error: {payload.get('BaseResp')}")
    items = []
    for h in payload.get("items") or []:
        curve_pts = sorted(h.get("popularityCurve") or [], key=lambda p: int(p["timestamp"]))
        curve = [float(p["value"]) for p in curve_pts]
        period = (
            datetime.fromtimestamp(int(curve_pts[-1]["timestamp"]), tz=timezone.utc).date().isoformat()
            if curve_pts
            else datetime.now(timezone.utc).date().isoformat()
        )
        name = h["hashtagName"]
        items.append(
            Item(
                source="tiktok",
                key=name.lower(),
                title=name,
                period=period,
                value=float(h.get("vv") or 0),
                aux=float(h.get("publishCnt") or 0),
                rank=int(h["rankIndex"]) if h.get("rankIndex") else None,
                region=region,
                category=category,
                url=f"https://www.tiktok.com/tag/{name}",
                curve=curve,
                extra={"time_range": time_range, "industry_ids": h.get("industryIDs", [])},
            )
        )
    return items


class TikTok:
    name = "tiktok"

    def __init__(self, countries=None, time_range: int = 7, pause: float = 0.5):
        self.countries = countries or TIKTOK_COUNTRIES
        self.time_range = time_range
        self.pause = pause
        self.http = session(BROWSER_UA)
        self.http.headers.update(HEADERS)

    def _query(self, country: str, industry_id: str | None) -> dict:
        body = {"timeRange": self.time_range, "countryCode": country, "page": 1, "limit": 20}
        if industry_id:
            body["industryID"] = industry_id
        r = self.http.post(URL, json=body, timeout=30)
        r.raise_for_status()
        return r.json()

    def fetch(self) -> list[Item]:
        out: list[Item] = []
        for country in self.countries:
            for iid, label in [(None, "All"), *TIKTOK_INDUSTRIES.items()]:
                out += parse(self._query(country, iid), country, label, self.time_range)
                time.sleep(self.pause)
        if not out:
            raise RuntimeError("TikTok returned no hashtags; endpoint may have changed")
        return out
