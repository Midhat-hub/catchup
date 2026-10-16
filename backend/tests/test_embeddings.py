"""Offline unit tests for local embedding batching and dimensions."""

import pytest

from backend.app.index.embeddings import (
    DEFAULT_BATCH_SIZE,
    EMBEDDING_DIMENSIONS,
    EmbeddingError,
    embed_texts,
)


class FakeEmbeddingModel:
    def __init__(self, dimensions=EMBEDDING_DIMENSIONS, fail=False):
        self.dimensions = dimensions
        self.fail = fail
        self.received = []

    def embed(self, documents, batch_size=32):
        self.received.append((list(documents), batch_size))
        if self.fail:
            raise RuntimeError("model failure")
        return [[float(index)] * self.dimensions for index, _ in enumerate(documents)]


def test_embedding_batches_and_validates_384_dimensions():
    model = FakeEmbeddingModel()
    vectors = embed_texts(["one", "two"], model=model, batch_size=1)

    assert len(vectors) == 2
    assert all(len(vector) == 384 for vector in vectors)
    assert model.received == [(["one", "two"], 1)]


def test_empty_input_does_not_initialize_or_call_model():
    assert embed_texts([]) == []


def test_default_batch_size_uses_stable_single_input_batches():
    model = FakeEmbeddingModel()

    embed_texts(["one", "two"], model=model)

    assert model.received == [(["one", "two"], DEFAULT_BATCH_SIZE)]
    assert DEFAULT_BATCH_SIZE == 1


def test_embedding_rejects_wrong_dimension():
    with pytest.raises(EmbeddingError, match="expected 384"):
        embed_texts(["text"], model=FakeEmbeddingModel(dimensions=3))


def test_embedding_failure_is_reported():
    with pytest.raises(EmbeddingError, match="generation failed"):
        embed_texts(["text"], model=FakeEmbeddingModel(fail=True))