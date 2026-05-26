from __future__ import annotations

import hashlib
import json
import math
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .paths import default_embedding_cache_path


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


class EmbeddingBackend:
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class EmbeddingCache:
    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None
        self._data: dict[str, list[float]] = {}
        self._loaded = False

    def get_many(self, keys: list[str]) -> dict[str, list[float]]:
        self._load()
        return {key: self._data[key] for key in keys if key in self._data}

    def set_many(self, values: dict[str, list[float]]) -> None:
        if not values:
            return
        self._load()
        self._data.update(values)
        self._save()

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(data, dict):
            self._data = {
                str(key): [float(item) for item in value]
                for key, value in data.items()
                if isinstance(value, list)
            }

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data), encoding="utf-8")


class GLMEmbeddingBackend(EmbeddingBackend):
    def __init__(self) -> None:
        self.base_url = (
            os.getenv("PREFERENCE_EMBEDDING_BASE_URL")
            or os.getenv("GLM_EMBEDDING_BASE_URL")
            or "https://open.bigmodel.cn/api/paas/v4"
        ).rstrip("/")
        self.api_key = (
            os.getenv("PREFERENCE_EMBEDDING_API_KEY")
            or os.getenv("GLM_API_KEY")
            or os.getenv("ZHIPUAI_API_KEY")
            or ""
        )
        self.model = os.getenv("PREFERENCE_EMBEDDING_MODEL", "embedding-3")
        self.dimensions = int(os.getenv("PREFERENCE_EMBEDDING_DIMENSIONS", "1024"))
        self.timeout = float(os.getenv("PREFERENCE_EMBEDDING_TIMEOUT", os.getenv("PREFERENCE_MODEL_TIMEOUT", "90")))
        self.batch_size = max(1, min(64, int(os.getenv("PREFERENCE_EMBEDDING_BATCH_SIZE", "64"))))
        self.cache = EmbeddingCache(_default_cache_path())
        if not self.api_key:
            raise RuntimeError("missing PREFERENCE_EMBEDDING_API_KEY for GLM embedding backend")

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        normalized = [_normalize_text(text) for text in texts]
        keys = [self._cache_key(text) for text in normalized]
        cached = self.cache.get_many(keys)
        result: list[list[float] | None] = [cached.get(key) for key in keys]
        missing_items = [(index, text, keys[index]) for index, text in enumerate(normalized) if result[index] is None]
        for start in range(0, len(missing_items), self.batch_size):
            batch = missing_items[start : start + self.batch_size]
            batch_vectors = self._embed_remote([item[1] for item in batch])
            if len(batch_vectors) != len(batch):
                raise RuntimeError(f"embedding response count mismatch: expected {len(batch)}, got {len(batch_vectors)}")
            cache_update: dict[str, list[float]] = {}
            for (index, _text, key), vector in zip(batch, batch_vectors):
                normalized_vector = _normalize_vector(vector)
                result[index] = normalized_vector
                cache_update[key] = normalized_vector
            self.cache.set_many(cache_update)
        return [vector or [] for vector in result]

    def _embed_remote(self, texts: list[str]) -> list[list[float]]:
        body = {
            "model": self.model,
            "input": texts,
            "dimensions": self.dimensions,
        }
        request = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"embedding request failed: HTTP {exc.code} {error_body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"embedding request failed: {exc}") from exc
        items = data.get("data", []) if isinstance(data, dict) else []
        items = sorted(items, key=lambda item: int(item.get("index", 0))) if items else []
        return [[float(value) for value in item.get("embedding", [])] for item in items]

    def _cache_key(self, text: str) -> str:
        raw = f"glm|{self.model}|{self.dimensions}|{text}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


class HashEmbeddingBackend(EmbeddingBackend):
    """Deterministic local fallback for tests and offline development."""

    def __init__(self, dimensions: int | None = None) -> None:
        self.dimensions = dimensions or int(os.getenv("PREFERENCE_HASH_EMBEDDING_DIMENSIONS", "256"))

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [_normalize_vector(self._embed_one(text)) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0 for _ in range(self.dimensions)]
        normalized = _normalize_text(text)
        grams = _char_grams(normalized)
        for gram in grams:
            digest = hashlib.sha256(gram.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        return vector


def build_embedding_backend(name: str | None = None) -> EmbeddingBackend:
    backend = (name or os.getenv("PREFERENCE_EMBEDDING_BACKEND") or "auto").strip().lower()
    if backend in {"glm", "bigmodel", "zhipu", "embedding-3"}:
        return GLMEmbeddingBackend()
    if backend in {"hash", "heuristic", "local"}:
        return HashEmbeddingBackend()
    if backend == "auto":
        if os.getenv("PREFERENCE_EMBEDDING_API_KEY") or os.getenv("GLM_API_KEY") or os.getenv("ZHIPUAI_API_KEY"):
            return GLMEmbeddingBackend()
        return HashEmbeddingBackend()
    return HashEmbeddingBackend()


def _default_cache_path() -> Path:
    return default_embedding_cache_path()


def _normalize_text(text: str) -> str:
    return " ".join(str(text).split()).strip()


def _normalize_vector(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        return vector
    return [value / norm for value in vector]


def _char_grams(text: str) -> list[str]:
    compact = "".join(text.casefold().split())
    if not compact:
        return []
    if len(compact) <= 2:
        return [compact]
    grams = [compact[index : index + 2] for index in range(len(compact) - 1)]
    grams.extend(compact[index : index + 3] for index in range(max(0, len(compact) - 2)))
    return grams
