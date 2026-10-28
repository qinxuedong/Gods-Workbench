#!/usr/bin/env python3
"""4K ProRes Proxy / LT 代理生成实测脚本（QIN-444 / GitHub Issue #389）。

用途：
    在本机用 FFmpeg `prores_ks` 编码器生成 ProRes 422 Proxy（profile 0）与
    ProRes 422 LT（profile 1）代理，记录耗时、体积、码率、平均占用核数与峰值内存，
    供「4K 代理是否纳入后续 Cut」的判断使用。

设计约束（对应票面 AC）：
    AC2 不改源文件：真实素材只以只读方式喂给 FFmpeg，并在编码前后比对 SHA-256。
    AC3 不进 CI：脚本默认不产生任何仓库内产物，结果写到 `--out-dir` 指定的目录。

两段式量测（保证测的是编码而非滤镜）：
    1. 合成样本先用 lavfi 生成一个 4K 中间源（x264 ultrafast），**不计时**；
    2. 再用同一路径把中间源（或真实素材）编码为 ProRes，**只对这一步计时**。

典型用法：
    # 合成 4K 样本（本机无真实 4K 素材时使用，报告中会标记 synthetic）
    python tools/prores_proxy_spike.py --synthetic --out-dir <输出目录>

    # 加上真实素材作为对照（只读，可重复 -P/--real）
    python tools/prores_proxy_spike.py --synthetic \
        --real <真实素材路径 1> --real <真实素材路径 2> --out-dir <输出目录>

    # 只测真实素材
    python tools/prores_proxy_spike.py --real <素材路径> --out-dir <输出目录>

输出：
    <out-dir>/prores_spike.json   结构化结果
    <out-dir>/prores_spike.md     人读报告（中文表格）
    <out-dir>/*.mov               生成的代理样本（仅用于量测体积，可随后删除）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

PROFILES: dict[str, int] = {"proxy": 0, "lt": 1}
PROFILE_LABELS = {"proxy": "ProRes 422 Proxy", "lt": "ProRes 422 LT"}

# 合成样本覆盖四种典型负载：静态画面下限、颗粒/高细节上限、DCI 4K 标准、高帧率。
# 均为 FFmpeg lavfi 内置源，可复现、不依赖外部素材。
SYNTHETIC_SAMPLES: tuple[dict[str, object], ...] = (
    {
        "sample_id": "synth_uhd_lowmotion",
        "label": "UHD 3840x2160 25p 低动态（smptebars）",
        "filter": "smptebars",
        "vf": None,
        "width": 3840,
        "height": 2160,
        "fps": 25,
    },
    {
        "sample_id": "synth_uhd_grain",
        "label": "UHD 3840x2160 25p 高细节（testsrc2 + 颗粒）",
        "filter": "testsrc2",
        "vf": "noise=alls=60:allf=t",
        "width": 3840,
        "height": 2160,
        "fps": 25,
    },
    {
        "sample_id": "synth_dci_24p",
        "label": "DCI 4096x2160 24p 标准（testsrc2）",
        "filter": "testsrc2",
        "vf": None,
        "width": 4096,
        "height": 2160,
        "fps": 24,
    },
    {
        "sample_id": "synth_uhd_50p",
        "label": "UHD 3840x2160 50p 高帧率（testsrc2）",
        "filter": "testsrc2",
        "vf": None,
        "width": 3840,
        "height": 2160,
        "fps": 50,
    },
)

# FFmpeg 9 把 bench 拆成两行：先 utime/stime/rtime，再 maxrss。两种写法都要兼容。
_BENCH_TIME_RE = re.compile(
    r"bench: utime=(?P<utime>[\d.]+)s stime=(?P<stime>[\d.]+)s(?: rtime=(?P<rtime>[\d.]+)s)?"
)
_BENCH_RSS_RE = re.compile(r"bench: maxrss=(?P<maxrss>\d+)(?P<unit>KiB|KB|MiB|MB)")


@dataclass
class Measurement:
    """单次编码的实测记录。"""

    sample_id: str
    label: str
    source_kind: str
    profile: str
    width: int
    height: int
    fps: float
    duration_sec: float
    encode_sec: float
    speed_x: float
    output_bytes: int
    bitrate_mbps: float
    cpu_cores_avg: float
    cpu_time_sec: float
    peak_rss_mb: float
    gpu_util_peak: float | None = None
    gpu_mem_peak_mb: float | None = None
    source_sha256_before: str | None = None
    source_sha256_after: str | None = None
    source_unchanged: bool | None = None
    notes: str = ""
    extra: dict[str, object] = field(default_factory=dict)


def _require_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise SystemExit(f"缺少可执行文件：{name}，请先安装 FFmpeg 并加入 PATH")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _probe(ffprobe: str, path: Path) -> dict[str, object]:
    """读取容器与首条视频流的关键参数，用于核对代理是否保持分辨率与帧率。"""
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,profile,width,height,r_frame_rate,pix_fmt:format=duration,size",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return {"error": completed.stderr.strip()[:400]}
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return {"error": "ffprobe 输出无法解析"}
    stream = (payload.get("streams") or [{}])[0]
    fmt = payload.get("format") or {}
    return {
        "codec_name": stream.get("codec_name"),
        "profile": stream.get("profile"),
        "width": stream.get("width"),
        "height": stream.get("height"),
        "r_frame_rate": stream.get("r_frame_rate"),
        "pix_fmt": stream.get("pix_fmt"),
        "duration": fmt.get("duration"),
        "size": fmt.get("size"),
    }


def _parse_rate(raw: object) -> float:
    """把 ffprobe 的 `30000/1001` 形式帧率转成浮点。"""
    if not raw:
        return 0.0
    text = str(raw)
    if "/" in text:
        numerator, _, denominator = text.partition("/")
        try:
            return round(float(numerator) / float(denominator), 3)
        except (ValueError, ZeroDivisionError):
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


class _GpuSampler:
    """可选：编码期间轮询 nvidia-smi，记录 GPU 利用率与显存峰值。

    非 NVIDIA 机器或未安装 nvidia-smi 时自动退化为「无 GPU 采样」，不报错。
    """

    def __init__(self, interval: float = 0.25) -> None:
        self.interval = interval
        self.samples: list[tuple[float, float]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._available = shutil.which("nvidia-smi") is not None

    def __enter__(self) -> _GpuSampler:
        if self._available:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                completed = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=utilization.gpu,memory.used",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=3,
                )
                if completed.returncode == 0 and completed.stdout.strip():
                    first = completed.stdout.strip().splitlines()[0]
                    util_str, mem_str = (part.strip() for part in first.split(",")[:2])
                    self.samples.append((float(util_str), float(mem_str)))
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            self._stop.wait(self.interval)

    @property
    def peak(self) -> tuple[float | None, float | None]:
        if not self.samples:
            return None, None
        return max(item[0] for item in self.samples), max(
            item[1] for item in self.samples
        )


def _build_synthetic_source(
    ffmpeg: str, sample: dict[str, object], duration: float, source_dir: Path
) -> Path:
    """生成 4K 中间源（x264 ultrafast）。此步不计入编码量测。"""
    target = source_dir / f"{sample['sample_id']}_src.mp4"
    command = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"{sample['filter']}=size={sample['width']}x{sample['height']}"
        f":rate={sample['fps']}",
        "-t",
        str(duration),
    ]
    if sample.get("vf"):
        command += ["-vf", str(sample["vf"])]
    command += [
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "18",
        "-an",
        str(target),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise SystemExit(
            f"合成源生成失败（{sample['sample_id']}）：{completed.stderr.strip()[-600:]}"
        )
    return target


def _parse_bench(stderr: str) -> tuple[float, float, float, float]:
    """解析 FFmpeg bench 输出，返回 (utime, stime, rtime, maxrss_kb)。"""
    utime = stime = rtime = maxrss_kb = 0.0
    time_match = _BENCH_TIME_RE.search(stderr)
    if time_match:
        utime = float(time_match.group("utime"))
        stime = float(time_match.group("stime"))
        if time_match.group("rtime"):
            rtime = float(time_match.group("rtime"))
    rss_match = _BENCH_RSS_RE.search(stderr)
    if rss_match:
        value = float(rss_match.group("maxrss"))
        unit = rss_match.group("unit")
        maxrss_kb = value if unit in {"KiB", "KB"} else value * 1024
    return utime, stime, rtime, maxrss_kb


def _encode(
    ffmpeg: str,
    ffprobe: str,
    sample: dict[str, object],
    profile: str,
    duration: float,
    out_dir: Path,
    source: Path,
    source_kind: str,
) -> Measurement:
    sample_id = str(sample["sample_id"])
    target = out_dir / f"{sample_id}_{profile}.mov"
    sha_before = _sha256(source) if source_kind == "real" else None

    # 真实素材可能短于请求时长：按「请求时长」与「素材实际时长」取小值，
    # 否则 -t 会被截断而成片时长仍按请求值计，倍速与码率都会失真。
    effective_duration = duration
    source_duration = float(sample.get("duration") or 0.0)
    if source_kind == "real" and source_duration > 0:
        effective_duration = min(duration, source_duration)

    command = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-t",
        f"{effective_duration:.3f}",
        "-c:v",
        "prores_ks",
        "-profile:v",
        str(PROFILES[profile]),
        "-pix_fmt",
        "yuv422p10le",
        "-vendor",
        "apl0",
        "-an",
        "-benchmark",
        str(target),
    ]

    started = time.perf_counter()
    with _GpuSampler() as gpu:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    wall = time.perf_counter() - started
    if completed.returncode != 0:
        raise SystemExit(
            f"编码失败（{sample_id}/{profile}）：{completed.stderr.strip()[-800:]}"
        )

    utime, stime, rtime, maxrss_kb = _parse_bench(completed.stderr)
    cpu_time = utime + stime
    encode_sec = rtime if rtime > 0 else wall
    size_bytes = target.stat().st_size
    probe = _probe(ffprobe, target)
    gpu_util, gpu_mem = gpu.peak
    sha_after = _sha256(source) if source_kind == "real" else None

    notes = "" if maxrss_kb else "FFmpeg 未输出 maxrss，峰值内存缺测"

    return Measurement(
        sample_id=sample_id,
        label=str(sample["label"]),
        source_kind=source_kind,
        profile=profile,
        width=int(probe.get("width") or sample["width"]),
        height=int(probe.get("height") or sample["height"]),
        fps=float(sample["fps"]),
        duration_sec=round(effective_duration, 3),
        encode_sec=round(encode_sec, 3),
        speed_x=round(effective_duration / encode_sec, 3) if encode_sec > 0 else 0.0,
        output_bytes=size_bytes,
        # 码率用**素材时长**作分母（媒体码率）；编码耗时只用于倍速，二者不可混用。
        bitrate_mbps=(
            round(size_bytes * 8 / effective_duration / 1_000_000, 2)
            if effective_duration > 0
            else 0.0
        ),
        cpu_cores_avg=round(cpu_time / wall, 2) if wall > 0 else 0.0,
        cpu_time_sec=round(cpu_time, 2),
        peak_rss_mb=round(maxrss_kb / 1024, 1),
        gpu_util_peak=gpu_util,
        gpu_mem_peak_mb=gpu_mem,
        source_sha256_before=sha_before,
        source_sha256_after=sha_after,
        source_unchanged=(sha_before == sha_after) if source_kind == "real" else None,
        notes=notes,
        extra={
            "probe": probe,
            "wall_sec": round(wall, 3),
            "source_meta": sample.get("source_meta") or {},
        },
    )


def _render_markdown(results: list[Measurement], meta: dict[str, object]) -> str:
    lines = [
        "# 4K ProRes 代理生成实测报告（QIN-444 / #389）",
        "",
        "| 项 | 值 |",
        "| :--- | :--- |",
    ]
    for key in ("生成时间", "主机", "CPU", "GPU", "FFmpeg", "样本时长", "编码档"):
        lines.append(f"| {key} | {meta.get(key, '-')} |")
    lines += [
        "",
        "## 逐样本结果",
        "",
        "| 样本 | 类型 | 编码档 | 分辨率 | 帧率 | 耗时(s) | 倍速(x) | 体积(MB) "
        "| 体积(GB/小时) | 码率(Mbps) | 平均占用核 | 峰值内存(MB) | GPU峰值(%) |",
        "| :--- | :--- | :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: "
        "| ---: | ---: | ---: |",
    ]
    for item in results:
        gpu = "-" if item.gpu_util_peak is None else f"{item.gpu_util_peak:.0f}"
        kind = "合成" if item.source_kind == "synthetic" else "真实"
        gb_per_hour = item.output_bytes * (3600 / item.duration_sec) / 1e9
        lines.append(
            f"| {item.sample_id} | {kind} | {PROFILE_LABELS[item.profile]} "
            f"| {item.width}x{item.height} | {item.fps:g} | {item.encode_sec:.2f} "
            f"| {item.speed_x:.2f} | {item.output_bytes / (1024 * 1024):.1f} "
            f"| {gb_per_hour:.1f} | {item.bitrate_mbps:.1f} "
            f"| {item.cpu_cores_avg:.2f} | {item.peak_rss_mb:.0f} | {gpu} |"
        )
    lines.append("")

    real: dict[str, Measurement] = {}
    for item in results:
        if item.source_kind == "real":
            real.setdefault(item.sample_id, item)
    if real:
        lines += [
            "## 真实素材完整性（AC2）",
            "",
            "| 素材 | 编码前 SHA-256 | 编码后 SHA-256 | 未被改动 |",
            "| :--- | :--- | :--- | :--- |",
        ]
        for item in real.values():
            lines.append(
                f"| {item.sample_id} | {(item.source_sha256_before or '')[:16]}… "
                f"| {(item.source_sha256_after or '')[:16]}… "
                f"| {'是' if item.source_unchanged else '否'} |"
            )
        lines.append("")

    for profile in PROFILES:
        subset = [item for item in results if item.profile == profile]
        if not subset:
            continue
        speeds = [item.speed_x for item in subset]
        lines.append(
            f"- {PROFILE_LABELS[profile]}：{len(subset)} 个样本，"
            f"倍速中位 {statistics.median(speeds):.2f}x，"
            f"区间 {min(speeds):.2f}x ~ {max(speeds):.2f}x。"
        )
    lines.append("")
    return "\n".join(lines)


def _host_meta(ffmpeg: str, duration: float, profiles: list[str]) -> dict[str, object]:
    gpu = "未检测到 NVIDIA GPU（ProRes 由 CPU 编码）"
    if shutil.which("nvidia-smi"):
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            gpu = completed.stdout.strip().splitlines()[0]
    version = subprocess.run(
        [ffmpeg, "-version"], capture_output=True, text=True, check=False
    ).stdout.splitlines()[0]
    return {
        "生成时间": time.strftime("%Y-%m-%d %H:%M:%S"),
        "主机": f"{os.environ.get('COMPUTERNAME', 'unknown')} / {sys.platform}",
        "CPU": f"{os.cpu_count() or 0} 逻辑核",
        "GPU": gpu,
        "FFmpeg": version,
        "样本时长": f"{duration:g}s",
        "编码档": "、".join(PROFILE_LABELS[item] for item in profiles),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="4K ProRes Proxy/LT 代理生成实测")
    parser.add_argument("--synthetic", action="store_true", help="跑内置合成 4K 样本")
    parser.add_argument(
        "--real",
        action="append",
        default=[],
        metavar="PATH",
        help="真实素材路径（只读，可重复多次）",
    )
    parser.add_argument("--out-dir", required=True, help="结果与代理样本输出目录")
    parser.add_argument(
        "--duration", type=float, default=10.0, help="每个样本截取时长（秒）"
    )
    parser.add_argument(
        "--profiles", default="proxy,lt", help="要测试的编码档，逗号分隔（proxy,lt）"
    )
    args = parser.parse_args(argv)

    if not args.synthetic and not args.real:
        parser.error("至少指定 --synthetic 或一个 --real 素材")

    profiles = [
        part.strip().lower() for part in args.profiles.split(",") if part.strip()
    ]
    unknown = [part for part in profiles if part not in PROFILES]
    if unknown:
        parser.error(f"未知编码档：{unknown}；可选 {sorted(PROFILES)}")

    ffmpeg = _require_binary("ffmpeg")
    ffprobe = _require_binary("ffprobe")

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    source_dir = out_dir / "_src"
    source_dir.mkdir(exist_ok=True)

    jobs: list[tuple[dict[str, object], Path, str]] = []
    if args.synthetic:
        for sample in SYNTHETIC_SAMPLES:
            print(f"[spike] 生成合成源 {sample['sample_id']} ...", flush=True)
            path = _build_synthetic_source(ffmpeg, sample, args.duration, source_dir)
            jobs.append((dict(sample), path, "synthetic"))
    for raw in args.real:
        source = Path(raw).expanduser()
        if not source.is_file():
            raise SystemExit(f"真实素材不存在：{raw}")
        info = _probe(ffprobe, source)
        jobs.append(
            (
                {
                    "sample_id": f"real_{source.stem}"[:60],
                    "label": f"真实素材 {source.name}",
                    "width": info.get("width") or 0,
                    "height": info.get("height") or 0,
                    "fps": _parse_rate(info.get("r_frame_rate")),
                    "duration": float(info.get("duration") or 0.0),
                    "source_meta": {
                        "codec_name": info.get("codec_name"),
                        "profile": info.get("profile"),
                        "pix_fmt": info.get("pix_fmt"),
                        "size_bytes": int(info.get("size") or 0),
                    },
                },
                source.resolve(),
                "real",
            )
        )

    results: list[Measurement] = []
    for sample, source, source_kind in jobs:
        for profile in profiles:
            print(f"[spike] {sample['sample_id']} / {profile} ...", flush=True)
            results.append(
                _encode(
                    ffmpeg,
                    ffprobe,
                    sample,
                    profile,
                    args.duration,
                    out_dir,
                    source,
                    source_kind,
                )
            )

    meta = _host_meta(ffmpeg, args.duration, profiles)
    payload = {
        "issue": 389,
        "ticket": "QIN-444",
        "generated_at": meta["生成时间"],
        "duration_sec": args.duration,
        "profiles": profiles,
        "measurements": [asdict(item) for item in results],
    }
    json_path = out_dir / "prores_spike.json"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path = out_dir / "prores_spike.md"
    md_path.write_text(_render_markdown(results, meta), encoding="utf-8")

    print(f"[spike] JSON -> {json_path}")
    print(f"[spike] 报告 -> {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
