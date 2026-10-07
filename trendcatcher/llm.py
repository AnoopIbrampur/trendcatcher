"""Thin client for a local Ollama server (embeddings + generation)."""
import json

import numpy as np
import requests

from .config import EMBED_MODEL, LLM_MODEL, OLLAMA_HOST


class OllamaUnavailable(RuntimeError):
    pass


def available(timeout: float = 2) -> bool:
    try:
        return requests.get(f"{OLLAMA_HOST}/api/tags", timeout=timeout).ok
    except requests.RequestException:
        return False


def embed(texts: list[str], model: str = EMBED_MODEL, batch: int = 64) -> np.ndarray:
    vecs = []
    for i in range(0, len(texts), batch):
        try:
            r = requests.post(f"{OLLAMA_HOST}/api/embed", json={"model": model, "input": texts[i:i + batch]}, timeout=120)
            r.raise_for_status()
        except requests.RequestException as e:
            raise OllamaUnavailable(str(e)) from e
        vecs += r.json()["embeddings"]
    v = np.asarray(vecs, dtype=np.float32)
    return v / np.clip(np.linalg.norm(v, axis=1, keepdims=True), 1e-9, None)


def generate_json(prompt: str, model: str = LLM_MODEL, temperature: float = 0.4) -> dict:
    try:
        r = requests.post(f"{OLLAMA_HOST}/api/generate", timeout=300, json={
            "model": model, "prompt": prompt, "format": "json", "stream": False, "think": False,
            "options": {"temperature": temperature},
        })
        r.raise_for_status()
    except requests.RequestException as e:
        raise OllamaUnavailable(str(e)) from e
    return json.loads(r.json()["response"])
