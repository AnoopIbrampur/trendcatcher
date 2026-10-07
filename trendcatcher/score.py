"""Per-source scorers.

Each scorer turns raw snapshots into one row per trend candidate with:
  rise   - source-specific momentum (log scale, higher = growing faster)
  stage  - emerging | peaking | fading | unknown
  score  - 0..100, percentile-based within the source so sources are comparable,
           then discounted by stage (a fading trend is not worth filming)
"""
import re
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import wordninja

STAGE_WEIGHT = {"emerging": 1.0, "peaking": 0.75, "unknown": 0.5, "fading": 0.35}
OUT_COLS = ["source", "key", "title", "text", "category", "regions", "value", "aux", "rise", "stage",
            "score", "url", "first_seen", "curve", "detail"]


def pct(s: pd.Series) -> pd.Series:
    """Percentile rank in [0, 1]; constant or single-row series map to 0.5."""
    if len(s) <= 1 or s.nunique(dropna=True) <= 1:
        return pd.Series(0.5, index=s.index)
    return s.rank(pct=True, method="average").fillna(0.0)


def finalize(df: pd.DataFrame, raw: pd.Series, stage_weighted: bool = True) -> pd.DataFrame:
    df = df.copy()
    weight = df["stage"].map(STAGE_WEIGHT) if stage_weighted else 1.0
    df["score"] = (100 * raw * weight).round(1)
    for c in OUT_COLS:
        if c not in df:
            df[c] = None
    return df[OUT_COLS].sort_values("score", ascending=False).reset_index(drop=True)


def hashtag_text(tag: str) -> str:
    """'breastcancerawarenessmonth' -> 'breast cancer awareness month' (ASCII tags only)."""
    if re.fullmatch(r"[a-z0-9]+", tag.lower()):
        return " ".join(wordninja.split(tag.lower()))
    return tag


def first_seen(snaps: pd.DataFrame) -> pd.Series:
    return snaps.groupby("key")["captured_at"].min()


# ---------------------------------------------------------------- TikTok

def tiktok_stage(curve: list[float] | None) -> str:
    if not curve or max(curve) <= 0:
        return "unknown"
    c = np.asarray(curve, dtype=float)
    peak_age = int(np.argmax(c[::-1]))  # days since the most recent occurrence of the peak
    if peak_age == 0 and (len(c) < 2 or c[-1] > c[-2]):
        return "emerging"
    if peak_age <= 2 and c[-1] >= 0.6 * c.max():
        return "peaking"
    return "fading"


def tiktok_rise(curve: list[float] | None) -> float:
    if not curve or len(curve) < 4:
        return np.nan
    c = np.asarray(curve, dtype=float)
    return float(np.log((c[-2:].mean() + 5) / (c[:3].mean() + 5)))


def score_tiktok(snaps: pd.DataFrame) -> pd.DataFrame:
    if snaps.empty:
        return pd.DataFrame(columns=OUT_COLS)
    seen = first_seen(snaps)
    latest = snaps[snaps["captured_at"] == snaps["captured_at"].max()]
    rows = []
    for key, g in latest.groupby("key"):
        top = g.loc[g["value"].idxmax()]
        cats = sorted(set(g["category"]) - {"All"}) or ["All"]
        # creator-supply growth across our own snapshots (available once history accumulates)
        hist = snaps[(snaps["key"] == key) & (snaps["region"] == top["region"]) & (snaps["category"] == top["category"])]
        post_growth = np.nan
        if hist["period"].nunique() > 1:
            h = hist.sort_values("captured_at")
            days = max((date.fromisoformat(h["period"].iloc[-1]) - date.fromisoformat(h["period"].iloc[0])).days, 1)
            post_growth = np.log((h["aux"].iloc[-1] + 1) / (h["aux"].iloc[0] + 1)) / days
        rows.append({
            "source": "tiktok", "key": key, "title": f"#{top['title']}", "text": hashtag_text(top["title"]),
            "category": ", ".join(cats), "regions": ", ".join(sorted(set(g["region"]))),
            "value": top["value"], "aux": top["aux"], "curve": top["curve"], "url": top["url"],
            "rise": tiktok_rise(top["curve"]), "stage": tiktok_stage(top["curve"]),
            "first_seen": seen.get(key),
            "detail": {"views": top["value"], "posts": top["aux"],
                       "views_per_post": top["value"] / max(top["aux"] or 0, 1), "post_growth_per_day": post_growth},
        })
    df = pd.DataFrame(rows)
    vpp = df["detail"].map(lambda d: np.log1p(d["views_per_post"]))
    # rising curve matters most; high views-per-post = audience demand outrunning creator supply
    raw = 0.6 * pct(df["rise"]) + 0.4 * pct(vpp)
    return finalize(df, raw)


# ------------------------------------------------------------- Wikipedia
#
# Backtesting showed that on Wikipedia every momentum feature predicts *decline*:
# news spikes peak the day they appear. The creator-relevant question is not "will it
# keep rising" but "will it stay hot long enough to post about", so the label is:
#
#   hot = mean views over the next HORIZON days >= HOT_MULT x the 7-day baseline
#
# A logistic regression on simple features, refit on all labelled history each run,
# predicts that. With too little history the scorer falls back to a heuristic.

