"""离线比较 Chinese-CLIP 与 SigLIP 的图文检索表现。"""

from __future__ import annotations

import argparse
import contextlib
import gc
import json
import math
import os
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

MODEL_IDS = {
    "chinese_clip": "OFA-Sys/chinese-clip-vit-base-patch16",
    "siglip": "google/siglip-base-patch16-224",
}
IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
MAX_QUERY_FILE_BYTES = 1_000_000


class BenchmarkError(Exception):
    """输入或模型运行错误。"""


class PeakRssSampler:
    """在独立进程中采样本次模型运行的峰值 RSS。"""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self.peak_bytes = 0
        self._thread: threading.Thread | None = None

    def _sample(self) -> None:
        try:
            import psutil

            process = psutil.Process(os.getpid())
            while not self._stop.is_set():
                self.peak_bytes = max(self.peak_bytes, process.memory_info().rss)
                self._stop.wait(0.02)
            self.peak_bytes = max(self.peak_bytes, process.memory_info().rss)
        except Exception:
            return

    def start(self) -> None:
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self._thread.start()

    def stop(self) -> int | None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        return self.peak_bytes or None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="比较 Chinese-CLIP 与 SigLIP 的中文图文检索表现并输出 JSON。"
    )
    parser.add_argument("--root", required=True, type=Path, help="图片子集目录")
    parser.add_argument("--queries", required=True, type=Path, help="UTF-8 JSONL 标注文件")
    parser.add_argument(
        "--max-images", type=int, default=500, help="最多评测图片数，范围 10–500（默认 500）"
    )
    parser.add_argument("--warmup-images", type=int, default=5, help="每个设备预热图片数")
    parser.add_argument("--_single-model", choices=MODEL_IDS, help=argparse.SUPPRESS)
    return parser


