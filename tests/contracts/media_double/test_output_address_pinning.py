"""产物下载连接层地址判定与钉住：Clash fake-ip 白名单与 DNS 重绑定防护。

助手与后端单元测试全部走纯代码与替身（伪造 getaddrinfo / 内层后端），
端到端回收走真实 _output_transport，仅替换 DNS 与套接字层，不访问网络。
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket

import pytest

from gw.core.errors import CleanroomException
from gw.god_workflow import platforms, routes
from media_doubles import APPROVED_HOST, FIXED_PNG, HEADERS, TASKS, execute, prepare_workflow

ALLOWED = frozenset({APPROVED_HOST})
OTHER_HOST = "other.example"
# media_env 夹具把 routes._output_transport 换成了 MockTransport；导入期快照留作真实实现。
REAL_OUTPUT_TRANSPORT = routes._output_transport


# ---------------------------------------------------------------------------
# 纯助手单元测试：_output_address_allowed
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("address", ["198.18.0.5", "198.19.255.1", "::ffff:198.18.0.5"])
def test_fakeip_addresses_pass_only_for_allowlisted_host(address):
    """fake-ip 网段（含 IPv4-mapped 形式）仅对白名单内主机放行。"""
    assert routes._output_address_allowed(APPROVED_HOST, address, ALLOWED) is True
    assert routes._output_address_allowed(OTHER_HOST, address, ALLOWED) is False


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.1", "192.168.1.1", "172.16.0.1", "169.254.169.254",
    "0.0.0.0", "224.0.0.1", "240.0.0.1", "::1", "fe80::1", "fc00::1",
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:169.254.169.254", "ff02::1",
])
def test_private_and_local_addresses_rejected_even_for_allowlisted_host(address):
    """回环/私网/链路本地/多播/保留/未指定及 IPv6 等价形式永不放行。"""
    assert routes._output_address_allowed(APPROVED_HOST, address, ALLOWED) is False
    assert routes._output_address_allowed(OTHER_HOST, address, ALLOWED) is False


def test_global_addresses_pass_without_allowlist():
    """全局公网地址不依赖白名单放行。"""
    assert routes._output_address_allowed(APPROVED_HOST, "8.8.8.8", ALLOWED) is True
    assert routes._output_address_allowed(OTHER_HOST, "8.8.8.8", ALLOWED) is True


def test_fakeip_range_boundary_is_exact():
    """/15 上界之外不得借道 fake-ip 规则：198.20.0.1 是全局地址，按全局规则放行。"""
    assert ipaddress.ip_address("198.20.0.1") not in routes._FAKE_IP_NETWORK
    assert routes._output_address_allowed(APPROVED_HOST, "198.20.0.1", ALLOWED) is True


def test_non_global_outside_fakeip_not_rescued_by_allowlist():
    """非全局且不在 fake-ip 网段（TEST-NET-2）时，白名单不能救行。"""
    assert routes._output_address_allowed(APPROVED_HOST, "198.51.100.1", ALLOWED) is False


def test_allowlisted_host_normalized_before_match():
    """主机名比较前先转小写并去掉结尾根点。"""
    assert routes._output_address_allowed("APPROVED.EXAMPLE.", "198.18.0.5", ALLOWED) is True
    assert routes._output_address_allowed("approved.example.", "198.18.0.5", ALLOWED) is True


def test_unparsable_address_rejected():
    """无法解析的地址按拒绝处理。"""
    assert routes._output_address_allowed(APPROVED_HOST, "not-an-ip", ALLOWED) is False
    assert routes._output_address_allowed(APPROVED_HOST, "", ALLOWED) is False


# ---------------------------------------------------------------------------
# 连接层后端单元测试：伪造 getaddrinfo 与内层后端
# ---------------------------------------------------------------------------
class _RecordingBackend:
    """替身内层后端：记录 connect_tcp 的实参，不真正联网。"""

    def __init__(self):
        self.calls: list[dict] = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.calls.append({"host": host, "port": port, "timeout": timeout,
                           "local_address": local_address, "socket_options": socket_options})
        return object()

    async def connect_unix_socket(self, *args, **kwargs):
        raise AssertionError("产物下载不允许本地套接字")

    async def sleep(self, seconds):
        await asyncio.sleep(0)


def _address_infos(addresses: list[str], port: int = 443) -> list[tuple]:
    infos = []
    for address in addresses:
        family = socket.AF_INET6 if ipaddress.ip_address(address).version == 6 else socket.AF_INET
        sockaddr = (address, port, 0, 0) if family == socket.AF_INET6 else (address, port)
        infos.append((family, socket.SOCK_STREAM, 6, "", sockaddr))
    return infos


def _install_resolver(monkeypatch, plan: dict[str, list[list[str]]]):
    """替身 DNS：按主机名记录解析次数，plan 内地址序列逐次返回（末项重复）。"""
    calls: list[tuple[str, int]] = []

    async def resolve(host, port, *args, **kwargs):
        calls.append((host, port))
        sequence = plan[host]
        addresses = sequence.pop(0) if len(sequence) > 1 else sequence[0]
        return _address_infos(addresses, port)

    # 类属性挂载需 staticmethod，避免实例绑定把 loop 当作首个实参。
    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", staticmethod(resolve))
    return calls


def test_backend_pins_validated_fakeip(monkeypatch):
    """白名单主机解析到 fake-ip：内层后端收到钉住的 IP 字面量，且只解析一次。"""
    resolver_calls = _install_resolver(monkeypatch, {APPROVED_HOST: [["198.18.0.7"]]})
    inner = _RecordingBackend()
    backend = routes._PublicOutputBackend(inner, ALLOWED)

    async def scenario():
        await backend.connect_tcp(APPROVED_HOST, 443)

    asyncio.run(scenario())
    assert inner.calls == [{"host": "198.18.0.7", "port": 443, "timeout": None,
                            "local_address": None, "socket_options": None}]
    assert resolver_calls == [(APPROVED_HOST, 443)]


def test_backend_rejects_mixed_resolution(monkeypatch):
    """解析结果混入未获准地址时整体拒绝，内层后端不被调用。"""
    resolver_calls = _install_resolver(monkeypatch, {APPROVED_HOST: [["198.18.0.7", "127.0.0.1"]]})
    inner = _RecordingBackend()
    backend = routes._PublicOutputBackend(inner, ALLOWED)

    async def scenario():
        await backend.connect_tcp(APPROVED_HOST, 443)

    with pytest.raises(CleanroomException) as excinfo:
        asyncio.run(scenario())
    assert excinfo.value.code == "OUTPUT_ADDRESS_NOT_ALLOWED"
    assert inner.calls == []
    assert len(resolver_calls) == 1


def test_backend_rejects_rebound_resolution(monkeypatch):
    """重绑定场景：首次解析 198.18.0.7 获准并钉住；解析被换成立即拒绝，不再连接。"""
    resolver_calls = _install_resolver(monkeypatch, {APPROVED_HOST: [["198.18.0.7"], ["127.0.0.1"]]})
    inner = _RecordingBackend()
    backend = routes._PublicOutputBackend(inner, ALLOWED)

    async def scenario():
        await backend.connect_tcp(APPROVED_HOST, 443)

    asyncio.run(scenario())
    assert [call["host"] for call in inner.calls] == ["198.18.0.7"]
    assert resolver_calls == [(APPROVED_HOST, 443)]

    async def rebound():
        await backend.connect_tcp(APPROVED_HOST, 443)

    with pytest.raises(CleanroomException) as excinfo:
        asyncio.run(rebound())
    assert excinfo.value.code == "OUTPUT_ADDRESS_NOT_ALLOWED"
    assert [call["host"] for call in inner.calls] == ["198.18.0.7"]
    assert resolver_calls == [(APPROVED_HOST, 443)] * 2


def test_backend_rejects_fakeip_for_unlisted_host(monkeypatch):
    """非白名单主机解析到 fake-ip：连接层拒绝，内层后端不被调用。"""
    _install_resolver(monkeypatch, {OTHER_HOST: [["198.18.0.7"]]})
    inner = _RecordingBackend()
    backend = routes._PublicOutputBackend(inner, ALLOWED)

    async def scenario():
        await backend.connect_tcp(OTHER_HOST, 443)

    with pytest.raises(CleanroomException) as excinfo:
        asyncio.run(scenario())
    assert excinfo.value.code == "OUTPUT_ADDRESS_NOT_ALLOWED"
    assert inner.calls == []


# ---------------------------------------------------------------------------
# 端到端回收测试：真实 _output_transport + 替身 DNS/套接字层
# ---------------------------------------------------------------------------
class _FakeStream:
    """替身网络流：记录 SNI 主机名与写入字节，回放固定 HTTP/1.1 200 响应。"""

    def __init__(self, payload: bytes):
        self._payload = payload
        self.tls_server_hostnames: list[str] = []
        self.written = b""

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.tls_server_hostnames.append(server_hostname)
        return self

    async def write(self, buffer: bytes, timeout=None) -> None:
        self.written += bytes(buffer)

    async def read(self, max_bytes: int, timeout=None) -> bytes:
        data, self._payload = self._payload, b""
        return data

    async def aclose(self) -> None:
        pass

    def get_extra_info(self, info: str):
        return None


class _PinnedStreamBackend:
    """替身 AutoBackend：只接受钉住后的地址字面量，返回替身网络流。"""

    def __init__(self, stream: _FakeStream):
        self.stream = stream
        self.calls: list[tuple[str, int]] = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.calls.append((host, port))
        return self.stream

    async def connect_unix_socket(self, *args, **kwargs):
        raise AssertionError("产物下载不允许本地套接字")

    async def sleep(self, seconds):
        await asyncio.sleep(0)


def _http_200_png() -> bytes:
    head = (f"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\n"
            f"Content-Length: {len(FIXED_PNG)}\r\nConnection: close\r\n\r\n").encode("ascii")
    return head + FIXED_PNG


def test_e2e_collection_pins_fakeip_and_keeps_tls_hostname(media_client, fake_rh, monkeypatch):
    """回收全程走真实 _output_transport：fake-ip 放行、钉住 IP、TLS/Host 仍是原主机名。

    DNS 与套接字层均为替身，隔离由严格解析断言与替身后端保证。
    """
    monkeypatch.setenv("GW_WORKFLOW_OUTPUT_HOSTS", APPROVED_HOST)
    monkeypatch.setattr(routes, "_output_transport", REAL_OUTPUT_TRANSPORT)
    from httpcore._backends import auto as auto_backend_module
    stream = _FakeStream(_http_200_png())
    inner = _PinnedStreamBackend(stream)
    monkeypatch.setattr(auto_backend_module, "AutoBackend", lambda: inner)

    async def query(task_id, api_key, domain=platforms.RUNNINGHUB_DEFAULT_DOMAIN):
        return {"status": "SUCCESS",
                "outputs": [{"fileUrl": f"https://{APPROVED_HOST}/out-0001.png",
                             "fileName": "out-0001.png"}], "raw": {}}

    monkeypatch.setattr(platforms, "query_rh_task_outputs", query)

    async def resolve(host, port, *args, **kwargs):
        assert (host, port) == (APPROVED_HOST, 443), (host, port)
        return _address_infos(["198.18.0.7"], port)

    # 类级替换（staticmethod 防实例绑定）覆盖服务端事件循环：GET 轮询在该循环内同步完成回收。
    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", staticmethod(resolve))

    _, workflow = prepare_workflow(media_client)
    started = execute(media_client, workflow, "rh_cloud", key="rh-pin-e2e")
    assert started.status_code == 202, started.text
    task = media_client.get(f"{TASKS}/{started.json()['job_id']}", headers=HEADERS)
    assert task.status_code == 200, task.text
    body = task.json()
    assert body["status"] == "completed", body
    assert body["collection_status"] == "completed"
    assert body["messages"] == []
    assert body["outputs"][0]["registered"] is True
    assert body["outputs"][0].get("asset_id")
    # 连接层钉住：内层后端只收到已验证的 fake-ip 字面量；TLS SNI 与 Host 头保持原始主机名。
    assert inner.calls == [("198.18.0.7", 443)]
    assert stream.tls_server_hostnames == [APPROVED_HOST]
    assert b"Host: approved.example" in stream.written
