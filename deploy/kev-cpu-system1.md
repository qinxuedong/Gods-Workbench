# System 1 的 Kev CPU 服务线程默认

C17 AC5 的当前纯 CPU 参考路径是 System 1 → Kev。2026-10-23 在线程扫参中，Torch intra-op/inter-op 均为 16 的档位取得最佳结果：N=200，P50 150.173ms、P95 163.259ms。原始环境、命令和限制见 `.local/qa/C17-evidence/c17-ac5-cpu-thread-sweep-20261023.md`。

`deploy/kev_cpu_serve.py` 在导入 `kev.serve` 前将 Torch intra-op/inter-op 与 `OMP_NUM_THREADS`、`MKL_NUM_THREADS` 设为 16。它必须用安装 Kev 与 Torch 的 Python 环境运行；Kev 是独立进程，System 1 容器里的 OMP/MKL 参数只作用于网关，不能替代此设置。

Windows PowerShell 启动示例（将两个路径替换为本机实际路径）：

```powershell
$KevPython = 'D:\path\to\kev\.venv\Scripts\python.exe'
$Launcher = 'D:\path\to\Gods-Workbench\deploy\kev_cpu_serve.py'
& $KevPython $Launcher --run 'jaredpalmer/kev-0.8b@9a45d25e' --device cpu --port 8009
```

启动日志会打印实际 intra-op、inter-op、OMP 与 MKL 值。可通过 `KEV_CPU_THREADS` 覆盖默认值；AC5 扫参证据只对应值为 16 的配置。使用 `kev.serve` 原生入口不会应用本启动器的设置。现有进程需要重启后才会采用新默认；本配置本身不代表新的延迟测量或性能结论。
