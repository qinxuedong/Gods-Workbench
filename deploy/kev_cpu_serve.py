"""按 C17 AC5 默认线程数启动 Kev CPU 服务。"""
from __future__ import annotations

import os
import runpy
import sys
from importlib import import_module


DEFAULT_CPU_THREADS = 16

try:
    cpu_threads = int(os.environ.get("KEV_CPU_THREADS", str(DEFAULT_CPU_THREADS)))
except ValueError as exc:
    raise SystemExit("KEV_CPU_THREADS 必须是正整数") from exc

if cpu_threads < 1:
    raise SystemExit("KEV_CPU_THREADS 必须是正整数")

# 在导入 Kev/Torch 前设置环境默认，避免 CPU 推理线程池继承机器核心数。
os.environ["OMP_NUM_THREADS"] = str(cpu_threads)
os.environ["MKL_NUM_THREADS"] = str(cpu_threads)

torch = import_module("torch")

torch.set_num_threads(cpu_threads)
torch.set_num_interop_threads(cpu_threads)
print(
    "KEV_CPU_THREAD_CONFIG "
    f"intra={torch.get_num_threads()} "
    f"interop={torch.get_num_interop_threads()} "
    f"OMP_NUM_THREADS={os.environ['OMP_NUM_THREADS']} "
    f"MKL_NUM_THREADS={os.environ['MKL_NUM_THREADS']}",
    flush=True,
)

# 保留用户传给本启动器的 kev.serve 参数（--run、--device、--port 等）。
sys.argv[0] = "kev.serve"
runpy.run_module("kev.serve", run_name="__main__")
