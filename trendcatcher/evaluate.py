"""Walk-forward backtest of the Wikipedia scorer.

For every past day d with enough history and `horizon` days of observed future:
  - trendcatcher: top-k by the scorer as of d (logistic model refit on labels observed by d)
  - heuristic:    the hand-weighted fallback (breakout ratio + volume, stage-discounted)
  - baselines:    top-k by raw views ("most viewed" list), top-k by breakout ratio alone,
                  and the base rate among breakout candidates (= random picks)

Primary label `hot`: the article's mean views over the next `horizon` days stay >= 2x its
7-day baseline, i.e. there is still an audience if a creator posts tomorrow.
Secondary label `rising`: views on some later day exceed day d (shown for honesty: on
Wikipedia, momentum mean-reverts and every breakout method loses to random here).
"""
import numpy as np
import pandas as pd

from . import db
from .config import WIKI_GLOBAL_REGION
from .score import HORIZON, score_wikipedia, wiki_panel


def backtest_wikipedia(k: int = 20, horizon: int = HORIZON, con=None, snaps: pd.DataFrame | None = None,
                       min_ratio: float = 1.5, region: str = WIKI_GLOBAL_REGION) -> dict:
    """`region`: "en" for the worldwide list (default) or a country code. Each region's top
    list is its own history; mixing them would blend different audiences into one series."""
    snaps = snaps if snaps is not None else db.load(con or db.connect(), "wikipedia")
    if "region" in snaps:
        snaps = snaps[snaps["region"] == region]
    panel = wiki_panel(snaps, horizon=horizon)
    days = sorted(panel["period"].unique())
    rows = []
    for d in days:
        day = panel[(panel["period"] == d) & ~(panel["aux"] > 0.9)]
        if day["hot"].isna().all() or day["ratio"].isna().all():
            continue
        cands = day[day["ratio"] >= np.log(min_ratio)]
        if len(cands) < k:
            continue
        hist = panel[panel["period"] <= d].copy()
        hist.loc[hist["period"] == d, ["hot", "rising", "fut_mean"]] = np.nan  # scorer must not see the answer
        model_keys = score_wikipedia(snaps, asof=d, panel=hist)
        heur_keys = score_wikipedia(snaps, asof=d, panel=hist, use_model=False)
        picks = {
            "trendcatcher": model_keys.head(k)["key"],
            "heuristic": heur_keys.head(k)["key"],
            "breakout_ratio_only": cands.nlargest(k, "ratio")["key"],
            "most_viewed": day.nlargest(k, "value")["key"],
        }
        lab = day.set_index("key")
        rec = {"day": d, "used_model": model_keys["detail"].map(lambda x: x.get("p_hot") is not None).any()}
        for label in ("hot", "rising"):
            rec[f"base_rate|{label}"] = cands[label].mean()
            for name, keys in picks.items():
                rec[f"{name}|{label}"] = lab[label].reindex(keys).mean()
        rows.append(rec)
    per_day = pd.DataFrame(rows)
    if per_day.empty:
        return {"per_day": per_day, "summary": pd.DataFrame()}
    scored = per_day[per_day["used_model"]]
    methods = ["trendcatcher", "heuristic", "breakout_ratio_only", "most_viewed", "base_rate"]
    summary = pd.DataFrame({
        f"P@{k} stays hot {horizon}d": [scored[f"{m}|hot"].mean() for m in methods],
        "std": [scored[f"{m}|hot"].std() for m in methods],
        "days beating most_viewed": [(scored[f"{m}|hot"] > scored["most_viewed|hot"]).mean()
                                     if m != "most_viewed" else np.nan for m in methods],
        f"P@{k} still rising": [scored[f"{m}|rising"].mean() for m in methods],
    }, index=methods).round(3)
    summary.attrs["n_days"] = len(scored)
    return {"per_day": per_day, "summary": summary}
