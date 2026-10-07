"""Wikipedia most-viewed articles (Wikimedia pageviews API), worldwide and per country.

Wikipedia is a clean signal for "a topic just broke into the mainstream". Two lists:
  - the worldwide English top-1000 (region "en"), which the model was backtested on
  - per-country top lists (region = ISO country code), covering every project; we keep
    English plus the country's local-language wiki (e.g. Hindi for India)

Lists are published once per UTC day, so this source is date-driven: each run fetches any
recent (region, date) pairs not yet stored. Backfill gives the scorer history from day one.
"""
import re
import time
from datetime import date, timedelta

from ..config import WIKI_COUNTRIES, WIKI_DEFAULT_PROJECTS, WIKI_GLOBAL_REGION, WIKI_PROJECTS
from .base import Item, session

TOP_URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/{access}/{y}/{m:02d}/{d:02d}"
COUNTRY_URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/top-per-country/{cc}/{access}/{y}/{m:02d}/{d:02d}"
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
# Namespace pages in any language ("विशेष:खोज", "Spezial:Suche"): a prefix glued to the colon.
# Real titles with colons have an underscore after it ("Drishyam:_The_Conclusion").
NAMESPACE_PATTERN = re.compile(r"^[^:_]+:[^_]")


def keep(article: str) -> bool:
    name = article.split("|", 1)[-1]  # strip a "hi.wikipedia|" project prefix
    return (name not in SKIP_TITLES and not name.startswith(SKIP_PREFIXES) and not SKIP_PATTERN.match(name)
            and not NAMESPACE_PATTERN.match(name))


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
                region=WIKI_GLOBAL_REGION,
                url=f"https://en.wikipedia.org/wiki/{name}",
                extra={"list_cutoff": cutoff},
            )
        )
    return items


def parse_country(all_access: dict, desktop: dict | None, day: date, country: str) -> list[Item]:
    """Per-country list. Views are rounded up by Wikimedia (`views_ceil`) for privacy."""
    projects = WIKI_PROJECTS.get(country, WIKI_DEFAULT_PROJECTS)
    arts = all_access["items"][0]["articles"]
    cutoff = min(a["views_ceil"] for a in arts) if arts else 0

    def key(a) -> str:
        return a["article"] if a["project"] == "en.wikipedia" else f"{a['project']}|{a['article']}"

    desk = {}
    if desktop:
        desk = {key(a): a["views_ceil"] for a in desktop["items"][0]["articles"]}
    items = []
    for a in arts:
        if a["project"] not in projects or not keep(a["article"]):
            continue
        k = key(a)
        lang = a["project"].split(".")[0]
        items.append(
            Item(
                source="wikipedia",
                key=k,
                title=a["article"].replace("_", " "),
                period=day.isoformat(),
                value=float(a["views_ceil"]),
                aux=desk.get(k, 0) / a["views_ceil"] if a["views_ceil"] else None,
                rank=a["rank"],
                region=country,
                url=f"https://{lang}.wikipedia.org/wiki/{a['article']}",
                extra={"list_cutoff": cutoff, "project": a["project"]},
            )
        )
    return items


class Wikipedia:
    name = "wikipedia"

    def __init__(self, countries=None, pause: float = 1.0):
        self.http = session()
        self.pause = pause
        self.countries = WIKI_COUNTRIES if countries is None else countries

    def regions(self) -> list[str]:
        return [WIKI_GLOBAL_REGION, *self.countries]

    def _get(self, url: str) -> dict | None:
        r = self.http.get(url, timeout=30)
        if r.status_code == 404:  # not published yet
            return None
        r.raise_for_status()
        time.sleep(self.pause)
        return r.json()

    def fetch_day(self, day: date, region: str = WIKI_GLOBAL_REGION) -> list[Item]:
        y, m, d = day.year, day.month, day.day
        if region == WIKI_GLOBAL_REGION:
            allv = self._get(TOP_URL.format(access="all-access", y=y, m=m, d=d))
            return parse(allv, self._get(TOP_URL.format(access="desktop", y=y, m=m, d=d)), day) if allv else []
        allv = self._get(COUNTRY_URL.format(cc=region, access="all-access", y=y, m=m, d=d))
        if allv is None:
            return []
        desk = self._get(COUNTRY_URL.format(cc=region, access="desktop", y=y, m=m, d=d))
        return parse_country(allv, desk, day, region)

    def missing_days(self, have: set[str], lookback: int = 3, today: date | None = None) -> list[date]:
        today = today or date.today()
        days = [today - timedelta(days=i) for i in range(1, lookback + 1)]
        return [d for d in reversed(days) if d.isoformat() not in have]

    def fetch(self, have: dict[str, set[str]] | None = None, lookback: int = 3) -> list[Item]:
        have = have or {}
        out = []
        for region in self.regions():
            for day in self.missing_days(have.get(region, set()), lookback):
                out += self.fetch_day(day, region)
        return out
