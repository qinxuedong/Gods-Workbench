# R4 I2V 控时长生成契约

- `contract_id`: `R4-I2V-GENERATION-CONTRACT`
- `status`: `frozen_for_implementation`
- `owner`: C14-T1 / QIN-216
- `updated_at`: 2026-10-20
- `acceptance`: C14-AC1

## 1. 边界

本契约定义从已锁定的分镜时长到 Image-to-Video（I2V）执行器的稳定请求，以及生成媒体回收后的纯函数时序适配结果。它不启动 FFmpeg、不访问供应商、不读取本地媒体、不保存 API Key；真实媒体时长由 C14-T2/C14-T4 的执行与探测证据提供。

C13 已经把对白锁定为 `target_duration`。C14-T1 只保证这个值在请求、变速因子和媒体片段终点之间保持同一来源，避免用未经确认的默认时长替代对白时长。

## 2. I2V 请求

请求必须是 JSON 对象：

```json
{
  "operation": "i2v",
  "shot_id": "SC01_SH001",
  "image_asset_id": "asset-storyboard-001",
  "target_duration": 4.2,
  "prompt": "角色走向窗边",
  "camera_ctrl": {
    "trajectory": [
      {"at": 0.0, "x": 0.0, "y": 0.0, "z": 0.0},
      {"at": 1.0, "x": 0.2, "y": 0.0, "z": 0.0}
    ]
  },
  "lora": [{"name": "character-main", "weight": 0.75}],
  "fixture_id": "c14-t1-duration-fitting-001"
}
```

| 字段 | 必需 | 约束 |
|---|---:|---|
| `operation` | 是 | 固定为 `i2v` |
| `shot_id` | 是 | 非空镜头稳定 ID |
| `image_asset_id` | 是 | 非空源分镜资产 ID |
| `target_duration` | 是 | 有限数字，`0.1 <= target_duration <= 120` 秒；唯一时长真源 |
| `prompt` | 是 | 非空字符串；不在客户端拼接凭据 |
| `camera_ctrl` | 否 | 对象；`trajectory` 为按 `at` 单调递增的相机轨迹点 |
| `lora` | 否 | 对象数组；每项有 `name` 或 `model` 与有限 `weight` |
| `fixture_id` | 否 | 测试夹具或证据索引，不携带密钥 |

`camera_ctrl` 与 `lora` 只描述生成参数，服务端执行器仍须按项目权限和供应商准入规则处理；本契约不授权客户端携带商业 API Key。

## 3. 时长适配公式

给定真实生成媒体时长 `source_duration > 0` 与请求的 `target_duration`：

```text
speed_ratio  = target_duration / source_duration
setpts_factor = speed_ratio
playback_rate = 1 / speed_ratio = source_duration / target_duration
fitted_duration = source_duration * speed_ratio
error_seconds = abs(fitted_duration - target_duration)
```

- `speed_ratio` 是 FFmpeg `setpts` 的时间缩放因子：小于 1 表示加速，大于 1 表示减速。
- `playback_rate` 是给播放器或宿主使用的速率倍数，与 `speed_ratio` 互为倒数。
- `fitted_duration` 在浮点规范化后应等于 `target_duration`；源文件不被覆盖。
- 适配计划的剪辑窗口固定从 0 开始，结束点使用 `target_duration`。

媒体 URL 的片段标记统一为：

```text
<源媒体地址>#t=0,<target_duration>
```

已有 fragment 会被替换，查询字符串保持不变；例如 `/media/take.mp4#old` 变为 `/media/take.mp4#t=0,4.2`。

## 4. 真实时长验收

回收媒体后由探测层提供 `actual_duration`，调用时长匹配校验：

```json
{
  "actual_duration": 4.18,
  "target_duration": 4.2,
  "error_seconds": 0.02,
  "tolerance_seconds": 0.1,
  "within_tolerance": true
}
```

默认容差为 `0.1` 秒。`error_seconds > tolerance_seconds` 时以 HTTP 语义 `422`、错误码 `DURATION_TOLERANCE_EXCEEDED` 失败关闭，不得把估算值写成真实验收证据。

## 5. 错误与安全边界

| 错误码 | 触发条件 |
|---|---|
| `INVALID_TARGET_DURATION` | 目标时长不是有限数字、未达到最小值或超过 120 秒 |
| `INVALID_SOURCE_DURATION` | 探测时长不是正的有限数字 |
| `INVALID_DURATION_TOLERANCE` | 容差不是有限数字或为负数 |
| `DURATION_TOLERANCE_EXCEEDED` | 实际时长误差超过容差 |
| `INVALID_CAMERA_CTRL` | 相机轨迹不是对象数组、时间点不单调或坐标不是有限数字 |
| `INVALID_LORA` | LoRA 项缺少模型名或权重不是有限数字 |
| `INVALID_MEDIA_URL` | 媒体地址不是字符串 |

本模块只返回可审计的 JSON 计算结果。任何真实供应商调用、媒体落盘、资产登记、任务轮询、取消和外部凭据代理均属于 C14-T2 或后续任务，不在本工单内隐式实现。

## 6. 证据夹具

- `fixture_id`: `c14-t1-duration-fitting-001`
- 定向测试：`tests/contracts/media/test_c14_duration_fitting.py`
- 验收门槛：实际媒体时长与 `target_duration` 的误差不超过 `0.1` 秒。
