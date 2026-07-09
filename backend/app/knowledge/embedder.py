"""Local text embeddings for the Knowledge Store.

Groq exposes no embeddings endpoint, so vectors are computed locally. The
default is a hashed word+bigram TF vector — deterministic, dependency-free,
and honest about what it is: lexical similarity, not semantic. It is good
enough for "have we seen this stack trace / ticket title before", which is
what the store is queried with. If `fastembed` is installed (ONNX — no wheels
for Python 3.14 as of mid-2026, which is why it isn't in requirements.txt),
it is used automatically instead; the interface stays identical so nothing
above this module changes.
"""
from __future__ import annotations

import re
from functools import lru_cache

import numpy as np

DIM = 512
_WORD_RE = re.compile(r"[a-z0-9_.]+")
# Function words carry no signal for "have we seen this before" matching and
# dilute the cosine badly on short texts — drop them before hashing.
_STOPWORDS = frozenset(
    "a an and are as at be but by for from has have if in into is it its of on or "
    "should that the this to use we what when where which will with you your".split()
)


class HashingEmbedder:
    """Hashed bag-of-words + bigrams, L2-normalized. Lexical, not semantic:
    strong on token-rich inputs (stack traces, scope docs), weak on short
    abstract queries — install fastembed for those."""

    name = "hashing-tf-512"
    dim = DIM

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), DIM), dtype=np.float32)
        for i, text in enumerate(texts):
            words = [w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS]
            for w in words:
                out[i, hash(w) % DIM] += 1.0
            for a, b in zip(words, words[1:]):
                out[i, hash(a + " " + b) % DIM] += 1.0
            norm = np.linalg.norm(out[i])
            if norm > 0:
                out[i] /= norm
        return out


class FastEmbedEmbedder:
    """Real semantic embeddings via fastembed/ONNX, when installed."""

    name = "fastembed-bge-small"

    def __init__(self) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding("BAAI/bge-small-en-v1.5")
        self.dim = 384

    def embed(self, texts: list[str]) -> np.ndarray:
        vecs = np.array(list(self._model.embed(texts)), dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms


@lru_cache
def get_embedder():
    try:
        return FastEmbedEmbedder()
    except ImportError:
        return HashingEmbedder()
