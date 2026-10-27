"""Run a resumable asset index job with optional rate and night-window controls."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, time as day_time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

API_PATH = "/api/asset-registry"
TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}
RETRYABLE = {"failed", "cancelled", "interrupted"}


class IngestError(RuntimeError):
    """An API or command-line operation failed with a user-facing message."""


def parse_night_window(value: str | None) -> tuple[int, int] | None:
    """Parse HH:MM-HH:MM into minute-of-day bounds."""
    if value is None:
        return None
    start, separator, end = value.partition("-")
    if not separator or "-" in end:
        raise argparse.ArgumentTypeError("夜间窗口格式须为 HH:MM-HH:MM")
    if any(not re.fullmatch(r"\d{2}:\d{2}", value) for value in (start, end)):
        raise argparse.ArgumentTypeError("夜间窗口格式须为 HH:MM-HH:MM")
    try:
        start_time = day_time.fromisoformat(start)
        end_time = day_time.fromisoformat(end)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("夜间窗口格式须为 HH:MM-HH:MM") from exc
    if start_time.second or start_time.microsecond or end_time.second or end_time.microsecond:
        raise argparse.ArgumentTypeError("夜间窗口只接受 HH:MM")
    start_minute = start_time.hour * 60 + start_time.minute
    end_minute = end_time.hour * 60 + end_time.minute
    if start_minute == end_minute:
        raise argparse.ArgumentTypeError("夜间窗口起止时间不能相同")
    return start_minute, end_minute


def window_is_open(now: datetime, window: tuple[int, int] | None) -> bool:
    """Return whether a local wall-clock time falls within a possibly overnight window."""
    if window is None:
        return True
    start, end = window
    minute = now.hour * 60 + now.minute
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def seconds_until_window(now: datetime, window: tuple[int, int]) -> float:
    """Return seconds until the next start of a closed window."""
    if window_is_open(now, window):
        return 0.0
    start, _ = window
    next_start = now.replace(
        hour=start // 60,
        minute=start % 60,
        second=0,
        microsecond=0,
    )
    if next_start <= now:
        next_start += timedelta(days=1)
    return max(0.0, (next_start - now).total_seconds())


def rate_wait_seconds(scanned: int, files_per_minute: int, elapsed: float) -> float:
    """Return the pacing delay needed to keep observed scan progress on budget."""
    if scanned <= 0:
        return 0.0
    target_elapsed = scanned * 60.0 / files_per_minute
    return max(0.0, target_elapsed - elapsed)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def make_report(
    job_id: str,
    job: dict,
    *,
    rate_limit: int | None,
    night_window: str | None,
) -> dict:
    """Build a compact throughput and outcome report without source paths."""
    created = _parse_timestamp(job.get("created_at"))
    updated = _parse_timestamp(job.get("updated_at")) or datetime.now(UTC)
    elapsed = max(0.0, (updated - created).total_seconds()) if created else 0.0
    progress = job.get("progress") or {}
    result = job.get("result") or {}
    scanned = int(result.get("scanned", progress.get("scanned", job.get("scanned", 0))))
    elapsed_for_rate = max(elapsed, 0.001)
    return {
        "job_id": job_id,
        "status": job.get("status", "unknown"),
        "attempt": job.get("attempt"),
        "scanned": scanned,
        "indexed": result.get("indexed"),
        "updated": result.get("updated"),
        "error_count": int(progress.get("error_count", job.get("error_count", 0))),
        "error_code": job.get("error_code"),
        "elapsed_seconds": round(elapsed, 3),
        "throughput_files_per_minute": round(scanned * 60.0 / elapsed_for_rate, 3),
        "rate_limit_files_per_minute": rate_limit,
        "night_window": night_window,
    }


def _api_request(
    base_url: str,
    token: str,
    role: str,
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    idempotency_key: str | None = None,
) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "X-User-Role": role,
        "Accept": "application/json",
    }
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=body,
        headers=headers,
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
        except (UnicodeDecodeError, json.JSONDecodeError):
            detail = ""
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("code") or ""
        message = f"HTTP {exc.code}"
        if detail:
            message += f": {detail}"
        raise IngestError(message) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise IngestError(f"索引 API 请求失败：{exc}") from exc
    if not isinstance(result, dict):
        raise IngestError("索引 API 返回了无效响应")
    return result


def _get_job(args, job_id: str) -> dict:
    return _api_request(
        args.base_url,
        args.token,
        args.role,
        "GET",
        f"{API_PATH}/workspace-jobs/{job_id}",
    )


def _operate(args, job: dict, action: str) -> dict:
    job_id = str(job["job_id"])
    return _api_request(
        args.base_url,
        args.token,
        args.role,
        "POST",
        f"{API_PATH}/workspace-jobs/{job_id}/{action}",
        payload={"expected_version": int(job["version"])},
    )


def _local_now(timezone: ZoneInfo | None) -> datetime:
    return datetime.now(timezone) if timezone is not None else datetime.now().astimezone()


def _wait_for_window(args, window: tuple[int, int] | None, timezone: ZoneInfo | None) -> None:
    while window is not None and not window_is_open(_local_now(timezone), window):
        if not args.wait_for_window:
            raise IngestError("夜间窗口尚未开启；传入 --wait-for-window 可等待窗口开启")
        time.sleep(min(60.0, seconds_until_window(_local_now(timezone), window)))


def run_job(args) -> dict:
    window = parse_night_window(args.night_window)
    timezone = None
    if args.timezone:
        try:
            timezone = ZoneInfo(args.timezone)
        except ZoneInfoNotFoundError as exc:
            raise IngestError(f"未知时区：{args.timezone}") from exc

    job_id = args.job_id
    if job_id:
        detail = _get_job(args, job_id)
        job = detail["job"]
        if job["status"] == "succeeded":
            return make_report(
                job_id,
                job,
                rate_limit=args.rate_limit,
                night_window=args.night_window,
            )
        if job["status"] in RETRYABLE:
            _wait_for_window(args, window, timezone)
            detail = _operate(args, job, "retry")
            job = detail["job"]
        elif job["status"] == "paused" and window_is_open(_local_now(timezone), window):
            detail = _operate(args, job, "resume")
            job = detail["job"]
    else:
        _wait_for_window(args, window, timezone)
        key = args.idempotency_key or uuid.uuid4().hex
        payload = {"background": True, "hash_files": True}
        if args.roots:
            payload["roots"] = args.roots
        detail = _api_request(
            args.base_url,
            args.token,
            args.role,
            "POST",
            f"{API_PATH}/reindex",
            payload=payload,
            idempotency_key=key,
        )
        job_id = str(detail["job_id"])
        job = detail["job"]
        print(f"索引任务：{job_id}", file=sys.stderr)

    pacing_started = time.monotonic()
    pacing_base = int((job.get("progress") or {}).get("scanned", job.get("scanned", 0)))
    last_detail = detail
    while True:
        last_detail = _get_job(args, job_id)
        job = last_detail["job"]
        status = str(job.get("status") or "unknown")
        if status in TERMINAL:
            return make_report(
                job_id,
                job,
                rate_limit=args.rate_limit,
                night_window=args.night_window,
            )

        now = _local_now(timezone)
        window_open = window_is_open(now, window)
        progress = job.get("progress") or {}
        scanned = int(progress.get("scanned", job.get("scanned", 0)))
        rate_wait = (
            rate_wait_seconds(
                max(0, scanned - pacing_base),
                args.rate_limit,
                time.monotonic() - pacing_started,
            )
            if args.rate_limit
            else 0.0
        )
        constrained = not window_open or rate_wait > 0

        if status in {"queued", "running"} and constrained:
            if last_detail.get("actions", {}).get("pause"):
                last_detail = _operate(args, job, "pause")
            if not window_open and window is not None:
                delay = min(60.0, seconds_until_window(now, window))
            else:
                delay = max(0.05, min(args.poll_interval, rate_wait))
            time.sleep(delay)
            continue

        if status == "paused" and not constrained:
            if last_detail.get("actions", {}).get("resume"):
                last_detail = _operate(args, job, "resume")
                pacing_started = time.monotonic()
                pacing_base = scanned
            else:
                time.sleep(args.poll_interval)
            continue

        if status == "paused" and constrained:
            if not window_open and window is not None:
                delay = min(60.0, seconds_until_window(now, window))
            else:
                delay = max(0.05, min(args.poll_interval, rate_wait))
            time.sleep(delay)
            continue

        time.sleep(args.poll_interval)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="在已登记素材来源上执行可恢复的后台索引")
    parser.add_argument("--base-url", default=os.environ.get("GW_ASSET_INDEX_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--token", default=os.environ.get("GW_ASSET_INDEX_TOKEN"))
    parser.add_argument("--role", default="editor", choices=("admin", "governor", "editor"))
    parser.add_argument("--root", dest="roots", action="append", default=[])
    parser.add_argument("--job-id", help="继续已有任务；中断或失败任务会按幂等语义重试")
    parser.add_argument("--idempotency-key")
    parser.add_argument("--rate-limit", type=int, help="目标吞吐，单位为文件/分钟")
    parser.add_argument("--night-window", help="允许执行的本地时间窗口，例如 22:00-06:00")
    parser.add_argument("--timezone", help="IANA 时区；省略时使用本机时区")
    parser.add_argument("--wait-for-window", action="store_true")
    parser.add_argument("--poll-interval", type=float, default=0.5)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.token:
        parser.error("请通过 --token 或 GW_ASSET_INDEX_TOKEN 提供 Bearer token")
    if args.rate_limit is not None and args.rate_limit < 1:
        parser.error("--rate-limit 必须为正整数")
    if args.poll_interval <= 0:
        parser.error("--poll-interval 必须大于 0")
    try:
        report = run_job(args)
    except KeyboardInterrupt:
        print("索引命令已中断；可使用 --job-id 继续任务。", file=sys.stderr)
        return 130
    except (IngestError, argparse.ArgumentTypeError) as exc:
        print(f"索引失败：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
