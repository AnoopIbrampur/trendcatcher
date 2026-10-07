"""SQLite storage: append-only snapshots plus a run log."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import DB_PATH
from .sources.base import Item

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    key TEXT NOT NULL,
    title TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    period TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    rank INTEGER,
    value REAL,
    aux REAL,
    url TEXT,
    curve TEXT,
    extra TEXT
);
CREATE INDEX IF NOT EXISTS ix_snap_source_key ON snapshots(source, key);
CREATE INDEX IF NOT EXISTS ix_snap_source_period ON snapshots(source, period);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    status TEXT NOT NULL,       -- ok | skipped | error
    n_items INTEGER DEFAULT 0,
    message TEXT
);
CREATE TABLE IF NOT EXISTS embeddings (
    text TEXT NOT NULL,
    model TEXT NOT NULL,
    vec TEXT NOT NULL,
    PRIMARY KEY (text, model)
);
CREATE TABLE IF NOT EXISTS angles (
    cluster_key TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    model TEXT NOT NULL,
    body TEXT NOT NULL
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def start_run(con, source: str) -> int:
    cur = con.execute("INSERT INTO runs (source, started_at, status) VALUES (?, ?, 'running')", (source, utcnow()))
    con.commit()
    return cur.lastrowid


def finish_run(con, run_id: int, status: str, n_items: int = 0, message: str = "") -> None:
    con.execute("UPDATE runs SET status=?, n_items=?, message=? WHERE id=?", (status, n_items, message[:2000], run_id))
    con.commit()


def insert_items(con, run_id: int, items: list[Item], captured_at: str | None = None) -> int:
    captured_at = captured_at or utcnow()
    con.executemany(
        "INSERT INTO snapshots (run_id, source, key, title, region, category, period, captured_at, rank, value, aux, url, curve, extra)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (run_id, i.source, i.key, i.title, i.region, i.category, i.period, captured_at, i.rank, i.value, i.aux,
             i.url, json.dumps(i.curve) if i.curve is not None else None, json.dumps(i.extra))
            for i in items
        ],
    )
    con.commit()
    return len(items)


def last_ok(con, source: str) -> datetime | None:
    row = con.execute("SELECT MAX(started_at) FROM runs WHERE source=? AND status='ok'", (source,)).fetchone()
    return datetime.fromisoformat(row[0]) if row and row[0] else None


def periods(con, source: str) -> set[str]:
    return {r[0] for r in con.execute("SELECT DISTINCT period FROM snapshots WHERE source=?", (source,))}


def load(con, source: str | None = None, since_period: str | None = None) -> pd.DataFrame:
    q, args = "SELECT * FROM snapshots WHERE 1=1", []
    if source:
        q += " AND source=?"
        args.append(source)
    if since_period:
        q += " AND period>=?"
        args.append(since_period)
    df = pd.read_sql_query(q, con, params=args)
    df["curve"] = df["curve"].map(lambda s: json.loads(s) if s else None)
    df["extra"] = df["extra"].map(lambda s: json.loads(s) if s else {})
    return df


def runs(con, limit: int = 200) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM runs ORDER BY id DESC LIMIT ?", con, params=[limit])
