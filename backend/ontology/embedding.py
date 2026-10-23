"""Deterministic local text embeddings (feature hashing of words and character trigrams).

Used for pgvector similarity retrieval without any network dependency. A hosted embedding
model can replace ``embed`` without changing callers as long as the dimension matches.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import List

DIM = 256
_WORD = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> List[str]:
    words = _WORD.findall(re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "").lower().replace("_", " "))
    out = []
    for w in words:
        out.append(w)
        if len(w) > 3 and w.endswith("s"):
            out.append(w[:-1])
    return out


def _h(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "little")


def embed(text: str) -> List[float]:
    vec = [0.0] * DIM
    for w in tokens(text):
        h = _h("w:" + w)
        vec[h % DIM] += 1.0 if (h >> 32) & 1 else -1.0
        padded = f"#{w}#"
        for i in range(len(padded) - 2):
            g = _h("g:" + padded[i:i + 3])
            vec[g % DIM] += 0.35 if (g >> 32) & 1 else -0.35
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


def cosine(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def to_pgvector(vec: List[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"