WIKI_FEATURES = ["ratio", "d1", "d2", "lv", "days_in_top"]
HORIZON = 3
HOT_MULT = 2.0
MIN_TRAIN_ROWS = 2000


def wiki_panel(snaps: pd.DataFrame, horizon: int = HORIZON, lookback: int = 7) -> pd.DataFrame:
    """One row per (day, article in that day's top list) with features and, where the
    future is observed, the `hot` label. Features only use data up to that day.

    An article missing from a day's top-1000 had fewer views than that day's cutoff:
    for features we fill with the cutoff (an upper bound, so ratios are conservative),
    for future labels with half the cutoff.
    """
    from .sources.wikipedia import keep

    snaps = snaps[snaps["key"].map(keep)]
    if snaps.empty:
        return pd.DataFrame()
    cut = snaps.groupby("period")["extra"].first().map(lambda e: e.get("list_cutoff", np.nan))
    raw = snaps.pivot_table(index="key", columns="period", values="value", aggfunc="max")
    days = sorted(raw.columns)
    filled = raw.copy()
    for d in days:
        filled[d] = filled[d].fillna(cut[d])
    present = raw.notna()
    meta = snaps.sort_values("captured_at").drop_duplicates(["key", "period"], keep="last").set_index(["period", "key"])
    frames = []
    for i, d in enumerate(days):
        keys = present.index[present[d]]
        v = filled.loc[keys, d]
        f = pd.DataFrame({"period": d, "key": keys, "value": v.values, "lv": np.log(v.values)})
        if i >= 3:
            prior = filled.loc[keys, days[max(0, i - lookback):i]]
            f["baseline"] = prior.median(axis=1).values
            f["ratio"] = np.log(v.values / f["baseline"].values)
            f["d1"] = np.log(v.values / filled.loc[keys, days[i - 1]].values)
            f["d2"] = np.log(filled.loc[keys, days[i - 1]].values / filled.loc[keys, days[i - 2]].values)
            f["days_in_top"] = present.loc[keys, days[max(0, i - lookback):i]].sum(axis=1).values
        else:
            f[["baseline", "ratio", "d1", "d2", "days_in_top"]] = np.nan
        fut_days = days[i + 1:i + 1 + horizon]
        if len(fut_days) == horizon and i >= 3:
            fut = raw.loc[keys, fut_days].copy()
            for fd in fut_days:
                fut[fd] = fut[fd].fillna(cut[fd] * 0.5)
            f["fut_mean"] = fut.mean(axis=1).values
            f["hot"] = (f["fut_mean"] >= HOT_MULT * f["baseline"]).astype(float)
            f["rising"] = (raw.loc[keys, fut_days].max(axis=1).fillna(0).values > v.values).astype(float)
        else:
            f["fut_mean"] = f["hot"] = f["rising"] = np.nan
        m = meta.loc[d].reindex(keys)
        f["title"] = m["title"].values
        f["aux"] = m["aux"].values
        f["url"] = m["url"].values
        frames.append(f)
    return pd.concat(frames, ignore_index=True)


