# Copyright 2026 Gods-Workbench Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""可选的本地图文嵌入；依赖或权重不可用时保留 FTS5 搜索。"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from gw.core.sqlite_vec_engine import SQLiteVecEngine

DEFAULT_MODEL_ID = "OFA-Sys/chinese-clip-vit-base-patch16"
MODEL_SPECS = {
    DEFAULT_MODEL_ID: 512,
    "google/siglip-base-patch16-224": 768,
}
DEFAULT_REVISION = "main"


class EmbeddingUnavailableError(RuntimeError):
    """本地嵌入依赖或模型权重不可用。"""


class _EmbeddingBackend(Protocol):
    model_version: str

    def encode_text(self, text: str) -> Sequence[float]: ...

    def encode_image(self, image: Any) -> Sequence[float]: ...


@dataclass(slots=True)
class _TransformersBackend:
    model_id: str
    model_version: str
    device: str
    processor: Any
    model: Any
    torch: Any

    def encode_text(self, text: str) -> Sequence[float]:
        text_config = getattr(self.model.config, "text_config", None)
        max_length = getattr(text_config, "max_position_embeddings", None)
        chinese_clip = self.model_id == DEFAULT_MODEL_ID
        tokenizer_options = (
            {"padding": True}
            if chinese_clip or not isinstance(max_length, int) or max_length < 1
            else {
                "padding": "max_length",
                "truncation": True,
                "max_length": max_length,
            }
        )
        batch = self.processor(text=[text], return_tensors="pt", **tokenizer_options)
        batch = {key: value.to(self.device) for key, value in batch.items()}
        with self.torch.inference_mode():
            if chinese_clip:
                # Transformers 4.57.5 的 ChineseCLIP get_text_features 会访问未启用的 pooler。
                outputs = self.model.text_model(**batch)
                features = self.model.text_projection(
                    outputs.last_hidden_state[:, 0, :]
                )
            else:
                features = self.model.get_text_features(**batch)
        return _first_vector(features, self.torch)

    def encode_image(self, image: Any) -> Sequence[float]:
        batch = self.processor(images=image, return_tensors="pt")
        batch = {key: value.to(self.device) for key, value in batch.items()}
        with self.torch.inference_mode():
            features = self.model.get_image_features(**batch)
        return _first_vector(features, self.torch)


def _first_vector(features: Any, torch: Any) -> list[float]:
    return features[0].detach().to(device="cpu", dtype=torch.float32).tolist()


def normalize_embedding(values: Sequence[float], dimensions: int) -> tuple[float, ...]:
    """验证维数、有限性并返回单位向量。"""
    if isinstance(values, (str, bytes, bytearray)):
        raise ValueError("嵌入必须是数值序列")
    try:
        vector = tuple(float(value) for value in values)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("嵌入必须只包含有限数值") from exc
    if len(vector) != dimensions:
        raise ValueError(f"嵌入维数必须为 {dimensions}")
    if not all(math.isfinite(value) for value in vector):
        raise ValueError("嵌入必须只包含有限数值")
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        raise ValueError("嵌入向量不能为空向量")
    return tuple(value / norm for value in vector)


def _model_cache_dir(data_root: str | Path | None) -> Path:
    if data_root is None:
        from gw.core.storage import data_root as get_data_root

        root = get_data_root()
    else:
        root = Path(data_root)
    if not root.is_absolute():
        raise ValueError("数据根目录必须是绝对路径")
    return root / "models" / "embeddings"


def download_model_weights(
    model_id: str = DEFAULT_MODEL_ID,
    *,
    data_root: str | Path | None = None,
    revision: str = DEFAULT_REVISION,
) -> Path:
    """显式下载模型到运行时 data 根；普通嵌入和检索始终仅访问本地缓存。"""
    if model_id not in MODEL_SPECS:
        raise ValueError(f"不支持的嵌入模型: {model_id}")
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError("revision 必须是非空字符串")
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise EmbeddingUnavailableError(
            "缺少 huggingface-hub；请安装 requirements-embedding.lock"
        ) from exc
    try:
        path = snapshot_download(
            repo_id=model_id,
            revision=revision,
            cache_dir=str(_model_cache_dir(data_root)),
            local_files_only=False,
            allow_patterns=[
                "*.json",
                "*.model",
                "*.safetensors",
                "*.bin",
                "*.txt",
                "*.tiktoken",
                "*.merges",
                "*.vocab",
            ],
        )
    except OSError as exc:
        raise EmbeddingUnavailableError(f"模型权重下载失败: {model_id}") from exc
    return Path(path)


