"""Creator-fit pre-scoring: how filmable and brand-safe is each trend?

Trend scores measure attention, not whether a creator can do anything with it: a live
hockey score can be the hottest search of the hour. A small local model rates each trend
cluster 0-10 for creator fit plus a brand-safety call, ten trends per prompt, and the feed
ranking blends that in. Ratings are cached by trend label, so each label is rated once.
"""
import json
import re
import sqlite3

import pandas as pd

from . import db, llm
from .config import LLM_MODEL


# Compared on 20 real trends, the 2b model rated minor news figures and a mass shooter as
# moderately filmable; the 9b model knew better. Ratings are cached, so speed matters less.
FIT_MODEL = LLM_MODEL
UNSCORED_FIT = 6  # neutral prior for trends not rated yet
BATCH = 10

PROMPT = """Rate trending topics for short-form video creators (TikTok, Reels, Shorts).
For each topic, judge only from the label and the context given. Do not guess facts.

creator_fit (0-10):
  9-10  easy, broad-appeal video anyone can make (seasonal, fashion, food, pets, games, films, music)
  5-8   good for a specific niche (a sport, a fandom, a hobby)
  2-4   hard to make a video about (a niche person or event with little visual hook)
  0-1   only news outlets should cover it (live scores, elections, deaths, crimes, disasters)
brand_safety: "safe", "caution" (politics, controversy, health claims) or "avoid" (tragedy,
death, crime, sexual content, hate).

Topics:
{topics}

Return JSON: {{"ratings": [{{"id": <id>, "creator_fit": <0-10>, "brand_safety": "<safe|caution|avoid>"}}, ...]}}
with one entry per topic id."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS fit_scores (
    label_key TEXT PRIMARY KEY,
    fit INTEGER NOT NULL,
    safety TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def label_key(label: str) -> str:
    return re.sub(r"\s+", " ", str(label).lower().lstrip("#")).strip()


def context_line(cluster: pd.Series, items: pd.DataFrame) -> str:
    """One short line of evidence per trend: platforms, category, and the best headline we have."""
    members = items[items["cluster"] == cluster["cluster"]]
    bits = [", ".join(sorted(set(members["source"])))]
    if cluster.get("category"):
        bits.append(f"category: {cluster['category']}")
    for _, m in members.iterrows():
        d = m["detail"] or {}
        news = [n.get("title") for n in (d.get("news") or []) if n.get("title")]
        if news:
            bits.append(f"news: {news[0][:110]}")
            break
        if m["source"] == "youtube" and d.get("channel"):
            bits.append(f"YouTube channel: {d['channel']}")
            break
    return "; ".join(bits)


def rate(topics: list[tuple[int, str, str]], model: str = FIT_MODEL) -> dict[int, tuple[int, str]]:
    """topics: (id, label, context). Returns id -> (fit, safety); malformed entries are dropped."""
    lines = "\n".join(f"{i}. {label}  [{ctx}]" for i, label, ctx in topics)
    out = llm.generate_json(PROMPT.format(topics=lines), model=model, temperature=0.1)
    ratings = {}
    for r in out.get("ratings", []) if isinstance(out, dict) else []:
        try:
            i, fit = int(r["id"]), int(round(float(r["creator_fit"])))
            safety = str(r.get("brand_safety", "caution")).lower()
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= fit <= 10 and safety in ("safe", "caution", "avoid"):
            ratings[i] = (fit, safety)
    return ratings


def score_clusters(clusters: pd.DataFrame, items: pd.DataFrame, con=None, top: int = 60,
                   model: str = FIT_MODEL) -> int:
    """Rate the top `top` clusters that have no cached rating. Returns how many were rated."""
    con = con or db.connect()
    con.executescript(SCHEMA)
    done = {r[0] for r in con.execute("SELECT label_key FROM fit_scores")}
    by = "base_score" if "base_score" in clusters else "score"  # rate by attention, not by blended rank
    todo = [c for _, c in clusters.sort_values(by, ascending=False).iterrows()
            if label_key(c["label"]) not in done][:top]
    rated = 0
    for start in range(0, len(todo), BATCH):
        batch = todo[start:start + BATCH]
        topics = [(i + 1, c["label"], context_line(c, items)) for i, c in enumerate(batch)]
        try:
            ratings = rate(topics, model)
        except (llm.OllamaUnavailable, json.JSONDecodeError):
            continue
        now = db.utcnow()
        con.executemany(
            "INSERT OR REPLACE INTO fit_scores (label_key, fit, safety, model, created_at) VALUES (?, ?, ?, ?, ?)",
            [(label_key(c["label"]), *ratings[i + 1], model, now) for i, c in enumerate(batch) if i + 1 in ratings])
        con.commit()
        rated += sum(1 for i in range(len(batch)) if i + 1 in ratings)
    return rated


def load(con=None) -> dict[str, tuple[int, str]]:
    """Read-only: never takes a write lock, so the feed isn't blocked by a running rating job."""
    con = con or db.connect()
    try:
        return {k: (f, s) for k, f, s in con.execute("SELECT label_key, fit, safety FROM fit_scores")}
    except sqlite3.OperationalError:  # table not created yet
        return {}


def apply(clusters: pd.DataFrame, ratings: dict[str, tuple[int, str]]) -> pd.DataFrame:
    """Blend creator fit into the ranking score.

    rank score = attention score x (0.5 + 0.05 x fit), so fit 10 keeps the full score and
    fit 0 halves it; 'avoid' takes another 30% off. Unrated trends get a neutral fit of 6.
    The attention score is kept as base_score.
    """
    if clusters.empty:
        return clusters.assign(base_score=[], fit=[], safety=[])
    c = clusters.copy()
    keys = c["label"].map(label_key)
    c["fit"] = keys.map(lambda k: ratings.get(k, (None, None))[0])
    c["safety"] = keys.map(lambda k: ratings.get(k, (None, None))[1])
    fit = c["fit"].fillna(UNSCORED_FIT).astype(float)
    factor = (0.5 + 0.05 * fit) * c["safety"].map(lambda s: 0.7 if s == "avoid" else 1.0)
    c["base_score"] = c["score"]
    c["score"] = (c["score"] * factor).round(1)
    return c.sort_values(["score", "n_sources"], ascending=False).reset_index(drop=True)


def rate_all(top: int = 40, con=None) -> int:
    """Rate the top trends worldwide and in each country (labels already rated are skipped)."""
    from .config import COUNTRIES
    from .pipeline import build
    if not llm.available():
        return 0
    con = con or db.connect()
    total = 0
    for loc in [None, *COUNTRIES]:
        clusters, items = build(con, location=loc)
        total += score_clusters(clusters, items, con, top=top)
    return total
