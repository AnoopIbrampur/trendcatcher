"""Common item shape every source adapter emits."""
from dataclasses import dataclass, field

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..config import USER_AGENT


class SourceSkipped(Exception):
    """Raised when a source can't run by design (e.g. no API key) rather than failing."""


@dataclass
class Item:
    source: str
    key: str  # stable id within the source (hashtag, article, video id, ...)
    title: str  # human-readable text used for clustering
    period: str  # YYYY-MM-DD the measurement refers to
    value: float  # primary popularity number (views, traffic, score)
    aux: float | None = None  # secondary number (post count, desktop share, comments)
    rank: int | None = None
    region: str = ""
    category: str = ""
    url: str = ""
    curve: list[float] | None = None
    extra: dict = field(default_factory=dict)


def session(user_agent: str = USER_AGENT) -> requests.Session:
    """HTTP session that backs off on 429/5xx and honours Retry-After."""
    s = requests.Session()
    s.headers["User-Agent"] = user_agent
    retry = Retry(total=5, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=None, respect_retry_after_header=True)
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s
