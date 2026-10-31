# 通用 NAS Docker 部署

本指南适用于运行 Docker Engine 和 Docker Compose V2 的 NAS。飞牛、群晖、QNAP、TrueNAS SCALE、Unraid 等系统的目录布局、账户管理和 Compose 图形界面各不相同；本指南使用通用 Compose 配置，不假设固定盘符、共享目录或 UID/GID。

当前部署方式从仓库源码构建。官方预构建镜像尚未发布；镜像发布作为独立阶段，计划跟随正式版本发布，不在每次 PR 合并后推送。

主应用只使用 `docker-compose.nas.yml`。`docker-compose.system1.yml` 是单独的可选 CPU 推理服务，不是启动主应用的前置条件；主应用默认使用宿主端口 2077，System1 默认使用 2078，修改端口时确保两者不冲突。

## 准备环境

1. 在 NAS 上安装 Docker Engine 与 Compose V2，并确认处理器架构和锁定依赖兼容。
2. 将完整仓库放在 NAS 持久化目录中。Compose 构建上下文依赖仓库根目录，单独复制 YAML 文件无法构建。
3. 在 NAS 上为应用创建三个独立持久化目录：数据、认证和视频。选择能供 Docker Engine 访问的绝对路径。
4. 选择运行容器的 NAS 用户，以该用户登录 NAS 终端并执行 `id` 获取数字 UID/GID。不要默认照抄示例值。

复制配置模板并编辑：

```sh
cp deploy/docker/.env.example deploy/docker/.env
```

在 `deploy/docker/.env` 中设置 NAS 用户的 `GW_UID`、`GW_GID`、三个数据目录的绝对路径、访问端口，以及浏览器访问 NAS 时使用的 IP 或 DNS 主机名。`GW_ALLOWED_HOSTS` 只填写主机名或 IP，不带 `http://`、端口或网段；多个地址用逗号分隔。建议在路由器中为 NAS 保留 DHCP 地址，或使用固定的局域网 DNS 名称。后续通过 `.env` 配置，不要直接修改 Compose 或 Dockerfile。

### 检查目录权限

三个目录必须允许上面配置的 UID/GID 读写。可在 NAS 文件管理界面设置目录属主和权限，或在 NAS 管理员终端操作。对**新建且为空**的目录，可使用：

```sh
set -a
. deploy/docker/.env
set +a
mkdir -p "$GW_DATA_DIR" "$GW_AUTH_DIR" "$GW_VIDEO_DIR"
chown "$GW_UID:$GW_GID" "$GW_DATA_DIR" "$GW_AUTH_DIR" "$GW_VIDEO_DIR"
chmod 700 "$GW_DATA_DIR" "$GW_AUTH_DIR" "$GW_VIDEO_DIR"
```

已有数据目录先检查属主、权限和备份，再按 NAS 管理方式修正；如果目录内旧文件属于其他 UID/GID，只对这三个应用目录及其内容修正，不要对整个共享盘盲目递归改属主或权限。

## 校验并启动

从仓库根目录运行。`-p` 指定 Compose 项目名 `gods-workbench`；服务名是 `app`，Docker 自动生成的容器名为 `gods-workbench-app-1`。`gods-workbench:local` 是 NAS 本地构建标签，不是可拉取的官方镜像：

```sh
docker compose -p gods-workbench --env-file deploy/docker/.env -f deploy/docker/docker-compose.nas.yml config --quiet
docker compose -p gods-workbench --env-file deploy/docker/.env -f deploy/docker/docker-compose.nas.yml up -d --build
```

如果 NAS 厂商的 Compose 管理界面支持从完整仓库目录导入项目，可选择 `deploy/docker/docker-compose.nas.yml`，并将项目名设为 `gods-workbench`、导入相同的 `.env` 变量。若界面不能访问仓库根目录的构建上下文，改用 NAS 的 Compose V2 命令行。

容器以配置的非 root UID/GID 运行；镜像中的应用文件提供读取与目录遍历权限，持久化目录仍由 NAS 用户控制。

## 创建管理员并登录

服务健康后，在 NAS 终端或容器控制台执行：

```sh
docker compose -p gods-workbench --env-file deploy/docker/.env -f deploy/docker/docker-compose.nas.yml exec -it app python -m gw.core.local_accounts_cli setup-admin
```

按提示输入账号和密码。命令仅能创建首位本地管理员；已有管理员时会拒绝重复创建。成功后立即保存终端显示的一次性恢复码，不要将恢复码贴到工单或聊天中。然后在局域网浏览器访问 `http://<NAS主机名或IP>:<NAS端口>` 并登录。

首次管理员初始化保留本机限制，不会因为开启局域网访问而放宽。`GW_ALLOWED_HOSTS` 校验的是用户访问时所用的 NAS 地址；局域网客户端都访问同一个 NAS 地址即可。`local_account` 仍要求使用账户登录。

## 检查状态与日志