def _load_inputs(root_arg: Path, queries_arg: Path, max_images: int):
    root = root_arg.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise BenchmarkError(f"图片根路径不是目录：{root}")
    if not 10 <= max_images <= 500:
        raise BenchmarkError("--max-images 必须在 10 到 500 之间。")

    query_path = queries_arg.expanduser().resolve(strict=True)
    if query_path.stat().st_size > MAX_QUERY_FILE_BYTES:
        raise BenchmarkError("查询 JSONL 超过 1 MB 限制。")
    queries: list[dict[str, str]] = []
    with query_path.open("r", encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BenchmarkError(f"查询文件第 {line_no} 行不是有效 JSON：{exc}") from exc
            if not isinstance(row, dict) or set(row) != {"image", "query"}:
                raise BenchmarkError(
                    f"查询文件第 {line_no} 行必须且只能包含 image 与 query 两个字段。"
                )
            image, query = row["image"], row["query"]
            if not isinstance(image, str) or not image or Path(image).is_absolute():
                raise BenchmarkError(f"查询文件第 {line_no} 行的 image 必须是非空相对路径。")
            if Path(image).suffix.lower() not in IMAGE_SUFFIXES or ".." in Path(image).parts:
                raise BenchmarkError(f"查询文件第 {line_no} 行的 image 路径不安全或不是图片。")
            if not isinstance(query, str) or not query.strip() or not any(
                "\u4e00" <= char <= "\u9fff" for char in query
            ):
                raise BenchmarkError(f"查询文件第 {line_no} 行的 query 必须包含中文文本。")
            queries.append({"image": image.replace("\\", "/"), "query": query.strip()})
    if len(queries) < 30:
        raise BenchmarkError(f"至少需要 30 条人工标注查询，当前只有 {len(queries)} 条。")

    paths = sorted(
        path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    if len(paths) < 10:
        raise BenchmarkError(f"图片目录至少需要 10 张图片，当前找到 {len(paths)} 张。")
    selected = paths[:max_images]
    relative_paths: list[str] = []
    for path in selected:
        resolved = path.resolve(strict=True)
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise BenchmarkError(f"图片路径越出根目录：{path}") from exc
        relative_paths.append(relative)

    selected_set = set(relative_paths)
    for row in queries:
        query_image = (root / row["image"]).resolve(strict=False)
        try:
            normalized = query_image.relative_to(root).as_posix()
        except ValueError as exc:
            raise BenchmarkError(f"查询图片路径越出根目录：{row['image']}") from exc
        if normalized not in selected_set:
            raise BenchmarkError(f"查询图片未进入评测子集：{row['image']}（可增大 --max-images）。")
        if not query_image.is_file():
            raise BenchmarkError(f"查询图片不存在：{row['image']}")

    return root, [root / path for path in relative_paths], relative_paths, queries


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _latency_summary(values: list[float]) -> dict[str, float]:
    return {
        "p50_ms": round(statistics.median(values), 3),
        "p95_ms": round(_percentile(values, 0.95), 3),
        "mean_ms": round(statistics.fmean(values), 3),
    }


def _checkpoint_size_bytes(model_id: str) -> int | None:
    try:
        from huggingface_hub import snapshot_download

        snapshot = Path(snapshot_download(model_id, local_files_only=True))
        files = list(snapshot.glob("*.safetensors")) + list(snapshot.glob("pytorch_model*.bin"))
        return sum(path.stat().st_size for path in files) or None
    except Exception:
        return None


def _run_device(
    model: Any,
    model_id: str,
    processor: Any,
    image_paths: list[Path],
    relative_paths: list[str],
    queries: list[dict[str, str]],
    device: str,
    warmup: int,
    torch: Any,
) -> dict[str, Any]:
    from PIL import Image

    if device == "cuda":
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
    model.to(device)
    model.eval()

    def encode_query_batch():
        text_config = getattr(model.config, "text_config", None)
        max_length = getattr(text_config, "max_position_embeddings", None)
        is_chinese_clip = model_id == MODEL_IDS["chinese_clip"]
        tokenizer_options = (
            {"padding": True}
            if is_chinese_clip or not isinstance(max_length, int) or max_length < 1
            else {"padding": "max_length", "truncation": True, "max_length": max_length}
        )
        batch = processor(
            text=[row["query"] for row in queries],
            return_tensors="pt",
            **tokenizer_options,
        )
        batch = {key: value.to(device) for key, value in batch.items()}
        with torch.inference_mode():
            if is_chinese_clip:
                # Transformers 4.57.5 的 ChineseCLIPModel 未启用 pooler，却在
                # get_text_features 中读取 pooler_output；按 OFA-Sys encode_text
                # 的 CLS 表示与 text_projection 路径计算，保持官方模型语义。
                text_outputs = model.text_model(**batch)
                features = model.text_projection(text_outputs.last_hidden_state[:, 0, :])
            else:
                features = model.get_text_features(**batch)
        return torch.nn.functional.normalize(features.float(), p=2, dim=-1)

    with torch.inference_mode():
        query_vectors = encode_query_batch()
    embedding_dimension = int(query_vectors.shape[-1])

    def encode_one(image):
        batch = processor(images=image, return_tensors="pt")
        batch = {key: value.to(device) for key, value in batch.items()}
        with torch.inference_mode():
            features = model.get_image_features(**batch)
        if device == "cuda":
            torch.cuda.synchronize()
        return torch.nn.functional.normalize(features.float(), p=2, dim=-1)

    warmup_count = min(warmup, len(image_paths))
    for path in image_paths[:warmup_count]:
        with Image.open(path) as source:
            encode_one(source.convert("RGB"))

    latencies: list[float] = []
    image_vectors = []
    total = len(image_paths)
    for index, path in enumerate(image_paths, start=1):
        with Image.open(path) as source:
            image = source.convert("RGB")
        started = time.perf_counter()
        features = encode_one(image)
        elapsed_ms = (time.perf_counter() - started) * 1000
        latencies.append(elapsed_ms)
        image_vectors.append(features.detach().to(device="cpu", dtype=torch.float32))
        if index == 1 or index % 25 == 0 or index == total:
            print(f"  {device}: {index}/{total} 张图片", file=sys.stderr, flush=True)

    images = torch.cat(image_vectors, dim=0)
    scores = query_vectors @ images.to(device).T
    path_to_index = {path: index for index, path in enumerate(relative_paths)}
    recalls = {}
    for k in (1, 5, 10):
        hits = 0
        for query_index, row in enumerate(queries):
            target = path_to_index[row["image"]]
            top_k = torch.topk(scores[query_index], k=min(k, len(image_paths))).indices.tolist()
            hits += int(target in top_k)
        recalls[f"recall_at_{k}"] = round(hits / len(queries), 6)

    peak_vram = None
    if device == "cuda":
        peak_vram = round(torch.cuda.max_memory_allocated() / (1024**2), 2)
    del query_vectors, images, image_vectors
    gc.collect()
    if device == "cuda":
        torch.cuda.empty_cache()
    return {
        "status": "ok",
        "embedding_dimension": embedding_dimension,
        "image_latency": _latency_summary(latencies),
        "peak_vram_allocated_mb": peak_vram,
        "recall": recalls,
    }


def _benchmark_one(
    model_id: str,
    image_paths: list[Path],
    relative_paths: list[str],
    queries: list[dict[str, str]],
    warmup: int,
) -> dict[str, Any]:
    sampler = PeakRssSampler()
    sampler.start()
    try:
        try:
            import transformers
            from transformers import AutoModel, AutoProcessor

            processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=False)
            model = AutoModel.from_pretrained(model_id, trust_remote_code=False)
        except Exception as exc:
            detail = str(exc).strip().replace("\n", " ")[:500]
            raise BenchmarkError(
                f"无法加载模型 {model_id}（{type(exc).__name__}: {detail}）。"
                "请确认临时基准环境已安装 transformers、torch、sentencepiece，"
                "且 Hugging Face 模型可下载或已缓存。"
            ) from exc

        try:
            import torch
        except ImportError as exc:
            raise BenchmarkError("缺少 torch。请在临时基准环境安装匹配 CUDA 的 PyTorch。") from exc

        if not hasattr(model, "get_image_features") or not hasattr(model, "get_text_features"):
            raise BenchmarkError(f"模型 {model_id} 未提供图像和文本特征接口。")
        parameter_count = sum(parameter.numel() for parameter in model.parameters())
        parameter_bytes = sum(parameter.numel() * parameter.element_size() for parameter in model.parameters())
        checkpoint_bytes = _checkpoint_size_bytes(model_id)

        devices = ["cuda", "cpu"] if torch.cuda.is_available() else ["cpu"]
        runtime = {
            "python_version": sys.version.split()[0],
            "torch_version": str(torch.__version__),
            "torch_cuda_version": torch.version.cuda,
            "transformers_version": getattr(transformers, "__version__", "unknown"),
            "cpu_threads": torch.get_num_threads(),
            "cuda_device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        }
        device_results: dict[str, Any] = {}
        for device in devices:
            print(f"  {model_id}: 开始 {device} 编码", file=sys.stderr, flush=True)
            try:
                device_results[device] = _run_device(
                    model,
                    model_id,
                    processor,
                    image_paths,
                    relative_paths,
                    queries,
                    device,
                    warmup,
                    torch,
                )
            except Exception as exc:
                raise BenchmarkError(
                    f"{model_id} 在 {device} 评测失败（{type(exc).__name__}: {str(exc)[:500]}）。"
                ) from exc
        if "cuda" not in device_results:
            device_results["cuda"] = {
                "status": "unavailable",
                "reason": "torch.cuda.is_available() 为 false",
                "image_latency": None,
                "peak_vram_allocated_mb": None,
                "recall": None,
            }
        embedding_dimension = next(
            result["embedding_dimension"]
            for result in device_results.values()
            if result.get("status") == "ok"
        )

        result = {
            "model_id": model_id,
            "runtime": runtime,
            "embedding_dimension": embedding_dimension,
            "parameter_count": parameter_count,
            "parameter_bytes": parameter_bytes,
            "checkpoint_bytes": checkpoint_bytes,
            "devices": device_results,
            "image_count": len(image_paths),
            "query_count": len(queries),
            "latency_definition": "含单张图片预处理、张量传输与图像编码前向；不含磁盘读取/解码、模型加载与文本编码。",
        }
    finally:
        peak_bytes = sampler.stop()
    result["peak_process_rss_mb"] = round((peak_bytes or 0) / (1024**2), 2)
    return result


def _single_model_main(args: argparse.Namespace) -> int:
    try:
        _root, image_paths, relative_paths, queries = _load_inputs(
            args.root, args.queries, args.max_images
        )
        result = _benchmark_one(
            MODEL_IDS[args._single_model], image_paths, relative_paths, queries, args.warmup_images
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except BenchmarkError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 3


def _parent_main(args: argparse.Namespace) -> int:
    command_base = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--root",
        str(args.root.expanduser().resolve()),
        "--queries",
        str(args.queries.expanduser().resolve()),
        "--max-images",
        str(args.max_images),
        "--warmup-images",
        str(args.warmup_images),
    ]
    runs = []
    for name, model_id in MODEL_IDS.items():
        print(f"开始评测 {name}（{model_id}）", file=sys.stderr, flush=True)
        completed = subprocess.run(
            [*command_base, "--_single-model", name],
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            encoding="utf-8",
        )
        if completed.returncode:
            if completed.stderr:
                print(completed.stderr.rstrip(), file=sys.stderr)
            elif completed.stdout:
                print(completed.stdout.rstrip(), file=sys.stderr)
            return completed.returncode
        try:
            runs.append(json.loads(completed.stdout))
        except json.JSONDecodeError as exc:
            print(f"错误：{model_id} 子进程未输出有效 JSON：{exc}", file=sys.stderr)
            return 4
    print(
        json.dumps(
            {
                "schema_version": 1,
                "root": str(args.root.expanduser().resolve()),
                "query_file": str(args.queries.expanduser().resolve()),
                "max_images": args.max_images,
                "runs": runs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.warmup_images < 0:
        parser.error("--warmup-images 不能小于 0")
    try:
        if args._single_model:
            return _single_model_main(args)
        return _parent_main(args)
    except OSError as exc:
        print(f"错误：无法访问输入路径（{exc}）", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
