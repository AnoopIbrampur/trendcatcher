# Trend Catcher data

This branch is written by the `collect` GitHub Actions workflow on `main`. It holds raw
snapshots, one immutable file per capture:

```
snapshots/wikipedia/<region>/<YYYY-MM-DD>.jsonl.gz    daily most-viewed lists ("en" = worldwide)
snapshots/<source>/<YYYY-MM-DD>/<HHMMSS>.jsonl.gz    google_trends, youtube, tiktok captures
runs/<YYYY-MM>.jsonl                                  one line per collection run
```

Import it into a local database with `python -m trendcatcher sync`.
