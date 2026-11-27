# Copyright 2026 qinxuedong (Gods-Workbench Authors)
# SPDX-License-Identifier: FSL-1.1-Apache-2.0

"""按需使用本机 ASR 包与模型，为视频生成带绝对时间的台词片段。"""

from __future__ import annotations

import importlib
import importlib.util
import json
import math
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Mapping

from gw.core import storage

SETTINGS_NAMESPACE = "asset_registry_asr_settings"
DEFAULT_ENGINE = "funasr"
DEFAULT_MODELS = {"funasr": "paraformer-zh", "faster-whisper": "small"}
SUPPORTED_MODELS = {
    "funasr": frozenset({"paraformer-zh", "paraformer-zh-streaming"}),
    "faster-whisper": frozenset({"tiny", "base", "small", "medium", "large-v3"}),
}
_MODEL_CACHE: dict[tuple[str, str], Any] = {}
_MODEL_LOCK = threading.RLock()


def _settings_state():
    return storage.JsonState(
        SETTINGS_NAMESPACE,
        lambda: {"enabled": False, "engine": DEFAULT_ENGINE, "model": DEFAULT_MODELS[DEFAULT_ENGINE]},
    )


def settings() -> dict[str, Any]:
    raw = _settings_state().read()
    engine = raw.get("engine") if raw.get("engine") in SUPPORTED_MODELS else DEFAULT_ENGINE
    model = raw.get("model") if raw.get("model") in SUPPORTED_MODELS[engine] else DEFAULT_MODELS[engine]
    return {"enabled": raw.get("enabled") is True, "engine": engine, "model": model}


def update_settings(*, enabled: bool, engine: str, model: str) -> dict[str, Any]:
    if engine not in SUPPORTED_MODELS or model not in SUPPORTED_MODELS[engine]:
        raise ValueError("ASR 引擎或模型不受支持")
    _settings_state().mutate(lambda raw: raw.update(enabled=enabled, engine=engine, model=model))
    return settings()


def _package_installed(package: str) -> bool:
    try:
        return importlib.util.find_spec(package) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def model_path(engine: str, model: str) -> Path:
    if engine not in SUPPORTED_MODELS or model not in SUPPORTED_MODELS[engine]:
        raise ValueError("ASR 引擎或模型不受支持")
    return storage.data_root() / "models" / "asr" / engine / model


def model_status(engine: str | None = None, model: str | None = None) -> dict[str, Any]:
    selected = settings()
    engine = engine or selected["engine"]
    model = model or selected["model"]
    if engine not in SUPPORTED_MODELS or model not in SUPPORTED_MODELS[engine]:
        raise ValueError("ASR 引擎或模型不受支持")
    package = "funasr" if engine == "funasr" else "faster_whisper"
    installed = _package_installed(package)
    local_model = model_path(engine, model)
    try:
        weights_ready = local_model.is_dir() and any(local_model.iterdir())
    except OSError:
        weights_ready = False
    ffmpeg_available = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
    ready = installed and weights_ready and ffmpeg_available
    instructions = []
    if not installed:
        package_install = (
            "python -m pip install funasr modelscope"
            if engine == "funasr"
            else "python -m pip install faster-whisper"
        )
        instructions.append(f"在服务端 Python 环境手动安装可选包：{package_install}。不会自动安装。")
    if not weights_ready:
        instructions.append(
            f"手动准备模型权重到 <data_root>/models/asr/{engine}/{model}，不会自动下载。"
        )
    if not ffmpeg_available:
        instructions.append("安装 ffmpeg/ffprobe 并确保其位于服务端 PATH。")
    install_hint = " ".join(instructions)
    if install_hint:
        missing = []
        if not installed:
            missing.append(f"可选依赖 {package}")
        if not weights_ready:
            missing.append("模型权重")
        if not ffmpeg_available:
            missing.append("ffmpeg/ffprobe")
        message = f"尚未就绪：{'、'.join(missing)}。{install_hint}"
    else:
        message = "本机 ASR 依赖、模型与 ffmpeg 已就绪。"
    return {
        "engine": engine,
        "model": model,
        "enabled": selected["enabled"],
        "dependency_installed": installed,
        "weights_ready": weights_ready,
        "ffmpeg_available": ffmpeg_available,
        "ready": ready,
        "message": message,
        "install_hint": install_hint,
        "supported_models": {key: sorted(value) for key, value in SUPPORTED_MODELS.items()},
    }


def _run(args: list[str], *, timeout: int, capture_output: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        check=True,
        capture_output=capture_output,
        text=True,
        timeout=timeout,
    )


