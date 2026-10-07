"""Turn a trend cluster into a creator brief with a local LLM.

The model's knowledge is older than the trend by definition, so the prompt carries the
evidence we collected (platform, metrics, news headlines, Wikipedia title) and the model
is told to say "unclear" rather than invent what a hashtag means. A bare hashtag gets a
name-matched Wikipedia article as a hint when one clearly exists.
"""
import json
from urllib.parse import quote

import pandas as pd
import requests

from . import db, llm
from .config import LLM_MODEL, location_name
from .sources.base import session

SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{}"
SEARCH_URL = "https://en.wikipedia.org/w/api.php"


_http = session()  # retries 429/5xx with backoff
_summaries: dict[str, str] = {}
_lookups: dict[str, tuple[str, str] | None] = {}


def wiki_summary(article: str) -> str:
    """Article intro. Only successful answers are cached, so a rate-limited call is retried next time."""
    if article in _summaries:
        return _summaries[article]
    try:
        r = _http.get(SUMMARY_URL.format(quote(article, safe="")), timeout=15)
    except requests.RequestException:
        return ""
    if r.status_code == 404:
        _summaries[article] = ""
    elif r.ok:
        _summaries[article] = r.json().get("extract", "")[:400]
    return _summaries.get(article, "")

def name_match(query: str, title: str) -> bool:
    """Accept a search hit only if it really names the same thing.

    Every word of the query must appear in the title (minus any parenthetical), so
    "balloon fiesta" matches "Albuquerque International Balloon Fiesta" but not
    "Hot air balloon". A one-word query must equal the title, because single words
    ("doggie", "pregnant") hit dictionary-style or unrelated articles.
    """
    from .cluster import tokens
    q, t = tokens(query), tokens(title)
    if not q or not q <= t:
        return False
    return len(q) >= 2 or q == t


def wiki_lookup(query: str) -> tuple[str, str] | None:
    """(title, summary) of the best Wikipedia article for a hashtag or search, if one clearly matches."""
    if query in _lookups:
        return _lookups[query]
    try:
        r = _http.get(SEARCH_URL, timeout=15, params={
            "action": "query", "list": "search", "srsearch": query, "srlimit": 5, "format": "json", "srprop": ""})
        r.raise_for_status()
        hits = [h["title"] for h in r.json().get("query", {}).get("search", [])]
    except (requests.RequestException, ValueError):
        return None  # transient: don't cache
    result = None
    for title in hits:
        if "(disambiguation)" in title or not name_match(query, title):
            continue
        summary = wiki_summary(title.replace(" ", "_"))
        if summary and "may refer to" not in summary[:200]:
            result = (title, summary)
        break
    _lookups[query] = result
    return result


PROMPT = """You help short-form video creators (Instagram Reels, TikTok, YouTube Shorts) decide
whether to make a video about a trend. Use ONLY the evidence below plus the plain meaning
of the label (e.g. #halloweencostume = people posting Halloween costume videos). If the
label is ambiguous and the evidence does not explain it (an unfamiliar name, slang, an
in-joke), set "what_it_is" to "unclear". Never invent events, people's actions, quotes,
or the reason something is in the news.

Audience location: {location}{local_note}
Trend label: {label}
Lifecycle stage (from our metrics): {stage}
Note: TikTok's industry tag hints at what a hashtag is about. If items in the evidence seem to
be about different things (e.g. a film vs. a vehicle hashtag), say so in "what_it_is".
Seen on: {sources}
Evidence:
{evidence}

Return JSON with exactly these keys:
{{
  "what_it_is": "one sentence, or 'unclear'",
  "creator_fit": 0-10 integer (10 = easy, safe, broad-appeal short video; 0 = not filmable
                  or only suitable for news outlets, e.g. tragedies, elections, live scores),
  "brand_safety": "safe" | "caution" | "avoid"
                  (avoid = real tragedies, deaths, crimes, sexual content, hate; caution = politics,
                   real controversies, health claims; safe = everything else, including films/games
                   with dark fictional plots),
  "safety_reason": "short",
  "best_niches": ["up to 3 creator niches"],
  "angles": [
    {{"hook": "first 2 seconds, spoken or on-screen", "format": "e.g. POV, tutorial, duet, reaction, list", "niche": "who should make it"}}
  ]  (exactly 3 angles, even when creator_fit is low)
}}"""