class LocalMultimodalEmbedder:
    """本地 Chinese-CLIP 或 SigLIP 编码器，默认使用中文基准较优的 Chinese-CLIP。"""

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL_ID,
        *,
        data_root: str | Path | None = None,
        revision: str = DEFAULT_REVISION,
        device: str | None = None,
        backend: _EmbeddingBackend | None = None,
    ) -> None:
        if model_id not in MODEL_SPECS:
            raise ValueError(f"不支持的嵌入模型: {model_id}")
        if not isinstance(revision, str) or not revision.strip():
            raise ValueError("revision 必须是非空字符串")
        self.model_id = model_id
        self.model_version = revision
        self.dimensions = MODEL_SPECS[model_id]
        self.revision = revision
        self.device = device
        self._data_root = data_root
        self._backend = backend
        if backend is not None and backend.model_version:
            self.model_version = backend.model_version

    def _load_backend(self) -> _EmbeddingBackend:
        try:
            import torch
            from transformers import AutoModel, AutoProcessor
        except ImportError as exc:
            raise EmbeddingUnavailableError(
                "缺少嵌入依赖；请安装 requirements-embedding.lock"
            ) from exc

        device = self.device
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        model_options = {
            "revision": self.revision,
            "cache_dir": str(_model_cache_dir(self._data_root)),
            "local_files_only": True,
            "trust_remote_code": False,
        }
        try:
            processor = AutoProcessor.from_pretrained(self.model_id, **model_options)
            model = AutoModel.from_pretrained(self.model_id, **model_options)
        except Exception as exc:
            raise EmbeddingUnavailableError(
                f"本地模型权重不可用: {self.model_id}"
            ) from exc
        model.to(device)
        model.eval()
        model_version = (
            getattr(getattr(model, "config", None), "_commit_hash", None)
            or self.revision
        )
        self.device = device
        self.model_version = str(model_version)
        return _TransformersBackend(
            model_id=self.model_id,
            model_version=self.model_version,
            device=device,
            processor=processor,
            model=model,
            torch=torch,
        )

    def _get_backend(self) -> _EmbeddingBackend:
        if self._backend is None:
            self._backend = self._load_backend()
            self.model_version = self._backend.model_version
        return self._backend

    def encode_text(self, text: str) -> tuple[float, ...]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text 必须是非空字符串")
        return normalize_embedding(
            self._get_backend().encode_text(text), self.dimensions
        )

    def encode_image(self, image: Any) -> tuple[float, ...]:
        if image is None:
            raise ValueError("image 不能为空")
        return normalize_embedding(
            self._get_backend().encode_image(image), self.dimensions
        )

    def index_document(
        self,
        engine: SQLiteVecEngine,
        *,
        asset_id: str,
        title: str,
        content: str,
        image: Any | None = None,
    ) -> bool:
        """写入 FTS 文档和本地向量；嵌入缺失时只写 FTS5。"""
        try:
            embedding = (
                self.encode_image(image)
                if image is not None
                else self.encode_text(f"{title}\n{content}".strip())
            )
        except EmbeddingUnavailableError:
            return engine.upsert_document(
                asset_id=asset_id,
                title=title,
                content=content,
            )
        engine.switch_embedding_model(
            model_id=self.model_id,
            model_version=self.model_version,
            dimensions=self.dimensions,
        )
        return engine.upsert_document(
            asset_id=asset_id,
            title=title,
            content=content,
            embedding=embedding,
        )

    def search(
        self,
        engine: SQLiteVecEngine,
        query: str,
        *,
        limit: int = 20,
        candidate_limit: int | None = None,
    ):
        """嵌入可用且索引模型一致时融合双轨，否则只走 FTS5。"""
        from gw.asset_registry.hybrid_search import HybridAssetSearcher

        searcher = HybridAssetSearcher(engine)
        metadata = engine.index_metadata()
        if (
            not engine.vector_enabled
            or metadata is None
            or metadata.model_id != self.model_id
            or metadata.dimensions != self.dimensions
        ):
            return searcher.search(query, limit=limit, candidate_limit=candidate_limit)
        try:
            query_vector = self.encode_text(query)
        except EmbeddingUnavailableError:
            return searcher.search(query, limit=limit, candidate_limit=candidate_limit)
        if metadata.model_version != self.model_version:
            return searcher.search(query, limit=limit, candidate_limit=candidate_limit)
        return searcher.search(
            query,
            query_vector=query_vector,
            limit=limit,
            candidate_limit=candidate_limit,
        )
