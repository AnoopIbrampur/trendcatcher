"""Run every source that is due, store snapshots, log the outcome. Safe to run hourly."""
import logging
import traceback
from datetime import date, datetime, timedelta, timezone

from . import db
from .config import MIN_INTERVAL_HOURS
from .sources import ALL_SOURCES, SourceSkipped, Wikipedia

log = logging.getLogger("trendcatcher.ingest")


def is_due(con, name: str, force: bool) -> bool:
    if force or name not in MIN_INTERVAL_HOURS:
        return True
    last = db.last_ok(con, name)
    return last is None or datetime.now(timezone.utc) - last >= timedelta(hours=MIN_INTERVAL_HOURS[name]) - timedelta(minutes=5)


def run_source(con, src) -> tuple[str, int]:
    run_id = db.start_run(con, src.name)
    try:
        items = src.fetch(have=db.periods(con, "wikipedia")) if isinstance(src, Wikipedia) else src.fetch()
        n = db.insert_items(con, run_id, items)
        db.finish_run(con, run_id, "ok", n)
        return "ok", n
    except SourceSkipped as e:
        db.finish_run(con, run_id, "skipped", 0, str(e))
        return "skipped", 0
    except Exception as e:  # one broken source must never take down the others
        db.finish_run(con, run_id, "error", 0, f"{e}\n{traceback.format_exc()}")
        log.exception("source %s failed", src.name)
        return "error", 0


def ingest(only: list[str] | None = None, force: bool = False, con=None) -> dict[str, tuple[str, int]]:
    con = con or db.connect()
    results = {}
    for cls in ALL_SOURCES:
        if only and cls.name not in only:
            continue
        if not is_due(con, cls.name, force):
            results[cls.name] = ("not due", 0)
            continue
        results[cls.name] = run_source(con, cls())
    return results


def backfill_wikipedia(days: int, con=None) -> int:
    con = con or db.connect()
    src = Wikipedia()
    have = db.periods(con, "wikipedia")
    todo = [d for d in (date.today() - timedelta(days=i) for i in range(days, 0, -1)) if d.isoformat() not in have]
    total = 0
    for d in todo:
        run_id = db.start_run(con, "wikipedia")
        try:
            n = db.insert_items(con, run_id, src.fetch_day(d))
            db.finish_run(con, run_id, "ok", n, f"backfill {d}")
            total += n
        except Exception as e:
            db.finish_run(con, run_id, "error", 0, f"backfill {d}: {e}")
    return total
