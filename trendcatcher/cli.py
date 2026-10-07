"""Command line entry point: `python -m trendcatcher <command>`."""
import argparse
import logging
import sys


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="trendcatcher")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("ingest", help="fetch every source that is due")
    s.add_argument("--only", nargs="*", help="source names to run")
    s.add_argument("--force", action="store_true", help="ignore per-source intervals")

    s = sub.add_parser("backfill-wiki", help="load past Wikipedia daily tops")
    s.add_argument("--days", type=int, default=60)
    s.add_argument("--regions", nargs="*", help="'en' (worldwide) and/or country codes; default all")

    s = sub.add_parser("top", help="print the current top trends")
    s.add_argument("-n", type=int, default=25)
    s.add_argument("--no-cluster", action="store_true", help="skip embedding-based cross-platform merge")
    s.add_argument("--by-score", action="store_true", help="global score sort instead of the mixed feed")
    s.add_argument("--location", help="country code (US, GB, IN...) or US state (US-NY); default global")
    s.add_argument("--local", action="store_true", help="only trends local to --location")

    s = sub.add_parser("backtest", help="evaluate the Wikipedia scorer on stored history")
    s.add_argument("--k", type=int, default=20)
    s.add_argument("--horizon", type=int, default=3)

    s = sub.add_parser("status", help="collection health")

    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if a.cmd == "ingest":
        from .ingest import ingest
        for name, (status, n) in ingest(a.only, a.force).items():
            print(f"{name:14s} {status:8s} {n}")
    elif a.cmd == "backfill-wiki":
        from .ingest import backfill_wikipedia
        print(f"stored {backfill_wikipedia(a.days, regions=a.regions)} wikipedia rows")
    elif a.cmd == "top":
        from .pipeline import build, mixed_feed
        from .config import location_name
        clusters, _ = build(cluster=not a.no_cluster, location=a.location)
        clusters = clusters if a.by_score else mixed_feed(clusters)
        if a.local:
            clusters = clusters[clusters["local"]]
        print(f"Trends for {location_name(a.location)}")
        cols = ["score", "stage", "label", "sources", "local", "category"]
        import pandas as pd
        with pd.option_context("display.width", 200, "display.max_colwidth", 60):
            print(clusters[cols].head(a.n).to_string(index=False))
    elif a.cmd == "backtest":
        from .evaluate import backtest_wikipedia
        res = backtest_wikipedia(k=a.k, horizon=a.horizon)
        print(f"walk-forward over {res['summary'].attrs.get('n_days', 0)} days")
        print(res["summary"].to_string())
    elif a.cmd == "status":
        from . import db
        con = db.connect()
        print(db.runs(con, 30)[["source", "started_at", "status", "n_items", "message"]]
              .assign(message=lambda d: d.message.fillna("").str.slice(0, 60)).to_string(index=False))
        print()
        print(con.execute("SELECT source, COUNT(*), COUNT(DISTINCT period), MIN(period), MAX(period) FROM snapshots GROUP BY source").fetchall())
    return 0


if __name__ == "__main__":
    sys.exit(main())
