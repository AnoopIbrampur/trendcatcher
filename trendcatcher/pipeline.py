"""Snapshots -> scored items -> cross-platform trend clusters."""
from datetime import date, timedelta

import pandas as pd

from . import db
from .cluster import cluster as cluster_items
from .config import COUNTRIES, WIKI_GLOBAL_REGION, parent_location
from .score import OUT_COLS, SCORERS

# How much history each scorer needs to see
HISTORY_DAYS = {"wikipedia": 90, "tiktok": 14, "google_trends": 4, "youtube": 2, "reddit": 2}


def region_snaps(source: str, snaps: pd.DataFrame, location: str | None) -> pd.DataFrame:
    """Which stored rows describe `location`.

    Global: worldwide Wikipedia, country-level Google Trends (states would double count),
    every country for TikTok and YouTube. Country: that country everywhere. US state:
    the state's Google Trends, with other sources falling back to US national, since
    no other source has sub-country data.
    """
    if location is None:
        if source == "wikipedia":
            return snaps[snaps["region"] == WIKI_GLOBAL_REGION]
        if source == "google_trends":
            return snaps[~snaps["region"].str.contains("-", regex=False)]
        return snaps
    parent = parent_location(location)
    if parent and source != "google_trends":
        return snaps[snaps["region"] == parent]
    return snaps[snaps["region"] == location]


# A state trend counts as local if it isn't national and trends in at most this many other states
MAX_OTHER_STATES = 3


def is_local(source: str, location: str | None, keys: pd.Series, snaps: pd.DataFrame) -> pd.Series:
    """Is each trend specific to `location`, judged against recent rows from other places?

    Country: not trending in any other tracked country.
    US state: not in the US national list and trending in <= MAX_OTHER_STATES other states.
    Only Google Trends has state data, so other sources are never local in a state view.
    """
    false = pd.Series(False, index=keys.index)
    if location is None:
        return false
    recent = snaps[snaps["period"] >= (date.today() - timedelta(days=3)).isoformat()]
    parent = parent_location(location)
    if parent:
        if source != "google_trends":
            return false
        national = set(recent[recent["region"] == parent]["key"])
        others = recent[recent["region"].str.startswith(f"{parent}-") & (recent["region"] != location)]
        n_states = others.groupby("key")["region"].nunique()
        return ~keys.isin(national) & (keys.map(n_states).fillna(0) <= MAX_OTHER_STATES)
    elsewhere = set(recent[recent["region"].isin([c for c in COUNTRIES if c != location])]["key"])
    return ~keys.isin(elsewhere)


def score_all(con, location: str | None = None) -> pd.DataFrame:
    frames = []
    for src, fn in SCORERS.items():
        since = (date.today() - timedelta(days=HISTORY_DAYS[src])).isoformat()
        snaps = db.load(con, src, since_period=since)
        if snaps.empty:
            continue
        mine = region_snaps(src, snaps, location)
        if mine.empty:
            continue
        scored = fn(mine)
        if scored.empty:
            continue
        scored["local"] = is_local(src, location, scored["key"], snaps).values
        frames.append(scored)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=[*OUT_COLS, "local"])


def build(con=None, cluster: bool = True, min_score: float = 0.0,
          location: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    from . import fit
    con = con or db.connect()
    items = score_all(con, location)
    items = items[items["score"] >= min_score]
    clusters, items = cluster_items(items, con, use_embeddings=cluster)
    return fit.apply(clusters, fit.load(con)), items


def mixed_feed(clusters: pd.DataFrame, n: int | None = None) -> pd.DataFrame:
    """Round-robin across primary sources so one platform can't flood the feed.

    Scores are percentiles within each source, so the #3 TikTok trend and the #3
    Wikipedia breakout are comparably strong; interleaving by within-source rank
    reflects that better than a global sort. Trends local to the chosen location go
    first, then cross-platform clusters.
    """
    if clusters.empty:
        return clusters
    c = clusters.copy()
    c["_rank"] = c.groupby("primary")["score"].rank(ascending=False, method="first")
    c = c.sort_values(["_rank", "n_sources", "score"], ascending=[True, False, False])
    local = c["local"] if "local" in c else pd.Series(False, index=c.index)
    first = c[local].sort_values("score", ascending=False)
    multi = c[~local & (c["n_sources"] > 1)].sort_values("score", ascending=False)
    rest = c[~local & (c["n_sources"] == 1)]
    out = pd.concat([first, multi, rest]).drop(columns="_rank").reset_index(drop=True)
    return out.head(n) if n else out
