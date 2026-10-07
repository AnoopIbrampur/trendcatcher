"""Cloud collection: run ingestion on an always-on machine and keep results as plain files.

A laptop that sleeps misses most hourly runs, so collection runs in GitHub Actions and
commits its output to a `data` branch. Your machine runs `sync` to import new files.

Store layout (the `data` branch):
    snapshots/wikipedia/<region>/<period>.jsonl.gz     one file per daily top list
    snapshots/<source>/<YYYY-MM-DD>/<HHMMSS>.jsonl.gz  one file per capture
    runs/<YYYY-MM>.jsonl                               append-only run log

Files are immutable once written, so syncing is "import every file not seen before".
"""
import gzip
import json
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .config import DATA_DIR, ROOT
from .ingest import run_source
from .sources import ALL_SOURCES, Wikipedia

DEFAULT_STORE = DATA_DIR / "cloud-store"
BRANCH = "data"
SNAP_COLS = ["source", "key", "title", "region", "category", "period", "captured_at", "rank", "value", "aux", "url",
             "curve", "extra"]

# UTC hours each source runs at when the workflow fires hourly. Wikipedia runs several
# times a day because the previous day's list is published at an unpredictable hour;
# runs that find nothing new cost a few requests and write nothing.
SCHEDULE = {
    "google_trends": lambda h: True,
    "youtube": lambda h: h % 3 == 0,
    "tiktok": lambda h: h % 6 == 0,
    "wikipedia": lambda h: h in (3, 9, 15, 21),
    "reddit": lambda h: True,  # skips itself without API keys
}


def due_sources(hour: int) -> list[str]:
    return [name for name, due in SCHEDULE.items() if due(hour)]


# ---------------------------------------------------------------- writing


def _ensure_imported_table(con) -> None:
    con.execute("CREATE TABLE IF NOT EXISTS imported (path TEXT PRIMARY KEY, at TEXT NOT NULL)")


def _rel(path: Path, store: Path) -> str:
    return path.relative_to(store).as_posix()


def snapshot_path(store: Path, row: dict) -> Path:
    if row["source"] == "wikipedia":
        return store / "snapshots" / "wikipedia" / row["region"] / f"{row['period']}.jsonl.gz"
    ts = datetime.fromisoformat(row["captured_at"]).astimezone(timezone.utc)
    return store / "snapshots" / row["source"] / ts.strftime("%Y-%m-%d") / f"{ts.strftime('%H%M%S')}.jsonl.gz"


def _write_gz(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows).encode()
    with open(path, "wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:  # mtime=0: stable bytes
        gz.write(body)


def export_snapshots(con, store: Path, where: str = "", params: tuple = ()) -> list[Path]:
    """Write snapshot rows to the store. Existing files are never overwritten."""
    cur = con.execute(f"SELECT {', '.join(SNAP_COLS)} FROM snapshots {where} ORDER BY id", params)
    groups: dict[Path, list[dict]] = {}
    for values in cur:
        row = dict(zip(SNAP_COLS, values))
        groups.setdefault(snapshot_path(store, row), []).append(row)
    written = []
    for path, rows in groups.items():
        if path.exists():
            continue
        _write_gz(path, rows)
        written.append(path)
    return written


def append_runs(con, store: Path) -> int:
    rows = con.execute("SELECT source, started_at, status, n_items, message FROM runs WHERE status != 'running'").fetchall()
    for source, started_at, status, n_items, message in rows:
        path = store / "runs" / f"{started_at[:7]}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"id": uuid.uuid4().hex, "source": source, "started_at": started_at, "status": status,
               "n_items": n_items, "message": (message or "").split("\n")[0][:500]}
        with open(path, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(rows)


def store_wiki_periods(store: Path) -> dict[str, set[str]]:
    base = store / "snapshots" / "wikipedia"
    if not base.exists():
        return {}
    return {d.name: {f.name.split(".")[0] for f in d.glob("*.jsonl.gz")} for d in base.iterdir() if d.is_dir()}


def collect(store: Path, sources: list[str]) -> dict[str, tuple[str, int]]:
    """Run the given sources into a scratch database, then write the results to the store."""
    store.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        con = db.connect(Path(tmp) / "scratch.db")
        results = {}
        for cls in ALL_SOURCES:
            if cls.name not in sources:
                continue
            have = store_wiki_periods(store) if cls is Wikipedia else None
            results[cls.name] = run_source(con, cls(), have=have)
        export_snapshots(con, store)
        append_runs(con, store)
        con.close()
    return results


def seed(con, store: Path) -> int:
    """Export everything already in the local database, and mark it imported locally."""
    _ensure_imported_table(con)
    written = export_snapshots(con, store)
    now = db.utcnow()
    con.executemany("INSERT OR IGNORE INTO imported (path, at) VALUES (?, ?)", [(_rel(p, store), now) for p in written])
    con.commit()
    return len(written)


# ---------------------------------------------------------------- reading


def _read_gz(path: Path) -> list[dict]:
    with gzip.open(path, "rt") as f:
        return [json.loads(line) for line in f if line.strip()]


def import_store(con, store: Path) -> dict[str, int]:
    """Import every snapshot file and run-log line not imported before. Idempotent."""
    _ensure_imported_table(con)
    seen = {r[0] for r in con.execute("SELECT path FROM imported")}
    now = db.utcnow()
    files = rows = runs = 0
    for path in sorted((store / "snapshots").rglob("*.jsonl.gz")) if (store / "snapshots").exists() else []:
        rel = _rel(path, store)
        if rel in seen:
            continue
        recs = _read_gz(path)
        con.executemany(
            f"INSERT INTO snapshots (run_id, {', '.join(SNAP_COLS)}) VALUES (0, {', '.join('?' * len(SNAP_COLS))})",
            [tuple(r.get(c) for c in SNAP_COLS) for r in recs],
        )
        con.execute("INSERT INTO imported (path, at) VALUES (?, ?)", (rel, now))
        files += 1
        rows += len(recs)
    for log in sorted((store / "runs").glob("*.jsonl")) if (store / "runs").exists() else []:
        for line in log.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            key = f"run:{rec['id']}"
            if key in seen:
                continue
            con.execute("INSERT INTO runs (source, started_at, status, n_items, message) VALUES (?, ?, ?, ?, ?)",
                        (rec["source"], rec["started_at"], rec["status"], rec["n_items"],
                         "[cloud] " + (rec.get("message") or "")))
            con.execute("INSERT INTO imported (path, at) VALUES (?, ?)", (key, now))
            runs += 1
    con.commit()
    return {"files": files, "rows": rows, "runs": runs}


def _git(*args, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def fetch_store(store: Path = DEFAULT_STORE) -> Path:
    """Clone or update a local copy of the `data` branch."""
    if (store / ".git").exists():
        # shallow: only the current files matter, never the branch history
        _git("fetch", "-q", "--depth", "1", "origin", BRANCH, cwd=store)
        _git("reset", "-q", "--hard", "FETCH_HEAD", cwd=store)
    else:
        origin = _git("remote", "get-url", "origin", cwd=ROOT)
        store.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "-q", "--depth", "1", "--branch", BRANCH, "--single-branch", origin, str(store))
    return store


def sync(con=None, store: Path = DEFAULT_STORE, pull: bool = True) -> dict[str, int]:
    con = con or db.connect()
    if pull:
        fetch_store(store)
    return import_store(con, store)

