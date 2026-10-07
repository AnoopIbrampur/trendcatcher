from datetime import date, timedelta

import numpy as np
import pandas as pd

from trendcatcher import cluster, db
from trendcatcher.pipeline import mixed_feed
from trendcatcher.score import hashtag_text, score_wikipedia, tiktok_stage, wiki_panel
from trendcatcher.sources.base import Item


def test_tiktok_stage():
    assert tiktok_stage([0, 10, 30, 60, 100]) == "emerging"
    assert tiktok_stage([0, 50, 100, 90, 80]) == "peaking"
    assert tiktok_stage([100, 80, 50, 30, 20]) == "fading"
    assert tiktok_stage([0, 0, 0]) == "unknown"
    assert tiktok_stage(None) == "unknown"


def test_hashtag_text():
    assert hashtag_text("breastcancerawarenessmonth") == "breast cancer awareness month"
    assert hashtag_text("деньучителя") == "деньучителя"


def test_merge_rule_precision():
    t = cluster.tokens
    # true matches
    assert cluster.should_merge(0.0, t("jane doe"), t("Jane Doe"), cluster.compact("#janedoe"), cluster.compact("Jane Doe"))
    assert cluster.should_merge(0.89, t("digger"), t("Digger (2026 film)"), "digger", "digger2026film")
    # embedding-similar but different things
    assert not cluster.should_merge(0.905, t("jane doe"), t("John Doe"), "janedoe", "johndoe")
    assert not cluster.should_merge(0.891, t("wuthering waves"), t("Wuthering Heights"), "a", "b")
    assert not cluster.should_merge(0.92, t("2026 Quebec general election"), t("2026 Spanish general election"), "a", "b")


def test_phrase_inside_long_title():
    t = cluster.tokens
    title = "JAILER 2 - Official Trailer | Superstar Rajinikanth | Sun Pictures"
    assert cluster.should_merge(0.822, t("Jailer 2"), t(title), "jailer2", "x", long_title=True)
    # single generic words inside a long title are not enough
    assert not cluster.should_merge(0.802, t("october"), t("We fell in love in October (Remix)"), "october", "y", True)
    assert not cluster.should_merge(0.85, t("War (TV series)"), t("Gears of War is worse than woke"), "war", "z", True)
    # the phrase path is only for long-title sources (video/post titles), not Wikipedia articles
    assert not cluster.should_merge(0.85, t("cornell university"), t("2024 Cornell University rape allegations"),
                                    "cornelluniversity", "w", long_title=False)


def test_assign_lexical_fallback():
    items = pd.DataFrame({"text": ["jane doe", "Jane Doe", "red wings"], "score": [90, 80, 70]})
    labels = cluster.assign(items, None)
    assert labels[0] == labels[1] != labels[2]


def _wiki_snaps(n_days=20):
    """Synthetic top lists: 'Steady' flat, 'Story' spikes on day 10 and stays high, 'Blip' spikes one day."""
    rows, start = [], date(2026, 9, 1)
    for i in range(n_days):
        d = (start + timedelta(days=i)).isoformat()
        views = {"Steady": 50_000, "Filler": 10_000}
        if i >= 10:
            views["Story"] = 200_000
        if i == 10:
            views["Blip"] = 300_000
        for k, v in views.items():
            rows.append({"source": "wikipedia", "key": k, "title": k, "period": d, "captured_at": d, "value": v,
                         "aux": 0.3, "url": "", "extra": {"list_cutoff": 10_000}})
    return pd.DataFrame(rows)


def test_wiki_panel_labels_and_no_lookahead_in_features():
    snaps = _wiki_snaps()
    panel = wiki_panel(snaps).set_index(["period", "key"])
    d10 = (date(2026, 9, 1) + timedelta(days=10)).isoformat()
    story, blip = panel.loc[(d10, "Story")], panel.loc[(d10, "Blip")]
    assert story["hot"] == 1 and blip["hot"] == 0
    # features identical whether or not the future exists
    trimmed = wiki_panel(snaps[snaps["period"] <= d10]).set_index(["period", "key"])
    for f in ["ratio", "d1", "d2", "lv", "days_in_top"]:
        assert np.isclose(trimmed.loc[(d10, "Blip"), f], blip[f])
    assert np.isnan(trimmed.loc[(d10, "Blip"), "hot"])


def test_score_wikipedia_drops_evergreen_and_bots():
    snaps = _wiki_snaps()
    d10 = (date(2026, 9, 1) + timedelta(days=10)).isoformat()
    out = score_wikipedia(snaps, asof=d10, use_model=False)
    assert set(out["key"]) == {"Story", "Blip"}
    snaps.loc[snaps["key"] == "Blip", "aux"] = 0.99
    assert set(score_wikipedia(snaps, asof=d10, use_model=False)["key"]) == {"Story"}


def test_mixed_feed_interleaves_sources():
    c = pd.DataFrame({"label": list("abcdef"), "score": [99, 98, 97, 60, 50, 40],
                      "primary": ["wikipedia"] * 3 + ["tiktok"] * 3, "n_sources": [1] * 5 + [2]})
    assert mixed_feed(c)["label"].tolist() == ["f", "a", "d", "b", "e", "c"]


def test_db_roundtrip(tmp_path):
    con = db.connect(tmp_path / "t.db")
    run = db.start_run(con, "tiktok")
    db.insert_items(con, run, [Item("tiktok", "x", "#x", "2026-10-06", 5.0, curve=[1, 2], extra={"a": 1})])
    db.finish_run(con, run, "ok", 1)
    df = db.load(con, "tiktok")
    assert df.loc[0, "curve"] == [1, 2] and df.loc[0, "extra"] == {"a": 1}
    assert db.last_ok(con, "tiktok") is not None
    assert db.periods(con, "tiktok") == {"2026-10-06"}
