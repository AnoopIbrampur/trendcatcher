"""JSON API + static front end for the Trend Catcher web app.

    python -m trendcatcher serve            # http://localhost:8517

The front end (static/index.html) does all filtering client side, so the server only
builds a location's feed (cached for a few minutes) and generates briefs on request.
"""
import json
import math
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import db, llm
from ..angles import brief as make_brief
from ..config import COUNTRIES, US_STATES, location_name
from ..pipeline import build, mixed_feed

STATIC = Path(__file__).parent / "static"
CACHE_TTL = 600  # seconds

app = FastAPI(title="Trend Catcher")
_cache: dict[str | None, tuple[float, pd.DataFrame, pd.DataFrame]] = {}
_lock = threading.Lock()


def clean(o):
    """Make pandas/numpy output JSON-safe (NaN -> null, numpy scalars -> Python)."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, np.ndarray)):
        return [clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if math.isnan(o) or math.isinf(o) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if o is pd.NaT or (o is not None and not isinstance(o, (str, bool, int)) and pd.isna(o) is True):
        return None
    return o


def norm_location(location: str | None) -> str | None:
    if not location or location in ("global", "Global"):
        return None
    if location in COUNTRIES or (location.startswith("US-") and location[3:] in US_STATES):
        return location
    raise HTTPException(404, f"unknown location {location!r}")


def feed(location: str | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    with _lock:
        hit = _cache.get(location)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1], hit[2]
        clusters, items = build(db.connect(), location=location)
        clusters = mixed_feed(clusters)
        if "local" not in clusters:
            clusters = clusters.assign(local=False)
        _cache[location] = (time.time(), clusters, items)
        return clusters, items


ITEM_FIELDS = ["source", "key", "title", "url", "category", "regions", "value", "aux", "rise", "stage", "score",
               "curve", "detail", "local"]


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/locations")
def locations():
    return {
        "groups": [
            {"name": "Worldwide", "items": [{"code": None, "name": "Global"}]},
            {"name": "Countries", "items": [{"code": c, "name": n} for c, n in COUNTRIES.items()]},
            {"name": "US states", "items": [{"code": f"US-{s}", "name": n}
                                            for s, n in sorted(US_STATES.items(), key=lambda kv: kv[1])]},
        ]
    }


@app.get("/api/trends")
def trends(location: str | None = None):
    loc = norm_location(location)
    clusters, items = feed(loc)
    out = []
    briefed = _briefed_keys()
    for _, c in clusters.iterrows():
        members = items[items["cluster"] == c["cluster"]]
        out.append({
            "cluster_key": c["cluster_key"],
            "label": c["label"],
            "score": c["score"],
            "stage": c["stage"],
            "sources": c["sources"].split(", ") if c["sources"] else [],
            "category": [x for x in (c["category"] or "").split(", ") if x],
            "local": bool(c["local"]),
            "led_by": c.get("led_by") or None,
            "has_brief": _brief_key(c["cluster_key"], loc) in briefed,
            "members": [{f: m.get(f) for f in ITEM_FIELDS} for _, m in members.iterrows()],
        })
    return clean({
        "location": loc,
        "location_name": location_name(loc),
        "state_view": bool(loc and loc.startswith("US-")),
        "ollama": llm.available(),
        "count": len(out),
        "local_count": int(clusters["local"].sum()) if len(clusters) else 0,
        "trends": out,
    })


def _brief_key(cluster_key: str, loc: str | None) -> str:
    return cluster_key + (f"@{loc}" if loc else "")


def _briefed_keys() -> set[str]:
    return {r[0] for r in db.connect().execute("SELECT cluster_key FROM angles")}


class BriefRequest(BaseModel):
    cluster_key: str
    location: str | None = None
    refresh: bool = False


@app.get("/api/brief")
def get_brief(cluster_key: str, location: str | None = None):
    loc = norm_location(location)
    row = db.connect().execute("SELECT body, created_at, model FROM angles WHERE cluster_key=?",
                               (_brief_key(cluster_key, loc),)).fetchone()
    if not row:
        return {"brief": None}
    return {"brief": json.loads(row[0]), "created_at": row[1], "model": row[2]}


@app.post("/api/brief")
def post_brief(req: BriefRequest):
    loc = norm_location(req.location)
    clusters, items = feed(loc)
    match = clusters[clusters["cluster_key"] == req.cluster_key]
    if match.empty:
        raise HTTPException(404, "trend not found; refresh the feed")
    if not llm.available():
        raise HTTPException(503, "Ollama isn't running. Start it to generate briefs.")
    try:
        b = make_brief(match.iloc[0], items, db.connect(), refresh=req.refresh, location=loc)
    except llm.OllamaUnavailable as e:
        raise HTTPException(503, f"Local model failed: {e}")
    except (ValueError, KeyError) as e:
        raise HTTPException(502, f"Model returned something unusable: {e}")
    return {"brief": clean(b)}


@app.get("/api/health")
def health():
    con = db.connect()
    runs = db.runs(con, 60)
    hist = pd.read_sql_query(
        "SELECT source, COUNT(*) AS rows, COUNT(DISTINCT period) AS days, COUNT(DISTINCT region) AS regions,"
        " MIN(period) AS first, MAX(period) AS last, MAX(captured_at) AS last_capture FROM snapshots GROUP BY source",
        con)
    latest = pd.read_sql_query(
        "SELECT r.source, r.status, r.started_at, r.n_items, r.message, ok.last_ok FROM runs r"
        " JOIN (SELECT source, MAX(id) AS id FROM runs WHERE status != 'running' GROUP BY source) m ON r.id = m.id"
        " LEFT JOIN (SELECT source, MAX(started_at) AS last_ok FROM runs WHERE status = 'ok' GROUP BY source) ok"
        " ON ok.source = r.source", con)
    return clean({
        "ollama": llm.available(),
        "sources": hist.merge(latest, on="source", how="outer").to_dict("records"),
        "runs": runs[["source", "started_at", "status", "n_items", "message"]].to_dict("records"),
    })


@app.post("/api/refresh")
def refresh(location: str | None = None):
    with _lock:
        _cache.pop(norm_location(location), None)
    return {"ok": True}
