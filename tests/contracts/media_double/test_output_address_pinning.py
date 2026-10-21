"""产物下载地址钉定：Clash fake-ip 白名单、DNS 重绑定防护与逐跳重定向校验。

助手与下载链测试全部走纯代码与替身（伪造 _getaddrinfo / MockTransport），
TLS 证书校验用本机自签证书服务器验证，不访问外部网络。
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl
import threading
from datetime import UTC, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from media_doubles import (
    APPROVED_HOST,
    FIXED_PNG,
    HEADERS,
    TASKS,
    execute,
    prepare_workflow,
)

from gw.core.errors import CleanroomException
from gw.god_workflow import platforms, routes

ALLOWED = frozenset({APPROVED_HOST})
OTHER_HOST = "other.example"
SECOND_HOST = "second.example"
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


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "192.168.1.1",
        "172.16.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "224.0.0.1",
        "240.0.0.1",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "::ffff:169.254.169.254",
        "ff02::1",
    ],
)
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
    assert (
        routes._output_address_allowed(APPROVED_HOST, "198.51.100.1", ALLOWED) is False
    )


def test_allowlisted_host_normalized_before_match():
    """主机名比较前先转小写并去掉结尾根点。"""
    assert (
        routes._output_address_allowed("APPROVED.EXAMPLE.", "198.18.0.5", ALLOWED)
        is True
    )
    assert (
        routes._output_address_allowed("approved.example.", "198.18.0.5", ALLOWED)
        is True
    )


def test_unparsable_address_rejected():
    """无法解析的地址按拒绝处理。"""
    assert routes._output_address_allowed(APPROVED_HOST, "not-an-ip", ALLOWED) is False
    assert routes._output_address_allowed(APPROVED_HOST, "", ALLOWED) is False


# ---------------------------------------------------------------------------
# 解析钉定单元测试：伪造 _getaddrinfo
# ---------------------------------------------------------------------------
def _address_infos(addresses: list[str], port: int = 443) -> list[tuple]:
    infos = []
    for address in addresses:
        family = (
            socket.AF_INET6
            if ipaddress.ip_address(address).version == 6
            else socket.AF_INET
        )
        sockaddr = (
            (address, port, 0, 0) if family == socket.AF_INET6 else (address, port)
        )
        infos.append((family, socket.SOCK_STREAM, 6, "", sockaddr))
    return infos


def _install_resolver(monkeypatch, plan: dict[str, list[list[str]]]):
    """替身 DNS：按主机名记录解析次数，plan 内地址序列逐次返回（末项重复）。"""
    calls: list[tuple[str, int]] = []

    async def resolve(host, port):
        calls.append((host, port))
        sequence = plan[host]
        addresses = sequence.pop(0) if len(sequence) > 1 else sequence[0]
        return _address_infos(addresses, port)

    monkeypatch.setattr(routes, "_getaddrinfo", resolve)
    return calls


def test_resolve_pins_first_address_when_all_allowed(monkeypatch):
    """白名单主机解析到 fake-ip：钉住首个地址，且只解析一次。"""
    resolver_calls = _install_resolver(monkeypatch, {APPROVED_HOST: [["198.18.0.7"]]})
    pinned = asyncio.run(routes._resolve_pinned(APPROVED_HOST, 443, ALLOWED))
    assert pinned == "198.18.0.7"
    assert resolver_calls == [(APPROVED_HOST, 443)]


def test_resolve_rejects_mixed_resolution(monkeypatch):
    """解析结果混入未获准地址时整体拒绝。"""
    resolver_calls = _install_resolver(
        monkeypatch, {APPROVED_HOST: [["198.18.0.7", "127.0.0.1"]]}
    )
    with pytest.raises(CleanroomException) as excinfo:
        asyncio.run(routes._resolve_pinned(APPROVED_HOST, 443, ALLOWED))
    assert excinfo.value.code == "OUTPUT_ADDRESS_NOT_ALLOWED"
    assert resolver_calls == [(APPROVED_HOST, 443)]


def test_resolve_rejects_empty_resolution(monkeypatch):
    """空解析结果一律拒绝。"""
    _install_resolver(monkeypatch, {APPROVED_HOST: [[]]})
    with pytest.raises(CleanroomException) as excinfo:
        asyncio.run(routes._resolve_pinned(APPROVED_HOST, 443, ALLOWED))
    assert excinfo.value.code == "OUTPUT_ADDRESS_NOT_ALLOWED"


def test_resolve_rejects_rebound_resolution(monkeypatch):
    """重绑定场景：首次解析 198.18.0.7 获准并钉住；解析被换成立即拒绝。"""
    resolver_calls = _install_resolver(
        monkeypatch, {APPROVED_HOST: [["198.18.0.7"], ["127.0.0.1"]]}
    )
    assert (
        asyncio.run(routes._resolve_pinned(APPROVED_HOST, 443, ALLOWED)) == "198.18.0.7"
    )
    with pytest.raises(CleanroomException) as excinfo:
        asyncio.run(routes._resolve_pinned(APPROVED_HOST, 443, ALLOWED))
    assert excinfo.value.code == "OUTPUT_ADDRESS_NOT_ALLOWED"
    assert resolver_calls == [(APPROVED_HOST, 443)] * 2


def test_resolve_rejects_fakeip_for_unlisted_host(monkeypatch):
    """非白名单主机解析到 fake-ip：拒绝。"""
    _install_resolver(monkeypatch, {OTHER_HOST: [["198.18.0.7"]]})
    with pytest.raises(CleanroomException) as excinfo:
        asyncio.run(routes._resolve_pinned(OTHER_HOST, 443, ALLOWED))
    assert excinfo.value.code == "OUTPUT_ADDRESS_NOT_ALLOWED"


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.1", "192.168.1.1", "172.16.0.1", "169.254.169.254"]
)
def test_resolve_rejects_private_answers_for_allowlisted_host(monkeypatch, address):
    """白名单主机解析到私网/链路本地地址：拒绝。"""
    _install_resolver(monkeypatch, {APPROVED_HOST: [[address]]})
    with pytest.raises(CleanroomException) as excinfo:
        asyncio.run(routes._resolve_pinned(APPROVED_HOST, 443, ALLOWED))
    assert excinfo.value.code == "OUTPUT_ADDRESS_NOT_ALLOWED"


def test_resolve_pins_ipv6_global_address(monkeypatch):
    """全局 IPv6 地址按全局规则放行并原样钉住。"""
    _install_resolver(monkeypatch, {APPROVED_HOST: [["2606:4700:4700::1111"]]})
    assert (
        asyncio.run(routes._resolve_pinned(APPROVED_HOST, 443, ALLOWED))
        == "2606:4700:4700::1111"
    )


# ---------------------------------------------------------------------------
# 下载链测试：MockTransport 捕获请求 + 伪造 _getaddrinfo
# ---------------------------------------------------------------------------
def _install_download_transport(monkeypatch, handler):
    """替身传输层：对齐真实 _output_transport 签名，handler 返回响应并捕获发出的请求。"""
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(
        routes,
        "_output_transport",
        lambda allowed_hosts=None, verify=True: httpx.MockTransport(handle),
    )
    return seen


def _png_response() -> httpx.Response:
    return httpx.Response(200, content=FIXED_PNG, headers={"content-type": "image/png"})


def test_download_pins_fakeip_and_sends_original_hostname(monkeypatch):
    """下载目标为钉住 IP，Host 头与 sni_hostname 扩展保留原主机名；每跳只解析一次。"""
    seen = _install_download_transport(monkeypatch, lambda request: _png_response())
    resolver_calls = _install_resolver(
        monkeypatch, {APPROVED_HOST: [["198.18.0.7"], ["127.0.0.1"]]}
    )

    content, media_type = asyncio.run(
        routes._download_pinned_output(
            f"https://{APPROVED_HOST}/out.png?expires=1", ALLOWED
        )
    )

    assert content == FIXED_PNG and media_type == "image/png"
    assert resolver_calls == [(APPROVED_HOST, 443)]
    assert len(seen) == 1
    request = seen[0]
    assert request.url.host == "198.18.0.7"
    assert str(request.url).startswith("https://198.18.0.7/out.png?expires=1")
    assert request.headers["host"] == APPROVED_HOST
    assert request.extensions.get("sni_hostname") == APPROVED_HOST


def test_download_rejects_redirect_to_unapproved_host(monkeypatch):
    """重定向到未获准主机：URL 层拒绝，不发出第二跳请求。"""
    seen = _install_download_transport(
        monkeypatch,
        lambda request: httpx.Response(
            302, headers={"location": f"https://{OTHER_HOST}/other.png"}
        ),
    )
    _install_resolver(monkeypatch, {APPROVED_HOST: [["198.18.0.7"]]})

    with pytest.raises(CleanroomException) as excinfo:
        asyncio.run(
            routes._download_pinned_output(f"https://{APPROVED_HOST}/out.png", ALLOWED)
        )
    assert excinfo.value.code == "OUTPUT_URL_NOT_ALLOWED"
    assert len(seen) == 1
    assert seen[0].url.host == "198.18.0.7"


def test_download_redirect_to_allowed_host_reresolves_and_repins(monkeypatch):
    """重定向到另一获准主机：重新校验 URL、重新解析并钉住新地址。"""
    allowed = frozenset({APPROVED_HOST, SECOND_HOST})
    resolver_calls = _install_resolver(
        monkeypatch,
        {
            APPROVED_HOST: [["198.18.0.7"]],
            SECOND_HOST: [["198.18.0.8"]],
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "198.18.0.7":
            return httpx.Response(
                302, headers={"location": f"https://{SECOND_HOST}/other.png"}
            )
        return _png_response()

    seen = _install_download_transport(monkeypatch, handler)
    content, _media = asyncio.run(
        routes._download_pinned_output(f"https://{APPROVED_HOST}/out.png", allowed)
    )

    assert content == FIXED_PNG
    assert [request.url.host for request in seen] == ["198.18.0.7", "198.18.0.8"]
    assert [request.headers["host"] for request in seen] == [APPROVED_HOST, SECOND_HOST]
    assert [request.extensions.get("sni_hostname") for request in seen] == [
        APPROVED_HOST,
        SECOND_HOST,
    ]
    assert resolver_calls == [(APPROVED_HOST, 443), (SECOND_HOST, 443)]


def test_download_redirect_hop_with_private_resolution_rejected(monkeypatch):
    """第二跳解析到私网地址：拒绝，不再发出该跳下载请求。"""
    allowed = frozenset({APPROVED_HOST, SECOND_HOST})
    _install_resolver(
        monkeypatch,
        {
            APPROVED_HOST: [["198.18.0.7"]],
            SECOND_HOST: [["10.0.0.1"]],
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "198.18.0.7":
            return httpx.Response(
                302, headers={"location": f"https://{SECOND_HOST}/other.png"}
            )
        return _png_response()

    seen = _install_download_transport(monkeypatch, handler)
    with pytest.raises(CleanroomException) as excinfo:
        asyncio.run(
            routes._download_pinned_output(f"https://{APPROVED_HOST}/out.png", allowed)
        )
    assert excinfo.value.code == "OUTPUT_ADDRESS_NOT_ALLOWED"
    assert len(seen) == 1


def test_download_redirect_hops_capped_at_three(monkeypatch):
    """重定向超过 3 跳：拒绝，共发出 1 + 3 个请求。"""
    seen = _install_download_transport(
        monkeypatch,
        lambda request: httpx.Response(
            302, headers={"location": f"https://{APPROVED_HOST}/next.png"}
        ),
    )
    _install_resolver(monkeypatch, {APPROVED_HOST: [["198.18.0.7"]]})

    with pytest.raises(CleanroomException) as excinfo:
        asyncio.run(
            routes._download_pinned_output(f"https://{APPROVED_HOST}/out.png", ALLOWED)
        )
    assert excinfo.value.code == "DOWNLOAD_FAILED"
    assert len(seen) == 4


def test_pinned_target_brackets_ipv6(monkeypatch):
    """IPv6 钉住地址写入目标 URL 时加方括号，路径与查询串保留。"""
    assert (
        routes._pinned_target("https://a.example/x.png?q=1", "2606:4700:4700::1111")
        == "https://[2606:4700:4700::1111]/x.png?q=1"
    )
    assert (
        routes._pinned_target("https://a.example/x.png?q=1", "198.18.0.7")
        == "https://198.18.0.7/x.png?q=1"
    )


# ---------------------------------------------------------------------------
# 默认产物主机：_output_allowed_hosts
# ---------------------------------------------------------------------------
def test_output_allowed_hosts_defaults_and_override(monkeypatch):
    """环境变量显式配置时完全覆盖；为空时仅 RunningHub 平台有内置默认。"""
    monkeypatch.delenv("GW_WORKFLOW_OUTPUT_HOSTS", raising=False)
    assert routes._output_allowed_hosts("rh_cloud") == routes.RH_DEFAULT_OUTPUT_HOSTS
    assert routes._output_allowed_hosts("runninghub") == routes.RH_DEFAULT_OUTPUT_HOSTS
    assert routes._output_allowed_hosts("comfyui") == frozenset()
    monkeypatch.setenv("GW_WORKFLOW_OUTPUT_HOSTS", " X.Example , y.example ,")
    configured = routes._output_allowed_hosts("rh_cloud")
    assert configured == frozenset({"x.example", "y.example"})
    assert routes.RH_DEFAULT_OUTPUT_HOSTS.isdisjoint(configured)


# ---------------------------------------------------------------------------
# 端到端回收测试：真实下载链 + 替身 DNS 与传输层
# ---------------------------------------------------------------------------
def test_e2e_collection_pins_fakeip_and_keeps_tls_hostname(
    media_client, fake_rh, monkeypatch
):
    """回收全程走真实下载链：fake-ip 放行、钉住 IP、Host 头与 SNI 扩展仍是原主机名。"""
    monkeypatch.setenv("GW_WORKFLOW_OUTPUT_HOSTS", APPROVED_HOST)
    seen = _install_download_transport(monkeypatch, lambda request: _png_response())

    async def query(task_id, api_key, domain=platforms.RUNNINGHUB_DEFAULT_DOMAIN):
        return {
            "status": "SUCCESS",
            "outputs": [
                {
                    "fileUrl": f"https://{APPROVED_HOST}/out-0001.png",
                    "fileName": "out-0001.png",
                }
            ],
            "raw": {},
        }

    monkeypatch.setattr(platforms, "query_rh_task_outputs", query)

    async def resolve(host, port):
        assert (host, port) == (APPROVED_HOST, 443), (host, port)
        return _address_infos(["198.18.0.7"], port)

    monkeypatch.setattr(routes, "_getaddrinfo", resolve)

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
    # 每跳钉定：请求目标是已验证的 fake-ip 字面量；Host 头与 sni_hostname 保持原始主机名。
    assert len(seen) == 1
    assert seen[0].url.host == "198.18.0.7"
    assert seen[0].headers["host"] == APPROVED_HOST
    assert seen[0].extensions.get("sni_hostname") == APPROVED_HOST


# ---------------------------------------------------------------------------
# 真实 TLS：本机自签证书服务器验证 SNI 主机名与证书校验保持开启
# ---------------------------------------------------------------------------
_TLS_BODY = FIXED_PNG


class _TLSHandler(BaseHTTPRequestHandler):
    """固定返回 PNG 的最小 HTTP/1.1 处理器。"""

    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(_TLS_BODY)))
        self.end_headers()
        self.wfile.write(_TLS_BODY)

    def log_message(self, *args):
        pass


def _generate_self_signed_cert(directory, common_name: str):
    """用 cryptography 生效自签证书（CN/SAN=common_name），返回证书与私钥路径。"""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(common_name)]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / "cert.pem"
    key_path = directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


@pytest.fixture
def local_tls_server(tmp_path):
    """本机 127.0.0.1 TLS 服务器：证书签给 approved.example，客户端凭据走 cafile 信任。"""
    cert_path, key_path = _generate_self_signed_cert(tmp_path, APPROVED_HOST)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TLSHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert_path), str(key_path))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1], cert_path
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_tls_certificate_hostname_verification_on_real_transport(
    media_client, fake_rh, local_tls_server, monkeypatch
):
    """真实传输层：sni_hostname 用原主机名做 SNI 与证书校验；主机名不符映射 DOWNLOAD_ERROR。

    地址判定在本用例中替换为固定 127.0.0.1，只验证 TLS 层行为；沙箱无法绑定特权
    端口 443，钉住目标改写到本机临时端口，不影响任何校验逻辑。
    """
    server_port, cert_path = local_tls_server
    real_pinned_target = routes._pinned_target
    monkeypatch.setenv("GW_WORKFLOW_OUTPUT_HOSTS", f"{APPROVED_HOST},{OTHER_HOST}")

    def pinned_target_on_local_port(url, pinned_ip):
        return real_pinned_target(url, pinned_ip).replace(
            "https://127.0.0.1/", f"https://127.0.0.1:{server_port}/"
        )

    monkeypatch.setattr(routes, "_pinned_target", pinned_target_on_local_port)

    # AC 允许：本用例只验证 TLS 层，地址判定整体替换为钉住 127.0.0.1。
    async def resolve_to_loopback(host, port, allowed_hosts):
        return "127.0.0.1"

    monkeypatch.setattr(routes, "_resolve_pinned", resolve_to_loopback)

    async def query(task_id, api_key, domain=platforms.RUNNINGHUB_DEFAULT_DOMAIN):
        return {
            "status": "SUCCESS",
            "outputs": [
                {"fileUrl": f"https://{APPROVED_HOST}/ok.png", "fileName": "ok.png"},
                {"fileUrl": f"https://{OTHER_HOST}/bad.png", "fileName": "bad.png"},
            ],
            "raw": {},
        }

    monkeypatch.setattr(platforms, "query_rh_task_outputs", query)

    # 仅在本用例把传输层证书信任指向本机自签 CA（真实 _output_transport 的 verify 参数）。
    client_ssl = ssl.create_default_context(cafile=str(cert_path))
    monkeypatch.setattr(
        routes,
        "_output_transport",
        lambda allowed_hosts=None, verify=True: REAL_OUTPUT_TRANSPORT(
            allowed_hosts, verify=client_ssl
        ),
    )

    _, workflow = prepare_workflow(media_client)
    started = execute(media_client, workflow, "rh_cloud", key="rh-pin-tls")
    assert started.status_code == 202, started.text
    task = media_client.get(f"{TASKS}/{started.json()['job_id']}", headers=HEADERS)
    assert task.status_code == 200, task.text
    body = task.json()
    # 证书与 SNI 主机名匹配：TLS 层成功并完成登记。
    assert body.get("outputs") and body["outputs"][0]["registered"] is True, body
    assert body["outputs"][0].get("asset_id")
    # 主机名与证书不匹配：TLS 证书校验失败，按单项失败记 DOWNLOAD_ERROR。
    assert body["outputs"][1]["registered"] is False
    assert body["outputs"][1]["code"] == "DOWNLOAD_ERROR"
    assert body["collection_status"] == "partial_failed"
