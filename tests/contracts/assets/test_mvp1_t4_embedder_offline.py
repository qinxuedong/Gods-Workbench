"""MVP1-T4 local multimodal embedding and offline fallback contracts."""

from __future__ import annotations

import builtins
import socket
import sys
from types import ModuleType, SimpleNamespace

import pytest

from gw.asset_registry.embedder import (
    DEFAULT_MODEL_ID,
    MODEL_SPECS,
    EmbeddingUnavailableError,
    LocalMultimodalEmbedder,
)
from gw.core.sqlite_vec_engine import FTSMatch, IndexMetadata, SQLiteVecEngine


class _FakeBackend:
    def __init__(self, dimensions: int, model_version: str = "test-commit") -> None:
        self.dimensions = dimensions
        self.model_version = model_version

    def encode_text(self, text: str) -> list[float]:
        return [3.0, 4.0, *([0.0] * (self.dimensions - 2))]

    def encode_image(self, image) -> list[float]:
        return [0.0, 2.0, *([0.0] * (self.dimensions - 2))]


@pytest.mark.parametrize(
    ("model_id", "dimensions"),
    [
        (DEFAULT_MODEL_ID, 512),
        ("google/siglip-base-patch16-224", 768),
    ],
)
def test_text_and_image_embeddings_are_normalized(model_id, dimensions):
    embedder = LocalMultimodalEmbedder(
        model_id,
        backend=_FakeBackend(dimensions),
    )

    for vector in (embedder.encode_text("夜间的森林"), embedder.encode_image(object())):
        assert len(vector) == dimensions
        assert sum(value * value for value in vector) == pytest.approx(1.0)


def test_embedding_model_switch_changes_dimensions_and_preserves_fts(tmp_path):
    database = tmp_path / "embedding.sqlite3"
    with SQLiteVecEngine(database, dimensions=3) as engine:
        engine.upsert_document(
            asset_id="forest-1",
            title="Forest night",
            content="A quiet forest under the stars",
            embedding=[1.0, 0.0, 0.0],
        )
        engine.switch_embedding_model(
            model_id=DEFAULT_MODEL_ID,
            model_version="commit-a",
            dimensions=512,
        )
        assert engine.dimensions == 512
        assert engine.index_metadata() == IndexMetadata(
            model_id=DEFAULT_MODEL_ID,
            model_version="commit-a",
            dimensions=512,
            quantization="float32" if engine.vector_enabled else None,
        )
        vector_hash = engine._db.execute(
            "SELECT vector_hash FROM c18_documents WHERE asset_id=?",
            ("forest-1",),
        ).fetchone()[0]
        assert vector_hash is None
        assert [match.asset_id for match in engine.search_fts("forest")] == ["forest-1"]
        if engine.vector_enabled:
            schema = engine._db.execute(
                "SELECT sql FROM sqlite_master WHERE name='c18_vectors'"
            ).fetchone()[0]
            assert "float[512]" in schema

    with SQLiteVecEngine(database) as reopened:
        assert reopened.dimensions == 512
        assert [match.asset_id for match in reopened.search_fts("stars")] == [
            "forest-1"
        ]


def test_missing_optional_dependencies_fall_back_to_fts5(monkeypatch, tmp_path):
    real_import = builtins.__import__

    def import_without_torch(name, *args, **kwargs):
        if name == "torch":
            raise ModuleNotFoundError("torch is not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_torch)
    embedder = LocalMultimodalEmbedder(data_root=tmp_path, device="cpu")

    with SQLiteVecEngine(":memory:") as engine:
        indexed = embedder.index_document(
            engine,
            asset_id="forest-2",
            title="Forest night",
            content="A quiet forest under the stars",
        )
        assert indexed is False
        assert [match.asset_id for match in engine.search_fts("forest")] == ["forest-2"]

    class FTSOnlyEngine:
        vector_enabled = True

        def index_metadata(self):
            return IndexMetadata(
                model_id=DEFAULT_MODEL_ID,
                model_version="main",
                dimensions=MODEL_SPECS[DEFAULT_MODEL_ID],
                quantization="float32",
            )

        def search_fts(self, query, *, limit=20):
            return [FTSMatch(asset_id="forest-2", score=0.5)]

        def search_vectors(self, query_vector, *, limit=20):
            pytest.fail("缺少嵌入依赖时不应调用向量检索")

        @property
        def last_vector_search_degraded(self):
            return False

    results = embedder.search(FTSOnlyEngine(), "forest")
    assert [result.asset_id for result in results] == ["forest-2"]


def test_missing_local_model_weights_fall_back_to_fts5(monkeypatch, tmp_path):
    class MissingModel:
        @staticmethod
        def from_pretrained(model_id, **options):
            raise RuntimeError("model snapshot is not cached")

    torch_module = ModuleType("torch")
    transformers_module = ModuleType("transformers")
    transformers_module.AutoModel = MissingModel
    transformers_module.AutoProcessor = MissingModel
    monkeypatch.setitem(sys.modules, "torch", torch_module)
    monkeypatch.setitem(sys.modules, "transformers", transformers_module)
    embedder = LocalMultimodalEmbedder(data_root=tmp_path, device="cpu")

    with SQLiteVecEngine(":memory:") as engine:
        indexed = embedder.index_document(
            engine,
            asset_id="forest-3",
            title="Forest night",
            content="A quiet forest under the stars",
        )
        assert indexed is False
        assert [match.asset_id for match in engine.search_fts("stars")] == ["forest-3"]


def test_model_load_is_local_only_and_never_opens_network(monkeypatch, tmp_path):
    calls = []

    class FakeModel:
        config = SimpleNamespace(_commit_hash="a" * 40)

        def to(self, device):
            assert device == "cpu"
            return self

        def eval(self):
            return self

    class FakeAutoModel:
        @staticmethod
        def from_pretrained(model_id, **options):
            calls.append(("model", model_id, options))
            return FakeModel()

    class FakeProcessor:
        @staticmethod
        def from_pretrained(model_id, **options):
            calls.append(("processor", model_id, options))
            return object()

    torch_module = ModuleType("torch")
    transformers_module = ModuleType("transformers")
    transformers_module.AutoModel = FakeAutoModel
    transformers_module.AutoProcessor = FakeProcessor
    monkeypatch.setitem(sys.modules, "torch", torch_module)
    monkeypatch.setitem(sys.modules, "transformers", transformers_module)

    def reject_network(*args, **kwargs):
        pytest.fail("默认模型加载不得打开网络连接")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    monkeypatch.setattr(socket.socket, "connect", reject_network)
    embedder = LocalMultimodalEmbedder(
        data_root=tmp_path,
        revision="main",
        device="cpu",
    )

    embedder._get_backend()

    assert len(calls) == 2
    for _, model_id, options in calls:
        assert model_id == DEFAULT_MODEL_ID
        assert options["local_files_only"] is True
        assert options["trust_remote_code"] is False
        assert options["cache_dir"] == str(tmp_path / "models" / "embeddings")
    assert embedder.model_version == "a" * 40


def test_unknown_model_is_rejected():
    with pytest.raises(ValueError, match="不支持的嵌入模型"):
        LocalMultimodalEmbedder("untrusted/model")


def test_unavailable_model_dependencies_have_specific_error(monkeypatch):
    embedder = LocalMultimodalEmbedder(device="cpu")

    def unavailable():
        raise EmbeddingUnavailableError("optional dependency missing")

    monkeypatch.setattr(embedder, "_load_backend", unavailable)
    with pytest.raises(EmbeddingUnavailableError, match="optional dependency"):
        embedder.encode_text("landscape")
