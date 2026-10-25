"""Offline unit tests plus an opt-in subprocess check for the real model."""

import os
import subprocess
import sys

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


@pytest.mark.skipif(
    os.environ.get("CATCHUP_TEST_REAL_EMBEDDINGS") != "1",
    reason="Set CATCHUP_TEST_REAL_EMBEDDINGS=1 to run/download the real FastEmbed model.",
)
def test_real_fastembed_model_in_isolated_process():
    """Keep native crashes isolated from pytest and report their process exit code."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from backend.app.index.embeddings import embed_texts; "
            "v = embed_texts(['Catchup native embedding smoke test']); "
            "assert len(v) == 1 and len(v[0]) == 384",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, (
        f"Real FastEmbed subprocess exited {result.returncode}.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
