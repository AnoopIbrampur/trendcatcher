"""Streamlit dashboard: `streamlit run trendcatcher/dashboard.py`."""
import json

import pandas as pd
import streamlit as st

from trendcatcher import db, llm
from trendcatcher.angles import brief
from trendcatcher.config import COUNTRIES, US_STATES, location_name
from trendcatcher.pipeline import build, mixed_feed

st.set_page_config(page_title="Trend Catcher", layout="wide")


@st.cache_data(ttl=600, show_spinner="Scoring trends...")
def load(location):
    con = db.connect()
    clusters, items = build(con, location=location)
    return mixed_feed(clusters), items


LOCATIONS = [None, *COUNTRIES, *(f"US-{s}" for s in sorted(US_STATES, key=US_STATES.get))]


def fmt_num(x) -> str:
    if x is None or pd.isna(x):
        return ""
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= div:
            return f"{x / div:.1f}{suf}"
    return f"{x:.0f}"


st.title("Trend Catcher")
# ?location=US-NY makes a shareable link per place
requested = st.query_params.get("location")
location = st.selectbox("Location", LOCATIONS, format_func=location_name,
                        index=LOCATIONS.index(requested) if requested in LOCATIONS else 0,
                        help="Country data comes from every source. US states only have Google search data; "
                             "the other sources fall back to US national.")
if location:
    st.query_params["location"] = location
elif "location" in st.query_params:
    del st.query_params["location"]
clusters, items = load(location)
if "local" not in clusters:  # never crash on results built by an older version
    clusters = clusters.assign(local=False)
st.caption("Rising topics across TikTok, YouTube, Google search and Wikipedia, ranked by how likely they are to stay hot long enough to post about.")

tab_feed, tab_health = st.tabs(["Feed", "Collection health"])

with tab_feed:
    c1, c2, c3, c4, c5 = st.columns([3, 2, 3, 2, 1.4])
    stages = c1.multiselect("Stage", ["emerging", "peaking", "fading", "unknown"], default=["emerging", "peaking"],
                            key="f_stage")
    sources = c2.multiselect("Source", sorted({s for ss in clusters["sources"] for s in ss.split(", ")}), key="f_source")
    cats = sorted({c for cs in clusters["category"] for c in cs.split(", ") if c})
    cat = c3.multiselect("Category", cats, key="f_cat")
    min_score = c4.slider("Min score", 0, 100, 40, key="f_score")
    only_local = c5.toggle("Local only", disabled=location is None, key="f_local",
                           help="Trending here but not nationally / in other countries")

    view = clusters[clusters["stage"].isin(stages) & (clusters["score"] >= min_score)]
    if sources:
        view = view[view["sources"].map(lambda s: any(x in s for x in sources))]
    if cat:
        view = view[view["category"].map(lambda s: any(x in s for x in cat))]
    if only_local:
        view = view[view["local"]]
    st.write(f"**{len(view)}** trends in {location_name(location)}" + (f" · {int(view['local'].sum())} local" if location else ""))

    ollama_up = llm.available()
    for _, row in view.head(60).iterrows():
        badge = " · ".join(row["sources"].split(", "))
        pin = "  ·  :orange[local]" if row.get("local") else ""
        with st.expander(f"**{row['label']}**  —  {row['score']:.0f}  ·  {row['stage']}  ·  {badge}{pin}",
                         key=f"exp-{location}-{row['cluster_key']}"):
            members = items[items["cluster"] == row["cluster"]]
            left, right = st.columns([3, 2])
            with left:
                for _, it in members.iterrows():
                    d = it["detail"] or {}
                    if it["source"] == "tiktok":
                        st.markdown(f"[{it['title']}]({it['url']}) — TikTok · {it['category']} · {it['regions']} · "
                                    f"{fmt_num(d.get('views'))} views / {fmt_num(d.get('posts'))} posts (7d)")
                        if it["curve"]:
                            st.line_chart(pd.Series(it["curve"], name="popularity"), height=120)
                    elif it["source"] == "wikipedia":
                        extra = f" · p(stays hot)={d['p_hot']:.2f}" if d.get("p_hot") is not None else ""
                        st.markdown(f"[{it['title']}]({it['url']}) — Wikipedia · {fmt_num(d.get('views'))} views, "
                                    f"{(d.get('ratio') or 0):.1f}x baseline{extra}")
                    elif it["source"] == "google_trends":
                        st.markdown(f"[{it['title']}]({it['url']}) — Google · {it['regions']} · {fmt_num(d.get('traffic'))}+ searches")
                        for n in (d.get("news") or [])[:3]:
                            st.markdown(f"  - [{n.get('title')}]({n.get('url')}) ({n.get('source')})")
                    elif it["source"] == "youtube":
                        st.markdown(f"[{it['title']}]({it['url']}) — YouTube · {d.get('channel')} · {it['category']} · "
                                    f"{fmt_num(it['value'])} views, {fmt_num(d.get('per_hour'))}/hour")
                    else:
                        st.markdown(f"[{it['title']}]({it['url']}) — {it['source']}")
            with right:
                con = db.connect()
                bkey = row["cluster_key"] + (f"@{location}" if location else "")
                cached = con.execute("SELECT body FROM angles WHERE cluster_key=?", (bkey,)).fetchone()
                b = json.loads(cached[0]) if cached else None
                if b is None and st.button("Generate video angles", key=f"gen-{location}-{row['cluster_key']}", disabled=not ollama_up,
                                           help=None if ollama_up else "Start Ollama to enable"):
                    with st.spinner("Asking the local model..."):
                        b = brief(row, items, con, location=location)
                if b:
                    st.markdown(f"**What it is:** {b.get('what_it_is')}")
                    st.markdown(f"**Creator fit:** {b.get('creator_fit')}/10 · **Brand safety:** {b.get('brand_safety')}"
                                f" — {b.get('safety_reason', '')}")
                    if b.get("best_niches"):
                        st.markdown("**Niches:** " + ", ".join(b["best_niches"]))
                    for a in b.get("angles", []):
                        st.markdown(f"- *{a.get('hook')}* — {a.get('format')} · {a.get('niche')}")

with tab_health:
    con = db.connect()
    st.subheader("Recent runs")
    st.dataframe(db.runs(con, 100)[["source", "started_at", "status", "n_items", "message"]], hide_index=True,
                 width="stretch")
    st.subheader("History per source")
    st.dataframe(pd.read_sql_query(
        "SELECT source, COUNT(*) AS rows, COUNT(DISTINCT period) AS days, MIN(period) AS first, MAX(period) AS last,"
        " MAX(captured_at) AS last_capture FROM snapshots GROUP BY source", con), hide_index=True)
