from datetime import date, datetime, timezone

from trendcatcher.sources import google_trends, reddit, tiktok, wikipedia, youtube

TIKTOK_PAYLOAD = {
    "BaseResp": {"StatusCode": 0},
    "items": [{
        "hashtagID": "1", "hashtagName": "HalloweenCostume", "industryIDs": ["22000000000"],
        "popularityCurve": [{"timestamp": "1790726400", "value": 10}, {"timestamp": "1790640000", "value": 0},
                            {"timestamp": "1790812800", "value": 100}],
        "publishCnt": "1138", "rankIndex": "1", "vv": "39645620",
    }],
}

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
<item><title>megan fox</title><ht:approx_traffic>2,000+</ht:approx_traffic>
<pubDate>Tue, 6 Oct 2026 16:10:00 -0700</pubDate>
<ht:news_item><ht:news_item_title>Headline</ht:news_item_title><ht:news_item_url>https://x.test/a</ht:news_item_url>
<ht:news_item_source>X</ht:news_item_source></ht:news_item></item>
</channel></rss>"""


def test_tiktok_parse_sorts_curve_and_reads_counts():
    [it] = tiktok.parse(TIKTOK_PAYLOAD, "US", "Apparel & Accessories", 7)
    assert it.key == "halloweencostume"
    assert it.curve == [0.0, 10.0, 100.0]
    assert it.value == 39645620 and it.aux == 1138
    assert it.period == datetime.fromtimestamp(1790812800, tz=timezone.utc).date().isoformat()


def test_tiktok_parse_raises_on_api_error():
    import pytest
    with pytest.raises(RuntimeError):
        tiktok.parse({"BaseResp": {"StatusCode": 40100}}, "US", "All", 7)


def test_google_traffic_parsing():
    assert google_trends.parse_traffic("2,000+") == 2000
    assert google_trends.parse_traffic("10K+") == 10000
    assert google_trends.parse_traffic("1M+") == 1e6
    assert google_trends.parse_traffic(None) == 0


def test_google_rss_parse():
    [it] = google_trends.parse(RSS, "US")
    assert it.title == "megan fox" and it.value == 2000
    assert it.extra["news"][0]["url"] == "https://x.test/a"
    assert it.period == "2026-10-06"


def test_wikipedia_filters_and_desktop_share():
    allv = {"items": [{"articles": [
        {"article": "Main_Page", "views": 9e6, "rank": 1},
        {"article": "Special:Search", "views": 8e5, "rank": 2},
        {"article": "October_5", "views": 5e5, "rank": 3},
        {"article": "Botted_Page", "views": 2e5, "rank": 4},
        {"article": "Digger_(2026_film)", "views": 1e5, "rank": 5},
    ]}]}
    desk = {"items": [{"articles": [{"article": "Botted_Page", "views": 199000, "rank": 1}]}]}
    items = wikipedia.parse(allv, desk, date(2026, 10, 5))
    assert [i.key for i in items] == ["Botted_Page", "Digger_(2026_film)"]
    assert items[0].aux > 0.9 and items[1].aux == 0
    assert items[1].title == "Digger (2026 film)"
    assert items[0].extra["list_cutoff"] == 1e5


def test_wikipedia_missing_days():
    w = wikipedia.Wikipedia()
    days = w.missing_days({"2026-10-04"}, lookback=3, today=date(2026, 10, 6))
    assert [d.isoformat() for d in days] == ["2026-10-03", "2026-10-05"]


def test_youtube_and_reddit_parse():
    yt = youtube.parse({"items": [{"id": "abc", "snippet": {"title": "T", "publishedAt": "2026-10-06T00:00:00Z",
                                                            "channelTitle": "C", "categoryId": "10"},
                                   "statistics": {"viewCount": "100"}}]}, "US")
    assert yt[0].value == 100 and yt[0].aux is None
    rd = reddit.parse({"data": {"children": [{"data": {"id": "x", "title": "post", "score": 5, "num_comments": 2,
                                                       "created_utc": 1790812800, "subreddit": "memes",
                                                       "permalink": "/r/memes/x"}}]}})
    assert rd[0].category == "memes" and rd[0].url.endswith("/r/memes/x")


def test_keyless_sources_skip(monkeypatch):
    import pytest
    from trendcatcher.sources import SourceSkipped
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    with pytest.raises(SourceSkipped):
        youtube.YouTube().fetch()
    with pytest.raises(SourceSkipped):
        reddit.Reddit().fetch()
