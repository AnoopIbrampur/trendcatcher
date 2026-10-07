"""Turn a trend cluster into a creator brief with a local LLM.

The model's knowledge is older than the trend by definition, so the prompt carries the
evidence we collected (platform, metrics, news headlines, Wikipedia title) and the model
is told to say "unclear" rather than invent what a hashtag means.
"""
import json
from functools import lru_cache
from urllib.parse import quote

import pandas as pd
import requests

from . import db, llm
from .config import LLM_MODEL, USER_AGENT

SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{}"


@lru_cache(maxsize=512)
def wiki_summary(article: str) -> str:
    try:
        r = requests.get(SUMMARY_URL.format(quote(article, safe="")), headers={"User-Agent": USER_AGENT}, timeout=15)
        return r.json().get("extract", "")[:400] if r.ok else ""
    except requests.RequestException:
        return ""

PROMPT = """You help short-form video creators (Instagram Reels, TikTok, YouTube Shorts) decide
whether to make a video about a trend. Use ONLY the evidence below plus the plain meaning
of the label (e.g. #halloweencostume = people posting Halloween costume videos). If the
label is ambiguous and the evidence does not explain it (an unfamiliar name, slang, an
in-joke), set "what_it_is" to "unclear". Never invent events, people's actions, quotes,
or the reason something is in the news.

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


def evidence_for(cluster_row: pd.Series, items: pd.DataFrame) -> str:
    lines = []
    for _, it in items[items["cluster"] == cluster_row["cluster"]].iterrows():
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
    return "\n".join(lines[:15])


def brief(cluster_row: pd.Series, items: pd.DataFrame, con=None, refresh: bool = False) -> dict:
    con = con or db.connect()
    key = cluster_row["cluster_key"]
    if not refresh:
        row = con.execute("SELECT body FROM angles WHERE cluster_key=?", (key,)).fetchone()
        if row:
            return json.loads(row[0])
    prompt = PROMPT.format(label=cluster_row["label"], stage=cluster_row["stage"], sources=cluster_row["sources"],
                           evidence=evidence_for(cluster_row, items))
    out = llm.generate_json(prompt)
    con.execute("INSERT OR REPLACE INTO angles (cluster_key, created_at, model, body) VALUES (?, ?, ?, ?)",
                (key, db.utcnow(), LLM_MODEL, json.dumps(out)))
    con.commit()
    return out