def evidence_for(cluster_row: pd.Series, items: pd.DataFrame, lookup: bool = True) -> str:
    lines = []
    members = items[items["cluster"] == cluster_row["cluster"]]
    for _, it in members.iterrows():
        d = it["detail"] or {}
        if it["source"] == "tiktok":
            lines.append(f"- TikTok hashtag {it['title']} (industry: {it['category']}; regions: {it['regions']}): "
                         f"{d.get('views', 0):,.0f} views, {d.get('posts', 0):,.0f} posts in the last 7 days")
        elif it["source"] == "wikipedia":
            ratio = d.get("ratio")
            lines.append(f"- Wikipedia article '{it['title']}': {d.get('views', 0):,.0f} views yesterday"
                         + (f", {ratio:.1f}x its 7-day baseline" if ratio else ""))
            if summary := wiki_summary(it["key"]):
                lines.append(f"    article summary: {summary}")
        elif it["source"] == "google_trends":
            lines.append(f"- Google search trend '{it['title']}' ({it['regions']}), ~{d.get('traffic', 0):,.0f}+ searches")
            for n in (d.get("news") or [])[:3]:
                lines.append(f"    news: {n.get('title')} ({n.get('source')})")
        elif it["source"] == "youtube":
            lines.append(f"- YouTube trending video '{it['title']}' by {d.get('channel')} ({it['category']}): "
                         f"{it['value']:,.0f} views, ~{d.get('per_hour', 0):,.0f}/hour since upload")
            if d.get("tags"):
                lines.append(f"    tags: {', '.join(d['tags'][:8])}")
        else:
            lines.append(f"- {it['source']}: {it['title']}")
    # Nothing explains a bare hashtag or a search with no news: try to name it via Wikipedia.
    explained = (members["source"] == "wikipedia").any() or any(
        (d or {}).get("news") for d in members.loc[members["source"] == "google_trends", "detail"])
    if lookup and not explained and len(members):
        top = members.sort_values("score", ascending=False).iloc[0]
        if top["source"] in ("tiktok", "google_trends"):
            # the word-split text ("balloon fiesta") and the raw tag ("jimothy", which the splitter
            # would turn into "jim othy") can each be the one that matches an article title
            queries = dict.fromkeys([top["text"], str(top["title"]).lstrip("#")])
            hit = next((h for q in queries if (h := wiki_lookup(q))), None)
            if hit:
                lines.append(f"- Possibly related Wikipedia article (matched by name only, may be a different "
                             f"thing): '{hit[0]}': {hit[1]}")
    return "\n".join(lines[:16])


def brief(cluster_row: pd.Series, items: pd.DataFrame, con=None, refresh: bool = False,
          location: str | None = None) -> dict:
    con = con or db.connect()
    key = cluster_row["cluster_key"] + (f"@{location}" if location else "")
    if not refresh:
        row = con.execute("SELECT body FROM angles WHERE cluster_key=?", (key,)).fetchone()
        if row:
            return json.loads(row[0])
    local_note = (" (this trend is specific to this location; angles should speak to a local audience)"
                  if location and cluster_row.get("local") else "")
    prompt = PROMPT.format(label=cluster_row["label"], stage=cluster_row["stage"], sources=cluster_row["sources"],
                           evidence=evidence_for(cluster_row, items), location=location_name(location),
                           local_note=local_note)
    out = llm.generate_json(prompt)
    con.execute("INSERT OR REPLACE INTO angles (cluster_key, created_at, model, body) VALUES (?, ?, ?, ?)",
                (key, db.utcnow(), LLM_MODEL, json.dumps(out)))
    con.commit()
    return out
