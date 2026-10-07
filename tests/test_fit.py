import pandas as pd

from trendcatcher import fit


def test_label_key():
    assert fit.label_key("#HalloweenCostume") == "halloweencostume"
    assert fit.label_key("  Megan   Fox ") == "megan fox"


def test_apply_blends_fit_and_safety():
    c = pd.DataFrame({"label": ["#doggie", "cam jurgens", "unrated"], "score": [80.0, 90.0, 70.0], "n_sources": [1, 1, 1]})
    out = fit.apply(c, {"doggie": (10, "safe"), "cam jurgens": (2, "avoid")}).set_index("label")
    assert out.loc["#doggie", "score"] == 80.0                       # fit 10 keeps the full score
    assert out.loc["cam jurgens", "score"] == round(90 * 0.6 * 0.7, 1)  # fit 2 and avoid
    assert out.loc["unrated", "score"] == round(70 * 0.8, 1)          # neutral prior fit 6
    assert out.loc["cam jurgens", "base_score"] == 90.0
    assert list(out.index) == ["#doggie", "unrated", "cam jurgens"]    # re-ranked


def test_apply_empty():
    out = fit.apply(pd.DataFrame(columns=["label", "score", "n_sources"]), {})
    assert out.empty and {"fit", "safety", "base_score"} <= set(out.columns)


def test_rate_drops_malformed(monkeypatch):
    monkeypatch.setattr(fit.llm, "generate_json", lambda *a, **k: {"ratings": [
        {"id": 1, "creator_fit": 9, "brand_safety": "Safe"},
        {"id": 2, "creator_fit": 14, "brand_safety": "safe"},   # out of range
        {"id": 3, "creator_fit": "x", "brand_safety": "safe"},  # not a number
        {"id": 4, "creator_fit": 3.4, "brand_safety": "maybe"},  # unknown safety
        {"creator_fit": 5, "brand_safety": "safe"},              # no id
    ]})
    assert fit.rate([(1, "a", ""), (2, "b", ""), (3, "c", ""), (4, "d", "")]) == {1: (9, "safe")}
