"""Lazy local code embeddings backed by FastEmbed and BGE Small EN v1.5."""

from __future__ import annotations

import logging
from typing import Iterable, Protocol, Sequence

logger = logging.getLogger(__name__)

MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIMENSIONS = 384
DEFAULT_BATCH_SIZE = 1


class EmbeddingError(RuntimeError):
    """Raised when the local embedding model cannot produce valid vectors."""


class EmbeddingModel(Protocol):
    def embed(
        self,
        documents: Sequence[str],
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> Iterable[Iterable[float]]:
        """Return vectors in input order."""
        ...


_model: EmbeddingModel | None = None


def _load_model() -> EmbeddingModel:
    """Load the model on first use; FastEmbed downloads it if not cached."""
    global _model
    if _model is None:
        try:
            from fastembed import TextEmbedding

            logger.info("Loading local embedding model %s", MODEL_NAME)
            _model = TextEmbedding(model_name=MODEL_NAME)
        except Exception as exc:
            raise EmbeddingError(
                f"Could not load {MODEL_NAME}. Check network access for the initial model download "
                "and available disk space, then retry."
            ) from exc
    return _model


def embed_texts(
    texts: Sequence[str],
    *,
    model: EmbeddingModel | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[list[float]]:
    """Embed texts locally and verify every result matches pgvector(384)."""
    if not texts:
        return []
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if any(not isinstance(text, str) for text in texts):
        raise TypeError("All embedding inputs must be strings")

    active_model = model if model is not None else _load_model()
    try:
        vectors = [list(map(float, vector)) for vector in active_model.embed(texts, batch_size=batch_size)]
    except Exception as exc:
        raise EmbeddingError(f"Local embedding generation failed using {MODEL_NAME}.") from exc

    if len(vectors) != len(texts):
        raise EmbeddingError(f"Embedding model returned {len(vectors)} vectors for {len(texts)} inputs.")
    for index, vector in enumerate(vectors):
        if len(vector) != EMBEDDING_DIMENSIONS:
            raise EmbeddingError(
                f"Embedding {index} has {len(vector)} dimensions; expected {EMBEDDING_DIMENSIONS}."
            )
    return vectors