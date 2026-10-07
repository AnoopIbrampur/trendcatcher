"""Snapshots -> scored items -> cross-platform trend clusters."""
from datetime import date, timedelta

import pandas as pd

from . import db
from .cluster import cluster as cluster_items
from .score import OUT_COLS, SCORERS

# How much history each scorer needs to see
HISTORY_DAYS = {"wikipedia": 90, "tiktok": 14, "google_trends": 4, "youtube": 2, "reddit": 2}


def score_all(con) -> pd.DataFrame:
    frames = []
    for src, fn in SCORERS.items():
        since = (date.today() - timedelta(days=HISTORY_DAYS[src])).isoformat()
        snaps = db.load(con, src, since_period=since)
        if len(snaps):
            frames.append(fn(snaps))
    frames = [f for f in frames if len(f)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=OUT_COLS)


def build(con=None, cluster: bool = True, min_score: float = 0.0) -> tuple[pd.DataFrame, pd.DataFrame]:
    con = con or db.connect()
    items = score_all(con)
    items = items[items["score"] >= min_score]
    return cluster_items(items, con, use_embeddings=cluster)


def mixed_feed(clusters: pd.DataFrame, n: int | None = None) -> pd.DataFrame:
    """Round-robin across primary sources so one platform can't flood the feed.

    Scores are percentiles within each source, so the #3 TikTok trend and the #3
    Wikipedia breakout are comparably strong; interleaving by within-source rank
    reflects that better than a global sort. Cross-platform clusters go first.
    """
    if clusters.empty:
        return clusters
    c = clusters.copy()
    c["_rank"] = c.groupby("primary")["score"].rank(ascending=False, method="first")
    c = c.sort_values(["_rank", "n_sources", "score"], ascending=[True, False, False])
    multi = c[c["n_sources"] > 1].sort_values("score", ascending=False)
    out = pd.concat([multi, c[c["n_sources"] == 1]]).drop(columns="_rank").reset_index(drop=True)
    return out.head(n) if n else out
