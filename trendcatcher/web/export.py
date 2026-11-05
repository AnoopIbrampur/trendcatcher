"""Static export of the web app, for hosting without a server (GitHub Pages).

    python -m trendcatcher export-site --store store --out _site

Imports the cloud `data` branch into a scratch database, builds every location's feed
with the same code the server uses, and writes the API responses as JSON files next to
a copy of the front end. The front end notices it's static (window.TC_STATIC) and reads
those files instead of calling /api/*.

What the hosted copy can't do: video briefs and creator-fit ratings need a local model
(Ollama), and cross-platform merging falls back to exact lexical matches without
embeddings. The page says so.
"""
import json
import logging
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .. import db
from ..cloud import import_store
from ..config import COUNTRIES, US_STATES
from ..pipeline import build, mixed_feed
from .server import STATIC, health_payload, locations_payload, trends_payload

log = logging.getLogger(__name__)

REPO_URL = "https://github.com/AnoopIbrampur/trendcatcher"


def all_locations() -> list[str | None]:
    return [None, *COUNTRIES, *(f"US-{s}" for s in US_STATES)]


def file_name(loc: str | None) -> str:
    return f"{loc or 'global'}.json"


def _write(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, separators=(",", ":")))


def static_index(built_at: str) -> str:
    """The front end with a flag that switches its data calls to the exported files."""
    html = (STATIC / "index.html").read_text()
    flag = (f"<script>window.TC_STATIC = {json.dumps({'builtAt': built_at, 'repo': REPO_URL})};</script>\n"
            "<meta name=\"description\" content=\"Trend Catcher: rising topics across TikTok, YouTube, Google and "
            "Wikipedia, scored for short-form creators. Rebuilt hourly.\">\n")
    return html.replace("</head>", flag + "</head>", 1)


def export_site(store: Path, out: Path, locations: list[str | None] | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with tempfile.TemporaryDirectory() as tmp:
        con = db.connect(Path(tmp) / "site.db")
        imported = import_store(con, store)
        log.info("imported %s", imported)
        counts = {}
        for loc in locations if locations is not None else all_locations():
            t0 = time.time()
            clusters, items = build(con, location=loc)
            clusters = mixed_feed(clusters)
            if "local" not in clusters:
                clusters = clusters.assign(local=False)
            body = trends_payload(clusters, items, loc, briefed=set(), ollama=False)
            _write(out / "data" / "trends" / file_name(loc), body)
            counts[loc or "global"] = body["count"]
            log.info("%-6s %4d trends (%.1fs)", loc or "global", body["count"], time.time() - t0)
        _write(out / "data" / "health.json", health_payload(con, ollama=False))
        con.close()
    _write(out / "data" / "locations.json", locations_payload())
    _write(out / "data" / "meta.json", {"built_at": built_at, "trends": counts})
    (out / "index.html").write_text(static_index(built_at))
    (out / ".nojekyll").write_text("")
    return {"built_at": built_at, "locations": len(counts), "trends": sum(counts.values()), **imported}