```sh
docker compose -p gods-workbench --env-file deploy/docker/.env -f deploy/docker/docker-compose.nas.yml ps
docker compose -p gods-workbench --env-file deploy/docker/.env -f deploy/docker/docker-compose.nas.yml logs --tail=100 app
```

健康检查状态为 `healthy` 后，可从 NAS 本机访问 `http://127.0.0.1:<NAS端口>/healthz`。如果配置了自定义端口，请使用 `.env` 中的 `GW_HOST_PORT`。

常见问题：

- `permission denied`：核对容器 UID/GID 与三个 NAS 目录的权限；不要通过改成 root 来绕过。
- `Invalid host header`：`GW_ALLOWED_HOSTS` 必须包含浏览器 URL 中实际使用的 NAS 主机名或 IP，且不能包含协议和端口。
- 管理员创建返回 `LOCAL_SETUP_ONLY`：确认执行的是上面的容器控制台命令，不要从 LAN 首次设置页面创建。
- 容器重启：先看 `logs` 输出；修改 Compose 或 `.env` 后执行 `up -d --force-recreate`。

## 备份与升级

升级前备份 `.env` 指向的三个宿主目录，或使用 NAS 自带的快照/备份功能。更新仓库代码到目标版本后，在仓库根目录执行：

```sh
git pull --ff-only
docker compose -p gods-workbench --env-file deploy/docker/.env -f deploy/docker/docker-compose.nas.yml build --pull app
docker compose -p gods-workbench --env-file deploy/docker/.env -f deploy/docker/docker-compose.nas.yml up -d --force-recreate app
docker compose -p gods-workbench --env-file deploy/docker/.env -f deploy/docker/docker-compose.nas.yml ps
```

当前没有可供 `docker compose pull` 使用的官方应用镜像。后续正式版本镜像发布后，将按版本标签提供拉取与回滚流程。不要使用 `docker compose down -v`；它可能删除容器管理的数据卷。回滚源码构建可恢复到先前代码版本并重新构建；只有确认需要时才从备份恢复数据。

## 可选：部署 System1 CPU 决策服务

System1 是独立的可选 Compose 服务，不会替代主应用，也不是主应用启动前置。容器内仍监听 2077，宿主默认映射到 2078；与主应用一起运行时两者不会抢同一个宿主端口。该镜像在仓库源码上本地构建，不会下载模型或 Kev 推理服务。

从仓库根目录复制单独的配置模板：

~~~sh
cp deploy/docker/.env.system1.example deploy/docker/.env.system1
~~~

编辑 deploy/docker/.env.system1：

- 将 SYSTEM1_PORT 保持为 2078，或改成未占用的 NAS 宿主端口。
- 将 GW_ALLOWED_HOSTS 改为浏览器或调用方访问 NAS 时实际使用的 IP/DNS 主机名，精确填写，不带协议和端口；多个主机用逗号分隔，不用通配符。
- 将 KEV_ENDPOINT 改成容器可访问的 Kev System One 推理 API 地址。示例中的 kev.example.invalid 是保留的无效域名；不要直接使用它。若 Kev 服务不在同一容器，不能把 127.0.0.1 当成宿主机或另一容器地址。

docker-compose.system1.yml 使用独立的 Docker 管理卷 gods-workbench-system1-models，并以只读方式挂载到 /models/kev。Compose 不会下载或填充模型。使用前按获准的模型交付方式预置所需的 Kev-0.8B revision 9a45d25e，并确保 Kev 推理 API 已启动且能从 System1 容器访问；此 Compose 只启动 System1 接口，不包含 Kev 模型推理服务本身。

校验、构建并启动：

~~~sh
docker compose -p gods-workbench-system1 --env-file deploy/docker/.env.system1 -f deploy/docker/docker-compose.system1.yml config --quiet
docker compose -p gods-workbench-system1 --env-file deploy/docker/.env.system1 -f deploy/docker/docker-compose.system1.yml up -d --build
docker compose -p gods-workbench-system1 --env-file deploy/docker/.env.system1 -f deploy/docker/docker-compose.system1.yml ps
docker compose -p gods-workbench-system1 --env-file deploy/docker/.env.system1 -f deploy/docker/docker-compose.system1.yml logs --tail=100 system1
~~~

健康检查使用容器内 127.0.0.1:2077。NAS 本机可检查 http://127.0.0.1:2078/healthz；局域网调用地址为 http://<NAS主机名或IP>:2078/v1/systemone。若更改 SYSTEM1_PORT，请同步替换宿主侧端口。防火墙需允许所选端口；GW_ALLOWED_HOSTS 必须与 URL 中的主机一致，否则服务返回 Invalid host header。

停止但保留模型和运行数据卷：

~~~sh
docker compose -p gods-workbench-system1 --env-file deploy/docker/.env.system1 -f deploy/docker/docker-compose.system1.yml down
~~~

不要加 -v；这会删除 Docker 管理卷。主应用继续单独使用 docker-compose.nas.yml 与宿主默认端口 2077。
