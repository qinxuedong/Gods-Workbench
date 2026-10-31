# syntax=docker/dockerfile:1
# Gods-Workbench NAS 单进程运行镜像。

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GW_RUNTIME_MODE=prod \
    GW_AUTH_MODE=oidc \
    GW_HOST=0.0.0.0 \
    GW_PORT=2077 \
    LOCALAPPDATA=/var/lib/gods-workbench

WORKDIR /app

# 生产镜像只安装仓库已核验的哈希锁依赖，不把宿主机环境带入容器。
COPY requirements.lock.hashes /tmp/requirements.lock.hashes
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock.hashes \
    && rm -f /tmp/requirements.lock.hashes

# 显式复制运行时所需内容，避免把 .git、.local 或宿主数据复制进镜像。
COPY gw ./gw
COPY tools ./tools
COPY web ./web
COPY run.py ./run.py
COPY LICENSE ./LICENSE

# NAS 上以可配置的非 root UID/GID 运行；源码需可读、目录需可遍历。
RUN chmod -R a+rX /app

CMD ["python", "run.py", "--prod"]
