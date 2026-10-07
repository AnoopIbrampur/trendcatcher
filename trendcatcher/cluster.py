"""Merge mentions of the same trend across platforms.

Short strings fool embeddings ("jane doe" vs "John Doe" scores 0.90), so a merge needs
both semantic similarity (recall) and real token overlap (precision):

    merge  <=>  compact strings equal
            or  cos >= HARD_COS
            or  (cos >= SOFT_COS and token Jaccard >= MIN_JACCARD)

Clustering is greedy against each cluster's seed, highest score first, so a strong
item is never absorbed into a weaker one and chains (A~B~C) cannot drift.
"""
import json
import re
import unicodedata

import numpy as np
import pandas as pd

from . import llm
from .config import EMBED_MODEL

SOFT_COS = 0.86
HARD_COS = 0.97
MIN_JACCARD = 0.5
PHRASE_COS = 0.80
LONG_TITLE_SOURCES = {"youtube", "reddit"}
STOP = {"vs", "v", "the", "of", "a", "an", "and", "in", "on", "for", "to", "de", "la", "le", "el", "tiktok", "tok"}


def tokens(text: str) -> set[str]:
    text = re.sub(r"\([^)]*\)", " ", text)  # "Digger (2026 film)" -> "Digger"
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode() or text
    return {t for t in re.findall(r"\w+", text.lower()) if t not in STOP}


def compact(text: str) -> str:
    """'Jane Doe' and '#janedoe' -> 'janedoe'."""
    return "".join(re.findall(r"\w+", re.sub(r"\([^)]*\)", "", text).lower()))


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def should_merge(cos: float, ta: set, tb: set, ca: str, cb: str, long_title: bool = False) -> bool:
    if ca and ca == cb:
        return True
    contained = bool(ta and tb) and (ta <= tb or tb <= ta)
    if cos >= HARD_COS or (cos >= SOFT_COS and contained and jaccard(ta, tb) >= MIN_JACCARD):
        return True
    small = ta if len(ta) <= len(tb) else tb
    phrase = len(small) >= 2 and any(not t.isdigit() and len(t) >= 3 for t in small)
    return long_title and contained and phrase and cos >= PHRASE_COS


def cached_embed(con, texts: list[str], model: str = EMBED_MODEL) -> np.ndarray:
    texts = [f"clustering: {t}" for t in texts]
    have = {}
    if con is not None:
        uniq = list(set(texts))
        for i in range(0, len(uniq), 500):
            chunk = uniq[i:i + 500]
            q = f"SELECT text, vec FROM embeddings WHERE model=? AND text IN ({','.join('?' * len(chunk))})"
            have.update({t: np.asarray(json.loads(v), dtype=np.float32) for t, v in con.execute(q, [model, *chunk])})
    missing = sorted(set(texts) - have.keys())
    if missing:
        vecs = llm.embed(missing, model)
        have.update(zip(missing, vecs))
        if con is not None:
            con.executemany("INSERT OR REPLACE INTO embeddings (text, model, vec) VALUES (?, ?, ?)",
                            [(t, model, json.dumps(v.round(6).tolist())) for t, v in zip(missing, vecs)])
            con.commit()
    return np.stack([have[t] for t in texts])


def assign(items: pd.DataFrame, vecs: np.ndarray | None) -> np.ndarray:
    """Greedy seed clustering. `items` must be sorted by score descending."""
    toks = [tokens(t) for t in items["text"]]
    comp = [compact(t) for t in items["text"]]
    long_src = items["source"].isin(LONG_TITLE_SOURCES).tolist() if "source" in items else [False] * len(items)
    seeds: list[int] = []
    labels = np.empty(len(items), dtype=int)
    for i in range(len(items)):
        best, best_cos = -1, -1.0
        for c, s in enumerate(seeds):
            cos = float(vecs[i] @ vecs[s]) if vecs is not None else 0.0
            if should_merge(cos, toks[i], toks[s], comp[i], comp[s], long_src[i] or long_src[s]) and cos >= best_cos:
                best, best_cos = c, cos
        if best < 0:
            seeds.append(i)
            best = len(seeds) - 1
        labels[i] = best
    return labels


def summarize(items: pd.DataFrame, cross_bonus: float = 8.0) -> pd.DataFrame:
    rows = []
    for cid, g in items.groupby("cluster"):
        g = g.sort_values("score", ascending=False)
        top = g.iloc[0]
        srcs = sorted(set(g["source"]))
        firsts = g.dropna(subset=["first_seen"]).groupby("source")["first_seen"].min().astype(str)
        rows.append({
            "cluster": cid,
            "cluster_key": f"{top['source']}:{top['key']}",
            "primary": top["source"],
            "label": top["title"],
            "score": min(100.0, round(top["score"] + cross_bonus * (len(srcs) - 1), 1)),
            "stage": top["stage"],
            "sources": ", ".join(srcs),
            "n_sources": len(srcs),
            "n_items": len(g),
            "category": ", ".join(sorted({c for cs in g["category"].dropna() for c in cs.split(", ") if c})),
            "led_by": firsts.idxmin() if len(firsts) > 1 else "",
            "members": g["title"].tolist(),
        })
    return pd.DataFrame(rows).sort_values(["score", "n_sources"], ascending=False).reset_index(drop=True)


def cluster(items: pd.DataFrame, con=None, use_embeddings: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    items = items.sort_values("score", ascending=False).reset_index(drop=True)
    vecs = None
    if use_embeddings and len(items):
        try:
            vecs = cached_embed(con, items["text"].tolist())
        except llm.OllamaUnavailable:
            vecs = None  # lexical-only fallback
    items["cluster"] = assign(items, vecs)
    return summarize(items), items
