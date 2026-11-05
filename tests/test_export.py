import json
from datetime import date, datetime, timezone

from trendcatcher import cloud, db
from trendcatcher.sources.base import Item
from trendcatcher.web.export import all_locations, export_site, file_name


def test_all_locations_cover_every_view():
    locs = all_locations()
    assert locs[0] is None and "US" in locs and "US-NY" in locs
    assert len(locs) == 1 + 5 + 51
    assert file_name(None) == "global.json" and file_name("US-NY") == "US-NY.json"


def test_export_writes_static_site(tmp_path):
    today = date.today().isoformat()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    src = db.connect(tmp_path / "src.db")
    run = db.start_run(src, "google_trends")
    db.insert_items(src, run, [
        Item("google_trends", "eclipse", "eclipse", today, 50000.0, region="US"),
        Item("google_trends", "snow day", "snow day", today, 2000.0, region="US-NY"),
    ], captured_at=now)
    db.finish_run(src, run, "ok", 2)
    store = tmp_path / "store"
    cloud.export_snapshots(src, store)
    cloud.append_runs(src, store)

    out = tmp_path / "site"
    r = export_site(store, out, locations=[None, "US", "US-NY"])
    assert r["locations"] == 3 and r["files"] == 1  # one capture -> one snapshot file

    html = (out / "index.html").read_text()
    assert "window.TC_STATIC" in html and html.index("window.TC_STATIC") < html.index("</head>")
    assert (out / ".nojekyll").exists()
    us = json.loads((out / "data" / "trends" / "US.json").read_text())
    assert us["location"] == "US" and us["ollama"] is False
    assert "eclipse" in {t["label"] for t in us["trends"]}
    assert all(t["has_brief"] is False for t in us["trends"])
    ny = json.loads((out / "data" / "trends" / "US-NY.json").read_text())
    assert ny["state_view"] is True
    assert json.loads((out / "data" / "locations.json").read_text())["groups"][0]["name"] == "Worldwide"
    assert json.loads((out / "data" / "health.json").read_text())["runs"]