def _probe_has_audio(source: Path) -> bool:
    executable = shutil.which("ffprobe")
    if executable is None:
        raise RuntimeError("ASR_MEDIA_TOOL_UNAVAILABLE: 请安装 ffprobe。")
    try:
        result = _run(
            [executable, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "json", str(source)],
            timeout=30,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("ASR_MEDIA_PROBE_FAILED: 无法读取视频音轨。") from exc
    try:
        streams = json.loads(result.stdout or "{}").get("streams") or []
    except (TypeError, ValueError):
        streams = []
    return bool(streams)


def _extract_audio(source: Path) -> tuple[Path, tempfile.TemporaryDirectory] | None:
    if not _probe_has_audio(source):
        return None
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise RuntimeError("ASR_MEDIA_TOOL_UNAVAILABLE: 请安装 ffmpeg。")
    temp_root = storage.data_root() / "tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    temp_dir = tempfile.TemporaryDirectory(prefix="gw-asr-", dir=temp_root)
    audio = Path(temp_dir.name) / "audio.wav"
    try:
        _run(
            [executable, "-nostdin", "-v", "error", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-y", str(audio)],
            timeout=1200,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        temp_dir.cleanup()
        raise RuntimeError("ASR_AUDIO_EXTRACTION_FAILED: 提取 16kHz 单声道音频失败。") from exc
    return audio, temp_dir


def _finite_seconds(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return seconds if math.isfinite(seconds) else None


def normalize_asr_segments(raw: Any) -> list[dict[str, Any]]:
    """兼容两引擎常见输出，只保留带真实边界的识别句段。"""
    values = raw if isinstance(raw, list) else [raw]
    result: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, Mapping):
            continue
        nested = value.get("segments") or value.get("sentence_info") or value.get("sentences")
        if isinstance(nested, list):
            sources = nested
        else:
            sources = [value]
        for source in sources:
            if not isinstance(source, Mapping):
                continue
            start = source.get("start_sec", source.get("start", source.get("begin")))
            end = source.get("end_sec", source.get("end", source.get("finish")))
            times = source.get("timestamp") or source.get("time")
            funasr_milliseconds = "start_sec" not in source and "end_sec" not in source
            if (start is None or end is None) and isinstance(times, (list, tuple)) and len(times) == 2:
                start, end = times
                funasr_milliseconds = True
            start_sec = _finite_seconds(start)
            end_sec = _finite_seconds(end)
            # FunASR sentence_info timestamps are milliseconds; faster-whisper values
            # are normalized to *_sec before reaching this adapter.
            if funasr_milliseconds and start_sec is not None and end_sec is not None:
                start_sec, end_sec = start_sec / 1000, end_sec / 1000
            text = str(source.get("text") or source.get("sentence") or "").strip()
            if start_sec is None or end_sec is None or start_sec < 0 or end_sec <= start_sec or not text:
                continue
            result.append({"start_sec": start_sec, "end_sec": end_sec, "text": text})
    return sorted(result, key=lambda item: (item["start_sec"], item["end_sec"]))


def map_transcripts_to_video_segments(video_segments: Any, transcripts: Any) -> list[dict[str, Any]]:
    """把视频绝对时间的 ASR 句段按区间交集挂到镜头。"""
    scenes = video_segments if isinstance(video_segments, list) else []
    utterances = normalize_asr_segments(transcripts)
    result: list[dict[str, Any]] = []
    for scene in scenes:
        if not isinstance(scene, Mapping):
            continue
        start = _finite_seconds(scene.get("start_sec"))
        end = _finite_seconds(scene.get("end_sec"))
        copied = dict(scene)
        mapped = []
        if start is not None and end is not None and start >= 0 and end > start:
            for utterance in utterances:
                overlap_start = max(start, utterance["start_sec"])
                overlap_end = min(end, utterance["end_sec"])
                if overlap_end > overlap_start:
                    mapped.append({
                        "start_sec": overlap_start,
                        "end_sec": overlap_end,
                        "text": utterance["text"],
                    })
        copied["transcript"] = mapped
        result.append(copied)
    return result


def _load_model(engine: str, model: str):
    key = (engine, model)
    with _MODEL_LOCK:
        if key in _MODEL_CACHE:
            return _MODEL_CACHE[key]
        local_path = str(model_path(engine, model))
        if engine == "funasr":
            AutoModel = importlib.import_module("funasr").AutoModel
            instance = AutoModel(model=local_path, device="cpu", disable_update=True)
        else:
            WhisperModel = importlib.import_module("faster_whisper").WhisperModel
            instance = WhisperModel(
                local_path,
                device="cpu",
                compute_type="int8",
                local_files_only=True,
            )
        _MODEL_CACHE[key] = instance
        return instance


def _recognize(audio: Path, engine: str, model: str) -> list[dict[str, Any]]:
    instance = _load_model(engine, model)
    if engine == "funasr":
        raw = instance.generate(input=str(audio), batch_size_s=300, sentence_timestamp=True)
    else:
        segments, _info = instance.transcribe(str(audio), vad_filter=True)
        raw = [
            {"start_sec": item.start, "end_sec": item.end, "text": item.text}
            for item in segments
        ]
    return normalize_asr_segments(raw)


def transcribe_video(source: Path, *, engine: str, model: str) -> list[dict[str, Any]] | None:
    """返回句段列表；无音轨时返回 None。临时 WAV 在函数返回前删除。"""
    extracted = _extract_audio(source)
    if extracted is None:
        return None
    audio, temp_dir = extracted
    try:
        return _recognize(audio, engine, model)
    finally:
        temp_dir.cleanup()
