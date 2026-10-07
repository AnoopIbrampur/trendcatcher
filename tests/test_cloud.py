from trendcatcher import cloud, db
from trendcatcher.sources.base import Item


def _db(path, items, captured_at):
    con = db.connect(path)
    run = db.start_run(con, items[0].source)
    db.insert_items(con, run, items, captured_at=captured_at)
    db.finish_run(con, run, "ok", len(items))
    return con


def test_due_sources():
    assert cloud.due_sources(1) == ["google_trends", "reddit"]
    assert set(cloud.due_sources(0)) == {"google_trends", "youtube", "tiktok", "reddit"}
    assert "wikipedia" in cloud.due_sources(9)


def test_export_import_round_trip_and_idempotent(tmp_path):
    store = tmp_path / "store"
    src = _db(tmp_path / "a.db", [
        Item("wikipedia", "Jailer_2", "Jailer 2", "2026-10-05", 44900.0, aux=0.1, region="en", extra={"list_cutoff": 7000}),
        Item("wikipedia", "Jailer_2", "Jailer 2", "2026-10-05", 30000.0, region="IN", extra={"list_cutoff": 1100}),
    ], "2026-10-06T03:00:00+00:00")
    run = db.start_run(src, "tiktok")
    db.insert_items(src, run, [Item("tiktok", "x", "#x", "2026-10-06", 5.0, curve=[1, 2], extra={"a": 1})],
                    captured_at="2026-10-06T14:40:44+00:00")
    db.finish_run(src, run, "ok", 1)
    paths = cloud.export_snapshots(src, store)
    rels = sorted(p.relative_to(store).as_posix() for p in paths)
    assert rels == ["snapshots/tiktok/2026-10-06/144044.jsonl.gz",
                    "snapshots/wikipedia/IN/2026-10-05.jsonl.gz",
                    "snapshots/wikipedia/en/2026-10-05.jsonl.gz"]
    assert cloud.export_snapshots(src, store) == []  # immutable: nothing rewritten
    assert cloud.store_wiki_periods(store) == {"en": {"2026-10-05"}, "IN": {"2026-10-05"}}
    assert cloud.append_runs(src, store) == 2

    dst = db.connect(tmp_path / "b.db")
    r = cloud.import_store(dst, store)
    assert r == {"files": 3, "rows": 3, "runs": 2}
    assert cloud.import_store(dst, store) == {"files": 0, "rows": 0, "runs": 0}
    t = db.load(dst, "tiktok")
    assert t.loc[0, "curve"] == [1, 2] and t.loc[0, "extra"] == {"a": 1}
    assert set(db.load(dst, "wikipedia")["region"]) == {"en", "IN"}
    assert all(m.startswith("[cloud]") for m in db.runs(dst)["message"])


def test_seed_marks_exported_files_imported(tmp_path):
    store = tmp_path / "store"
    con = _db(tmp_path / "a.db", [Item("google_trends", "q", "q", "2026-10-06", 500.0, region="US")],
              "2026-10-06T12:00:00+00:00")
    assert cloud.seed(con, store) == 1
    assert cloud.import_store(con, store)["files"] == 0  # already in this database: no duplicates
