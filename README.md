# Trend Catcher

[![tests](https://github.com/AnoopIbrampur/trendcatcher/actions/workflows/tests.yml/badge.svg)](https://github.com/AnoopIbrampur/trendcatcher/actions/workflows/tests.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Finds topics that are taking off across TikTok, YouTube, Google search and Wikipedia early enough
for a short-form creator (Reels / TikTok / Shorts) to still post about them, and turns
each one into a brief: what it is, whether it's brand-safe, and three video angles.

```
sources (hourly)            score (per source)               merge                     brief
TikTok Creative Center ─┐   momentum, saturation,            embeddings for recall,    local LLM, grounded
Google Trends RSS ──────┼─> lifecycle stage, 0-100   ──>     token containment for ──> in collected evidence
Wikipedia pageviews ────┤   Wikipedia: learned p(stays hot)  precision                 (Ollama)
YouTube (API key) ───────┘
```

## Locations

Pick a country or a US state in the dashboard (or link straight to `?location=US-NY`), or
use `python -m trendcatcher top --location US-NY --local` in the terminal.

| Level | Google Trends | TikTok | YouTube | Wikipedia |
|---|---|---|---|---|
| Country (US, GB, CA, AU, IN) | ✓ | ✓ (not India: TikTok is banned there) | ✓ | ✓ per-country top list |
| US state (50 + DC) | ✓ | national fallback | national fallback | national fallback |
| City / metro | ✗ (Google's metro geos return nothing) | ✗ | ✗ | ✗ |

**Local** trends are what's specific to a place:
- **Country:** not trending in any other tracked country.
- **US state:** not in the US national list, and trending in no more than 3 other states.

The first version only checked against the national list, and nearly every state item came
out "local", because Google's national list holds just ~10 items. Local trends are listed first.

## Results so far

The useful question for a creator isn't "is this big?" but **"will there still be an
audience if I post tomorrow?"** I tested that on 59 days of Wikipedia history
(walk-forward, model refit each day only on labels it could have seen):

> label: mean views over the next 3 days stay ≥ 2× the article's 7-day baseline

| method (top 20 per day, 41 days) | stays hot | beats "most viewed" |
|---|---|---|
| **Trend Catcher (logistic model)** | **75.6%** | 40 / 41 days |
| breakout ratio alone | 71.0% | 37 / 41 |
| hand-tuned heuristic | 61.6% | 26 / 41 |
| "most viewed" list | 54.4% | — |
| random breakout candidate | 24.7% | — |

The same picks **lose to random** if you ask "will views keep rising?" (18% vs 24%).
Wikipedia spikes peak the day they appear and then decay. So momentum tells you a topic is
hot; it does not tell you the topic is still early. Most of the signal is the breakout ratio
itself, and the model adds about 5 points on top of it. The first label I tried
(keep-rising) was the wrong question, and the backtest is what showed that.

Reproduce: `python -m trendcatcher backtest`

## Data sources: what actually works (Oct 2026)

| Source | Access | Notes |
|---|---|---|
| TikTok Creative Center | public JSON endpoint, no login | Anonymous requests return only the top 3 hashtags per query. Sweeping 15 industry filters × 4 countries gets about 150 hashtags with 7-day popularity curves, views and post counts. |
| Google Trends | public RSS | ~10 items per country; mostly live sports and news |
| Wikipedia | Wikimedia REST API | daily top-1000; backfillable, which is what gives the backtest its labels |
| YouTube | Data API v3 key (free, no approval) | `YOUTUBE_API_KEY` in `.env`; top 50 trending, scored by views per hour since upload |
| Reddit | **approval required** | Unauthenticated `.json` endpoints return 403, and under Reddit's [Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy) API access needs explicit approval via a support ticket. The adapter is written but untested; it skips cleanly without keys. |
| Instagram | none usable | the Graph API only covers accounts you manage. IG is where creators post, not a data source; the idea is to spot trends on TikTok before they reach Reels. |

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m trendcatcher ingest            # fetch every source that's due
.venv/bin/python -m trendcatcher backfill-wiki --days 60
.venv/bin/python -m trendcatcher top               # mixed feed in the terminal
.venv/bin/python -m trendcatcher serve             # web app on http://localhost:8517
.venv/bin/streamlit run trendcatcher/dashboard.py  # older Streamlit dashboard (same features)
.venv/bin/python -m trendcatcher status            # collection health
scripts/schedule.sh install                        # hourly launchd job (uninstall to remove)
.venv/bin/python -m pytest
```

Video briefs and cross-platform merging need [Ollama](https://ollama.com) with
`nomic-embed-text` and `qwen3.5:9b`. Without it, merging falls back to exact lexical
matches and the brief button is disabled.

## The app

`python -m trendcatcher serve` starts a FastAPI server (`trendcatcher/web/server.py`) and a
hand-built single-page front end (`trendcatcher/web/static/index.html`, no build step and no
framework). The design follows Apple's interface guidelines:
- **Filters run in the browser.** They respond on press, with no server round trip.
- **Animations are interruptible springs.** Card expansion, popovers and the tab indicator
  start from their current on-screen value, so tapping a card again mid-animation reverses it smoothly.
- **The chrome is translucent.** The header and popovers blur the content scrolling underneath them.
- **Typography:** tracking changes with font size.
- **Modes:** light and dark, plus `prefers-reduced-motion`, `-transparency` and `-contrast` support.

Every location has a shareable link (`/?location=US-NY`, `/?tab=health`).

## Design notes

- **Scores are percentiles within each source**, so a TikTok score and a Wikipedia score
  mean "how strong is this relative to everything else on that platform right now". The feed
  interleaves sources instead of sorting globally, so one platform can't flood it.
- **Saturation (TikTok):** views per post. Lots of views on few posts means audience demand
  is outrunning creator supply, which is the opening a creator wants.
- **Merging across platforms:** short strings fool embeddings ("jane doe" vs "John Doe" has
  cosine 0.90). A merge needs cosine ≥ 0.86 **and** one item's tokens contained in the
  other's. That joins `#digger` and *Digger (2026 film)* but keeps the Quebec and Spanish
  elections apart. Long video titles get a narrower second rule: a multi-word name inside the
  title ("Jailer 2" inside *JAILER 2 - Official Trailer | …*). That rule is restricted to
  video and post titles, because applying it to Wikipedia attached the TikTok hashtag
  `#cornelluniversity` to an article about allegations at Cornell.
- **LLM briefs are grounded:** the prompt carries the collected evidence (metrics, news
  headlines, Wikipedia summary) and the model must answer "unclear" rather than invent what
  a hashtag means. It does this: `#digger` is tagged *Vehicle & Transportation* by TikTok,
  so it's probably excavator videos rather than the film, and the model flags the conflict
  instead of choosing one.
- **Bots:** Wikipedia articles with >90% desktop traffic are dropped (scrapers), as are
  calendar, year and "Deaths in" pages.

## Limitations / next

- TikTok scoring is still a heuristic. It needs a few weeks of hourly snapshots before
  post-count growth can be used as a feature, and before the same backtest can be run on it.
- The cross-platform "who saw it first" lag (TikTok → Reels) is the original thesis and
  isn't measurable yet. `first_seen` is recorded per source so it can be measured later.
- Google Trends is dominated by live sports, which is low value for creators. The LLM
  `creator_fit` score helps, but it is not used for ranking yet.
- Hashtag ambiguity: TikTok's industry tag should be used as a merge constraint.

## License

MIT, see [LICENSE](LICENSE).