def fit_wiki_model(panel: pd.DataFrame, asof: str, horizon: int = HORIZON, min_ratio: float = 1.5):
    """Logistic regression on rows whose label was fully observed by `asof` (no leakage)."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    last_labelled = (date.fromisoformat(asof) - timedelta(days=horizon)).isoformat()
    train = panel[(panel["period"] < last_labelled) & panel["hot"].notna() & (panel["ratio"] >= np.log(min_ratio))]
    train = train.dropna(subset=WIKI_FEATURES)
    if len(train) < MIN_TRAIN_ROWS or train["hot"].nunique() < 2:
        return None
    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))
    model.fit(train[WIKI_FEATURES], train["hot"].astype(int))
    return model


def wiki_stage(d1: float) -> str:
    if pd.isna(d1):
        return "unknown"
    if d1 > 0.1:
        return "emerging"
    if d1 > -0.25:
        return "peaking"
    return "fading"


def score_wikipedia(snaps: pd.DataFrame, asof: str | None = None, min_ratio: float = 1.5,
                    panel: pd.DataFrame | None = None, use_model: bool = True) -> pd.DataFrame:
    if snaps.empty and panel is None:
        return pd.DataFrame(columns=OUT_COLS)
    panel = panel if panel is not None else wiki_panel(snaps)
    asof = asof or panel["period"].max()
    f = panel[(panel["period"] == asof) & ~(panel["aux"] > 0.9)].copy()  # ~all-desktop traffic = bots
    if f["ratio"].notna().any():
        f = f[f["ratio"] >= np.log(min_ratio)]  # evergreen pages are not trends
    if f.empty:
        return pd.DataFrame(columns=OUT_COLS)
    model = fit_wiki_model(panel, asof) if use_model else None
    if model is not None and f[WIKI_FEATURES].notna().all(axis=None):
        f["p_hot"] = model.predict_proba(f[WIKI_FEATURES])[:, 1]
        raw = pct(f["p_hot"])
    else:
        f["p_hot"] = np.nan
        raw = 0.8 * pct(f["ratio"]) + 0.2 * pct(f["lv"])
    f["rise"] = f["ratio"]
    f["stage"] = f["d1"].map(wiki_stage)
    f["source"] = "wikipedia"
    f["text"] = f["title"]
    f["regions"] = "en"
    f["category"] = ""
    f["first_seen"] = f["key"].map(panel.groupby("key")["period"].min())
    f["detail"] = [{"views": v, "baseline": b, "ratio": float(np.exp(r)) if pd.notna(r) else None,
                    "p_hot": None if pd.isna(p) else float(p), "desktop_share": a}
                   for v, b, r, p, a in zip(f["value"], f["baseline"], f["ratio"], f["p_hot"], f["aux"])]
    # p_hot already prices in the lifecycle, so don't discount by stage a second time
    return finalize(f, raw, stage_weighted=model is None)


# ---------------------------------------------------------- Google Trends

def score_google_trends(snaps: pd.DataFrame, now: datetime | None = None, max_age_h: float = 72) -> pd.DataFrame:
    if snaps.empty:
        return pd.DataFrame(columns=OUT_COLS)
    now = now or datetime.now(timezone.utc)
    seen = first_seen(snaps)
    rows = []
    for key, g in snaps.groupby("key"):
        g = g.sort_values("captured_at")
        last = g[g["captured_at"] == g["captured_at"].iloc[-1]]
        pub = last["extra"].iloc[0].get("pub_date")
        age_h = (now - datetime.fromisoformat(pub)).total_seconds() / 3600 if pub else np.nan
        if pd.notna(age_h) and age_h > max_age_h:
            continue
        first_val = g[g["captured_at"] == g["captured_at"].iloc[0]]["value"].max()
        val = last["value"].max()
        growth = np.log((val + 1) / (first_val + 1))
        stage = "emerging" if (age_h < 12 or growth > 0) else ("peaking" if age_h < 36 else "fading")
        news = last["extra"].iloc[0].get("news", [])
        rows.append({
            "source": "google_trends", "key": key, "title": last["title"].iloc[0], "text": last["title"].iloc[0],
            "category": "", "regions": ", ".join(sorted(set(g["region"]))), "value": val, "aux": None,
            "url": last["url"].iloc[0], "rise": growth, "stage": stage, "first_seen": seen.get(key),
            "detail": {"traffic": val, "age_hours": age_h, "news": news}, "n_regions": g["region"].nunique(),
        })
    if not rows:
        return pd.DataFrame(columns=OUT_COLS)
    df = pd.DataFrame(rows)
    age = df["detail"].map(lambda d: d["age_hours"])
    raw = (0.5 * pct(np.log1p(df["value"])) + 0.3 * pct(-age) + 0.2 * pct(df["rise"])
           + 0.05 * (df["n_regions"] - 1)).clip(0, 1)
    return finalize(df, raw)


# --------------------------------------------------- YouTube and Reddit

def _velocity_score(snaps, source, created_field, emerging_h, peaking_h, now=None, text_fn=None):
    if snaps.empty:
        return pd.DataFrame(columns=OUT_COLS)
    now = now or datetime.now(timezone.utc)
    latest = snaps[snaps["captured_at"] == snaps["captured_at"].max()].drop_duplicates("key").copy()
    seen = first_seen(snaps)
    created = latest["extra"].map(lambda e: datetime.fromisoformat(e[created_field]))
    hours = created.map(lambda c: max((now - c).total_seconds() / 3600, 1.0))
    latest["rise"] = np.log1p(latest["value"] / hours)
    latest["stage"] = hours.map(lambda h: "emerging" if h < emerging_h else ("peaking" if h < peaking_h else "fading"))
    latest["text"] = latest.apply(text_fn, axis=1) if text_fn else latest["title"]
    latest["regions"] = latest["region"]
    latest["first_seen"] = latest["key"].map(seen)
    latest["detail"] = [dict(e, per_hour=float(np.expm1(r))) for e, r in zip(latest["extra"], latest["rise"])]
    return finalize(latest, pct(latest["rise"]))


def score_youtube(snaps, now=None):
    from .sources.youtube import CATEGORIES
    snaps = snaps.assign(category=snaps["category"].map(lambda c: CATEGORIES.get(c, c)))
    return _velocity_score(snaps, "youtube", "published_at", 24, 72, now,
                           lambda r: r["title"] + " " + " ".join(r["extra"].get("tags", [])[:5]))


def score_reddit(snaps, now=None):
    return _velocity_score(snaps, "reddit", "created_utc", 6, 24, now)


SCORERS = {
    "tiktok": score_tiktok,
    "wikipedia": score_wikipedia,
    "google_trends": score_google_trends,
    "youtube": score_youtube,
    "reddit": score_reddit,
}
