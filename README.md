# QIN-585 独立复核用证据（PR #683）

本仓库只存放 PR #683（QIN-585）的**可读取证据**与复现脚本，便于评审/QA 不依赖作者本机。

## 结论速览

| 指标 | master（改前） | PR head（改后） |
| --- | --- | --- |
| 第一张卡片可见（禁缓存，5 次中位数） | 10195.2 ms | 1387.6 ms |
| JS 传输 | 3,842,693 B | 3,011,262 B |
| CSS 传输 | 754,164 B | 840,249 B |
| 首屏期间一次进页的收集箱 GET（早点开收集箱） | 4 | 1 |
| 首屏视觉差异（像素） | — | 0.083%（明显差异 0.019%） |

图片：

- `qin585-before.png`：改前首屏（master，1000 条素材，1600×900，禁缓存）
- `qin585-after.png`：改后首屏（PR head，同一数据集与视口）
- `qin585-diff.png`：两者像素差（`ImageChops.difference`）
- `qin585-side.png`：上=改前，下=改后

## 复现步骤

1. 起一个隔离实例（测试态、独立数据目录、独立端口）：

   ```bash
   GW_RUNTIME_MODE=test \
   GW_DATA_DIR=<repo>/.local/runtime-dev/qin585/data \
   GW_LOCAL_AUTH_DB=<repo>/.local/runtime-dev/qin585/auth/auth.sqlite3 \
   GW_VIDEO_DATA_DIR=<repo>/.local/runtime-dev/qin585/video \
   py -3.11 run.py --test --port 2097
   ```

2. 建本地账号并拿到会话 cookie：

   ```bash
   curl --noproxy '*' -c cookies.txt -H 'Origin: http://127.0.0.1:2097' \
     -H 'Content-Type: application/json' \
     -d '{"username":"qa585","password":"Gw-QA585-pass!"}' \
     http://127.0.0.1:2097/api/asset-auth/local/setup
   ```

3. 导入 1000 条素材（`import.json` 是 `/api/asset-registry/assets/import` 的完整载荷）：

   ```bash
   curl --noproxy '*' -b cookies.txt -H 'Origin: http://127.0.0.1:2097' \
     -H 'Content-Type: application/json' -X POST --data-binary @import.json \
     http://127.0.0.1:2097/api/asset-registry/assets/import
   ```

4. 首卡时间与体积：`py -3.11 measure.py <标签> <gw_session 值> 5`（产物 `<标签>.json` + `<标签>-<n>.png`）。

5. 收集箱并发读取：`py -3.11 probe_inbox.py <标签>`。它会在首屏渲染后**尽早**点开收集箱，
   统计一次进页里 `/api/asset-inbox?` 的 GET 次数（`inbox-before.json` / `inbox-after.json` 是两次实测）。

6. 延后链路可用性与时序：`py -3.11 verify_deferred.py <gw_session 值>`，
   输出治理抽屉正文长度与「列表 responseEnd → 收集箱/治理建议发起」的相对时序。

## 备注

- 收集箱未读角标由内嵌页向外壳推送快照渲染，时机不稳定，探针只把它作为旁证记录，
  不作判据；「清零后不被晚到旧响应改回」由 `tests/frontend/qin-585-asset-first-paint.test.mjs`
  的行为级用例（真实函数 + 假 HTTP）覆盖。
- `baseline.json` / `r2.json` 是两次 `measure.py` 的完整产物（含每次 run 的资源明细）。
