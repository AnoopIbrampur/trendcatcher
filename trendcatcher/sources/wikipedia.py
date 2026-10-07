"""English Wikipedia most-viewed articles (Wikimedia pageviews API).

Wikipedia is a clean signal for "a topic just broke into the mainstream". The daily
top-1000 is published once per UTC day, so this source is date-driven: each run
fetches any recent dates not yet stored. It also supports backfill, which gives
the scorer history (and the backtest labels) from day one.
"""
import re
import time
from datetime import date, timedelta

from .base import Item, session

TOP_URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/{access}/{y}/{m:02d}/{d:02d}"
SKIP_PREFIXES = (
    "Special:", "Wikipedia:", "File:", "Portal:", "Help:", "Template:", "Category:",
    "Talk:", "User:", "Draft:", "Module:", "MediaWiki:",
)
SKIP_TITLES = {"Main_Page", "-", "Undefined"}
# calendar / year / obituary list pages spike on schedule, not because anything is trending
SKIP_PATTERN = re.compile(
    r"^((January|February|March|April|May|June|July|August|September|October|November|December)_\d{1,2}"
    r"|\d{4}|Deaths_in_.*|\d{4}_in_.*)$"
)


def keep(article: str) -> bool:
    return article not in SKIP_TITLES and not article.startswith(SKIP_PREFIXES) and not SKIP_PATTERN.match(article)


def parse(all_access: dict, desktop: dict | None, day: date) -> list[Item]:
    """Combine all-access and desktop tops. Desktop share near 1.0 usually means bot traffic."""
    desk = {}
    if desktop:
        desk = {a["article"]: a["views"] for a in desktop["items"][0]["articles"]}
    arts = all_access["items"][0]["articles"]
    cutoff = min(a["views"] for a in arts) if arts else 0
    items = []
    for a in arts:
        name = a["article"]
        if not keep(name):
            continue
        share = desk.get(name, 0) / a["views"] if a["views"] else None
        items.append(
            Item(
                source="wikipedia",
                key=name,
                title=name.replace("_", " "),
                period=day.isoformat(),
                value=float(a["views"]),
                aux=share,
                rank=a["rank"],
                region="en",
                url=f"https://en.wikipedia.org/wiki/{name}",
                extra={"list_cutoff": cutoff},
            )
        )
    return items


class Wikipedia:
    name = "wikipedia"

    def __init__(self, pause: float = 1.0):
        self.http = session()
        self.pause = pause

    def _top(self, access: str, day: date) -> dict | None:
        r = self.http.get(TOP_URL.format(access=access, y=day.year, m=day.month, d=day.day), timeout=30)
        if r.status_code == 404:  # not published yet
            return None
        r.raise_for_status()
        return r.json()

    def fetch_day(self, day: date) -> list[Item]:
        allv = self._top("all-access", day)
        if allv is None:
            return []
        time.sleep(self.pause)
        return parse(allv, self._top("desktop", day), day)

    def missing_days(self, have: set[str], lookback: int = 3, today: date | None = None) -> list[date]:
        today = today or date.today()
        days = [today - timedelta(days=i) for i in range(1, lookback + 1)]
        return [d for d in reversed(days) if d.isoformat() not in have]

    def fetch(self, have: set[str] | None = None, lookback: int = 3) -> list[Item]:
        out = []
        for day in self.missing_days(have or set(), lookback):
            out += self.fetch_day(day)
            time.sleep(self.pause)
        return out
