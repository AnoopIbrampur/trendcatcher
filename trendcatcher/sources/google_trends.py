"""Google Trends 'trending now' RSS feed."""
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from ..config import GOOGLE_TRENDS_GEOS
from .base import Item, session

URL = "https://trends.google.com/trending/rss?geo={geo}"
NS = {"ht": "https://trends.google.com/trending/rss"}


def parse_traffic(s: str | None) -> float:
    """'2,000+' -> 2000, '10K+' -> 10000, '1M+' -> 1e6."""
    if not s:
        return 0.0
    m = re.match(r"([\d.,]+)\s*([KkMm]?)", s.strip())
    if not m:
        return 0.0
    n = float(m.group(1).replace(",", ""))
    return n * {"": 1, "k": 1e3, "m": 1e6}[m.group(2).lower()]


def parse(xml_text: str, geo: str) -> list[Item]:
    root = ET.fromstring(xml_text)
    items = []
    for rank, it in enumerate(root.iter("item"), start=1):
        title = (it.findtext("title") or "").strip()
        if not title:
            continue
        pub = it.findtext("pubDate")
        pub_dt = parsedate_to_datetime(pub) if pub else None
        news = [
            {"title": n.findtext("ht:news_item_title", namespaces=NS), "url": n.findtext("ht:news_item_url", namespaces=NS),
             "source": n.findtext("ht:news_item_source", namespaces=NS)}
            for n in it.findall("ht:news_item", NS)
        ]
        items.append(
            Item(
                source="google_trends",
                key=title.lower(),
                title=title,
                period=pub_dt.date().isoformat() if pub_dt else "",
                value=parse_traffic(it.findtext("ht:approx_traffic", namespaces=NS)),
                rank=rank,
                region=geo,
                url=f"https://www.google.com/search?q={title.replace(' ', '+')}",
                extra={"pub_date": pub_dt.isoformat() if pub_dt else None, "news": news},
            )
        )
    return items


class GoogleTrends:
    name = "google_trends"

    def __init__(self, geos=None):
        self.geos = geos or GOOGLE_TRENDS_GEOS
        self.http = session()

    def fetch(self) -> list[Item]:
        out = []
        for geo in self.geos:
            r = self.http.get(URL.format(geo=geo), timeout=30)
            r.raise_for_status()
            out += parse(r.text, geo)
            time.sleep(0.3)  # 56 geos per run; stay polite
        return out
