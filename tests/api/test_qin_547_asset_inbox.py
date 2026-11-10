"""QIN-547（GitHub #603）：收集箱默认目录、迁移幂等与改位置。

覆盖票面 AC：
1. 收集箱是 data_root 下的真实文件夹，位置可改；
2. 托管资产记录并入收集箱（外部 URL 保留为 URL 条目）；
3. 迁移可重复执行，重复执行不多出数据；失败不入账、可重试；
4. 改位置支持迁移/不迁移，搬不动时不切路径；
5. 新素材角标按「已查看时间点」计算；
6. 路径校验：拒绝相对路径、互为上下级目录与占用文件；
7. 主体隔离：迁移只覆盖当前主体可见的资产，遗留收集目录只列当前主体的子目录。
"""

import hashlib
import io
import re
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from PIL import Image

from gw.api.app import create_app
from gw.asset_library import inbox
from gw.asset_library.service import default_asset_library_service
from gw.core.asset_visibility import asset_owner_key
from gw.core.auth import require_authenticated

BASE = "/api/asset-inbox"
AUTH = {"Authorization": "Bearer test-owner", "X-User-Role": "editor"}
EDIT_AUTH = {**AUTH}


@pytest.fixture
def inbox_runtime(tmp_path, monkeypatch):
    """三态隔离：数据根与允许根都在 tmp_path，不触碰真实数据。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(data_dir))
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed))
    return {"data": data_dir, "allowed": allowed, "tmp": tmp_path}


def _error_code(response) -> str:
    detail = response.json().get("detail")
    return str(detail.get("code") if isinstance(detail, dict) else detail)


def _owner_key() -> str:
    """与生产一致地推导当前请求主体键（不手写哈希，避免两边算法漂移）。"""
    context = require_authenticated(AUTH["Authorization"], "editor")
    return asset_owner_key(context)


def _register_item(
    name: str, url: str | None, owner: str | None = None
) -> str:
    item = default_asset_library_service.register_item(
        asset_id=f"asset_qin547_{name}",
        name=name,
        category_type="image",
        category_name="图片",
        url=url,
        workflow_owner=owner,
    )
    return item.asset_id


def _upload(client, filename: str, content: bytes) -> dict:
    response = client.post(
        f"{BASE}/upload",
        headers=EDIT_AUTH,
        files={"files": (filename, content, "image/png")},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["count"] == 1, payload
    return payload["items"][0]


def test_inbox_defaults_to_real_folder_under_data_root(inbox_runtime):
    """AC1：收集箱默认落在 data_root 下的真实文件夹，且后端会创建它。"""
    with TestClient(create_app()) as client:
        response = client.get(BASE, headers=AUTH)
        assert response.status_code == 200, response.text
        settings = response.json()["settings"]
        path = Path(settings["path"])
        assert path == (inbox_runtime["data"] / "inbox").resolve()
        assert path.is_dir(), "默认收集箱目录应真实存在"
        assert settings["is_default"] is True
        assert settings["unread_count"] == 0


def test_migrate_links_local_files_keeps_source_and_external_urls(
    inbox_runtime,
):
    """AC2/AC3：本地文件进收集箱、源文件保留、外部 URL 保留条目；重复执行不多出数据。"""
    local_file = inbox_runtime["allowed"] / "reference.png"
    local_file.write_bytes(b"local-payload")
    local_id = _register_item("本地参考图.png", local_file.as_uri())
    external_id = _register_item("外部参考图", "https://example.com/reference.png")

    with TestClient(create_app()) as client:
        first = client.post(f"{BASE}/migrate", headers=EDIT_AUTH)
        assert first.status_code == 200, first.text
        payload = first.json()
        assert payload["moved_files"] == 1, payload
        assert payload["url_entries"] == 1, payload
        assert payload["failed"] == []
        assert payload["linked"] + payload["copied"] == 1, payload
        # 审核 635-r1 #5：不能用 move，否则 asset_library 里的 url 会变成死链。
        assert local_file.is_file(), "迁移必须保留源文件"
        assert local_file.read_bytes() == b"local-payload"

        inbox_files = list((inbox_runtime["data"] / "inbox").rglob("*.png"))
        assert len(inbox_files) == 1, "收集箱里应多出一份副本"
        assert inbox_files[0].read_bytes() == b"local-payload"
        # GNS-2026：磁盘名纯 ASCII 且不可预测，用户原名只做展示名。
        assert re.fullmatch(r"inbox_[0-9a-f]{12}\.png", inbox_files[0].name), inbox_files[0].name

        listing = client.get(BASE, headers=AUTH).json()
        local_entry = next(item for item in listing["items"] if item["source"] == "inbox")
        assert local_entry["name"] == "本地参考图.png", "展示名仍是用户原名"

        second = client.post(f"{BASE}/migrate", headers=EDIT_AUTH)
        assert second.status_code == 200, second.text
        again = second.json()
        assert again["moved_files"] == 0, "重复迁移不得再搬文件"
        assert again["url_entries"] == 0, "重复迁移不得新增 URL 条目"
        assert again["skipped"] == 2, "两条记录均应记为已迁移"
        assert again["total_records"] == payload["total_records"] == 2
        assert len(list((inbox_runtime["data"] / "inbox").rglob("*.png"))) == 1, "重复执行不多出文件"

        by_asset = {
            item["asset_id"]: item
            for item in listing["items"]
            if item["source"] == "asset_library"
        }
        assert external_id in by_asset
        assert by_asset[external_id]["url"] == "https://example.com/reference.png"
        assert local_id not in by_asset, "已迁入本地的记录由磁盘文件呈现，不重复列 URL 条目"
        assert listing["settings"]["migrated_asset_count"] == 2


def test_migrate_failure_is_not_recorded_and_can_retry(inbox_runtime, monkeypatch):
    """阻断 1：搬运失败不得入账，否则下次调用会被 skip，文件永远进不了收集箱。"""
    local_file = inbox_runtime["allowed"] / "flaky.png"
    local_file.write_bytes(b"flaky")
    asset_id = _register_item("易失败素材.png", local_file.as_uri())

    with TestClient(create_app()) as client:
        real_transfer = inbox._transfer_into

        def boom(target, source):
            raise OSError("磁盘不可写")

        monkeypatch.setattr(inbox, "_transfer_into", boom)
        broken = client.post(f"{BASE}/migrate", headers=EDIT_AUTH).json()
        assert broken["failed"] == [
            {"asset_id": asset_id, "code": "INBOX_MIGRATE_FAILED"}
        ], broken
        assert broken["moved_files"] == 0
        assert broken["total_records"] == 0, "失败记录不得入账"

        monkeypatch.setattr(inbox, "_transfer_into", real_transfer)
        retried = client.post(f"{BASE}/migrate", headers=EDIT_AUTH).json()
        assert retried["failed"] == [], retried
        assert retried["moved_files"] == 1, "第二次调用必须重试并成功"
        assert retried["skipped"] == 0, "第一次失败不能被当成已迁移"
        assert list((inbox_runtime["data"] / "inbox").rglob("*.png")), "文件应真的进了收集箱"


def test_migrate_only_covers_assets_visible_to_current_owner(inbox_runtime):
    """阻断 2：迁移经 iter_library_items → asset_visible，必须带上请求主体上下文。"""
    local_file = inbox_runtime["allowed"] / "shared.png"
    local_file.write_bytes(b"shared")
    mine = _register_item("我的素材.png", local_file.as_uri(), owner=_owner_key())
    other = _register_item("别人的素材.png", local_file.as_uri(), owner="someone-else")

    with TestClient(create_app()) as client:
        payload = client.post(f"{BASE}/migrate", headers=EDIT_AUTH).json()
        assert payload["moved_files"] == 1, payload
        assert payload["total_records"] == 1, "别人的资产不得被迁入"
        assert payload["failed"] == []
        listing = client.get(BASE, headers=AUTH).json()
        assert {item["name"] for item in listing["items"] if item["source"] == "inbox"} == {
            "我的素材.png"
        }
        assert other not in {
            str(item.get("asset_id") or "") for item in listing["items"]
        }


def test_migrate_marks_records_without_url_as_record_kind(inbox_runtime):
    """AC3：没有访问地址的托管记录标为「仅记录」，不伪装成可点击链接。"""
    no_url_id = _register_item("无地址记录", None)
    url_id = _register_item("有地址记录", "https://example.com/a.png")

    with TestClient(create_app()) as client:
        payload = client.post(f"{BASE}/migrate", headers=EDIT_AUTH).json()
        assert payload["moved_files"] == 0, payload
        assert payload["url_entries"] == 2, payload

        listing = client.get(BASE, headers=AUTH).json()
        by_asset = {item["asset_id"]: item for item in listing["items"]}
        assert by_asset[no_url_id]["kind"] == "record", "url 为空的记录应为 record"
        assert by_asset[no_url_id]["url"] is None
        assert by_asset[url_id]["kind"] == "url", "真实地址仍为链接条目"


def test_migrate_never_moves_files_outside_allowed_roots(inbox_runtime):
    """迁移边界：只搬已挂载素材根内的文件；根外本地文件保留为 URL 条目，不碰盘。"""
    outside = inbox_runtime["tmp"] / "outside.png"
    outside.write_bytes(b"outside")
    asset_id = _register_item("根外文件", outside.as_uri())
    with TestClient(create_app()) as client:
        result = client.post(f"{BASE}/migrate", headers=EDIT_AUTH).json()
        assert result["moved_files"] == 0, result
        assert outside.is_file(), "不允许把已挂载根目录之外的文件搬进收集箱"
        listing = client.get(BASE, headers=AUTH).json()
        entry = next(item for item in listing["items"] if item["asset_id"] == asset_id)
        assert entry["url"] == outside.as_uri()


def test_relocate_with_migrate_moves_files_and_repeat_is_noop(inbox_runtime):
    """AC1/AC6：改位置并迁移；同一位置重复提交幂等。"""
    target = inbox_runtime["tmp"] / "新收集箱"
    with TestClient(create_app()) as client:
        uploaded = _upload(client, "take.png", b"payload")
        stored = uploaded["file"]
        response = client.put(
            f"{BASE}/settings", headers=EDIT_AUTH, json={"path": str(target), "migrate": True}
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["applied"] is True, result
        assert result["migrated"] == [stored], result
        assert (target / stored).is_file()
        assert not (inbox_runtime["data"] / "inbox" / stored).exists()

        repeated = client.put(
            f"{BASE}/settings", headers=EDIT_AUTH, json={"path": str(target), "migrate": True}
        )
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["unchanged"] is True
        assert repeated.json()["migrated"] == []
        assert list(target.rglob(stored)) == [target / stored], "重复改位置不得复制文件"


def test_relocate_without_migrate_keeps_old_files(inbox_runtime):
    """AC1：选择不迁移时，旧位置文件保留在原处，新位置为空。"""
    target = inbox_runtime["tmp"] / "空收集箱"
    with TestClient(create_app()) as client:
        uploaded = _upload(client, "keep.png", b"payload")
        stored = uploaded["file"]
        response = client.put(
            f"{BASE}/settings", headers=EDIT_AUTH, json={"path": str(target), "migrate": False}
        )
        assert response.status_code == 200, response.text
        assert response.json()["migrated"] == []
        assert (inbox_runtime["data"] / "inbox" / stored).is_file()
        assert list(target.iterdir()) == []
        assert client.get(BASE, headers=AUTH).json()["count"] == 0


def test_relocate_reports_applied_false_when_move_fails(inbox_runtime, monkeypatch):
    """重要项：有文件搬不动时不切 path，并把已搬走的文件回滚，避免文件从界面消失。"""
    target = inbox_runtime["tmp"] / "半途失败"
    with TestClient(create_app()) as client:
        uploaded = _upload(client, "half.png", b"payload")
        stored = uploaded["file"]
        real_move = inbox._move_into
        calls = {"count": 0}

        def flaky(new_root, relative, source):
            calls["count"] += 1
            if calls["count"] == 1:
                raise OSError("目标卷已满")
            return real_move(new_root, relative, source)

        monkeypatch.setattr(inbox, "_move_into", flaky)
        response = client.put(
            f"{BASE}/settings", headers=EDIT_AUTH, json={"path": str(target), "migrate": True}
        )
        payload = response.json()
        assert response.status_code == 200, response.text
        assert payload["applied"] is False, payload
        assert payload["failed"], payload
        assert Path(payload["path"]) == (inbox_runtime["data"] / "inbox").resolve(), "路径不得切换"
        assert (inbox_runtime["data"] / "inbox" / stored).is_file(), "文件必须还在旧位置可访问"
        assert client.get(BASE, headers=AUTH).json()["count"] == 1


def test_unread_badge_tracks_new_materials(inbox_runtime):
    """AC5：新素材计入角标，查看后清零，再来新素材重新计数。"""
    with TestClient(create_app()) as client:
        _upload(client, "one.png", b"one")
        assert client.get(BASE, headers=AUTH).json()["unread_count"] == 1

        viewed = client.post(f"{BASE}/viewed", headers=EDIT_AUTH)
        assert viewed.status_code == 200, viewed.text
        assert client.get(BASE, headers=AUTH).json()["unread_count"] == 0

        _upload(client, "two.png", b"two")
        listing = client.get(BASE, headers=AUTH).json()
        assert listing["unread_count"] == 1
        assert listing["count"] == 2


def test_upload_keeps_same_name_without_overwriting(inbox_runtime):
    """手动上传落在收集箱；同名素材物理名不同、展示名自动加序号，不覆盖已有素材。"""
    with TestClient(create_app()) as client:
        first = _upload(client, "shot.png", b"first")
        second = _upload(client, "shot.png", b"second")
        assert first["file"] != second["file"], "物理名必须唯一"
        assert re.fullmatch(r"inbox_[0-9a-f]{12}\.png", first["file"]), first["file"]
        assert first["name"] == "shot.png"
        assert second["name"] == "shot (2).png", "展示名加序号以区分同名素材"
        inbox_dir = inbox_runtime["data"] / "inbox"
        assert (inbox_dir / first["file"]).read_bytes() == b"first"
        assert (inbox_dir / second["file"]).read_bytes() == b"second"


def test_legacy_collect_files_are_listed_in_inbox(inbox_runtime):
    """AC2：遗留 collect_inbox 收集文件在收集箱里显示（不搬迁，避免破坏受管回读）。

    重要项：遗留目录按主体分账，只列当前主体的子目录，不跨主体全量列出。
    """
    owner_directory = hashlib.sha256(_owner_key().encode("utf-8")).hexdigest()
    legacy = inbox_runtime["data"] / "registry_uploads" / "collect_inbox" / owner_directory
    legacy.mkdir(parents=True)
    (legacy / "collected.png").write_bytes(b"legacy")
    stranger = inbox_runtime["data"] / "registry_uploads" / "collect_inbox" / ("f" * 64)
    stranger.mkdir(parents=True)
    (stranger / "别人的素材.png").write_bytes(b"stranger")

    with TestClient(create_app()) as client:
        listing = client.get(BASE, headers=AUTH).json()
        sources = {item["source"] for item in listing["items"]}
        assert "collect_inbox" in sources
        entries = [item for item in listing["items"] if item["source"] == "collect_inbox"]
        assert [item["name"] for item in entries] == ["collected.png"], "不得列出其他主体的收集结果"
        assert entries[0]["file"] == (
            f"registry_uploads/collect_inbox/{owner_directory}/collected.png"
        )
        assert (legacy / "collected.png").is_file(), "遗留文件保持原处"


def test_settings_path_validation(inbox_runtime):
    """AC6：拒绝相对路径、互为上下级目录与已被文件占用的位置。"""
    occupied = inbox_runtime["tmp"] / "occupied.txt"
    occupied.write_text("x", encoding="utf-8")
    inbox = inbox_runtime["data"] / "inbox"
    with TestClient(create_app()) as client:
        relative = client.put(
            f"{BASE}/settings", headers=EDIT_AUTH, json={"path": "relative/inbox"}
        )
        assert relative.status_code == 400
        assert _error_code(relative) == "INVALID_PATH"

        nested = client.put(
            f"{BASE}/settings", headers=EDIT_AUTH, json={"path": str(inbox / "child")}
        )
        assert nested.status_code == 400
        assert _error_code(nested) == "INBOX_PATH_NESTED"

        parent = client.put(
            f"{BASE}/settings", headers=EDIT_AUTH, json={"path": str(inbox_runtime["data"])}
        )
        assert parent.status_code == 400
        assert _error_code(parent) == "INBOX_PATH_NESTED"

        file_path = client.put(
            f"{BASE}/settings", headers=EDIT_AUTH, json={"path": str(occupied)}
        )
        assert file_path.status_code == 400
        assert _error_code(file_path) == "INVALID_PATH"

        bad_flag = client.put(
            f"{BASE}/settings", headers=EDIT_AUTH, json={"path": str(occupied), "migrate": "yes"}
        )
        assert bad_flag.status_code == 400
        assert _error_code(bad_flag) == "INVALID_REQUEST"


def test_inbox_requires_authentication(inbox_runtime):
    """未认证请求一律 401，不暴露收集箱位置。"""
    with TestClient(create_app()) as client:
        assert client.get(BASE).status_code == 401
        assert client.post(f"{BASE}/migrate").status_code == 401


def _png_bytes(color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    """生成一张真实 PNG（PIL 编码），供预览链路真实解码。"""
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_inbox_upload_then_preview_roundtrip(inbox_runtime):
    """【QIN-588 AC1/AC3】上传 → 列表带受控预览参数 → media-preview 返回真实图像；二次命中缓存。"""
    with TestClient(create_app()) as client:
        uploaded = _upload(client, "shot.png", _png_bytes((255, 0, 0)))
        assert uploaded["kind"] == "image"
        assert uploaded["preview_query"], "上传响应应携带受控预览查询串"
        assert "inbox_file=" in uploaded["preview_query"]
        assert str(inbox_runtime["data"]) not in uploaded["preview_query"], "查询串不含绝对路径"

        listing = client.get(BASE, headers=AUTH).json()
        entry = next(
            item for item in listing["items"] if item["source"] == "inbox"
        )
        assert entry["preview_query"] == uploaded["preview_query"]

        preview = client.get(
            f"/api/media-preview?w=256&{entry['preview_query']}", headers=AUTH
        )
        assert preview.status_code == 200, preview.text
        assert preview.headers["content-type"].startswith("image/")
        assert len(preview.content) > 0

        again = client.get(
            f"/api/media-preview?w=256&{entry['preview_query']}", headers=AUTH
        )
        assert again.status_code == 200, again.text
        assert again.content == preview.content, "缓存二次命中应返回同一字节"


def test_inbox_preview_fails_closed(inbox_runtime):
    """【QIN-588 AC6】穿越、绝对路径、软链接逃逸与匿名一律失败关闭。"""
    with TestClient(create_app()) as client:
        uploaded = _upload(client, "guard.png", _png_bytes())
        stored = uploaded["file"]

        # 穿越到 data_root 之外（auth.sqlite3 所在层级）→ 404，绝不返回字节。
        traversal = client.get(
            f"/api/media-preview?w=256&inbox_file={stored}/../../auth/auth.sqlite3",
            headers=AUTH,
        )
        assert traversal.status_code == 404, traversal.text

        absolute = client.get(
            "/api/media-preview?w=256&inbox_file="
            + quote(str(inbox_runtime["data"] / "inbox" / stored), safe=""),
            headers=AUTH,
        )
        assert absolute.status_code == 404

        empty = client.get("/api/media-preview?w=256&inbox_file=", headers=AUTH)
        assert empty.status_code == 404

        anonymous = client.get(
            f"/api/media-preview?w=256&inbox_file={stored}"
        )
        assert anonymous.status_code == 401, "预览请求必须经鉴权"

    # 软链接逃逸：根内软链接指向根外文件，resolve 后落在外面 → 失败关闭。
    # Windows 无符号链接特权时无法构造，跳过由平台能力决定（Linux job 覆盖）。
    inbox_dir = inbox_runtime["data"] / "inbox"
    outside = inbox_runtime["tmp"] / "outside-secret.png"
    outside.write_bytes(_png_bytes((0, 255, 0)))
    try:
        link = inbox_dir / "inbox_escape.png"
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前平台无法创建符号链接")
    with TestClient(create_app()) as client:
        escaped = client.get(
            "/api/media-preview?w=256&inbox_file=inbox_escape.png", headers=AUTH
        )
        assert escaped.status_code == 404, escaped.text


def test_inbox_preview_rejects_injection_params(inbox_runtime):
    """【QIN-588 AC6】受控参数与 asset_id/path 不得组合绕过；未知参数组合失败关闭。"""
    with TestClient(create_app()) as client:
        uploaded = _upload(client, "combo.png", _png_bytes())
        query = uploaded["preview_query"]
        combo = client.get(
            f"/api/media-preview?w=256&{query}&path=C:/Windows/win.ini", headers=AUTH
        )
        assert combo.status_code == 200, "受控参数存在时应忽略 path（同链路拒绝越权路径）"
        asset_combo = client.get(
            f"/api/media-preview?w=256&{query}&asset_id=some-asset", headers=AUTH
        )
        assert asset_combo.status_code == 200, "受控参数存在时应忽略 asset_id"


def test_inbox_preview_refreshes_after_source_changes(inbox_runtime):
    """【QIN-588 AC3】源文件被替换后不得继续显示旧图（源身份 mtime+size 失效缓存）。"""
    with TestClient(create_app()) as client:
        uploaded = _upload(client, "variant.png", _png_bytes((255, 0, 0)))
        stored = uploaded["file"]
        preview_before = client.get(
            f"/api/media-preview?w=256&inbox_file={stored}", headers=AUTH
        )
        assert preview_before.status_code == 200

        source = inbox_runtime["data"] / "inbox" / stored
        source.write_bytes(_png_bytes((0, 0, 255)))
        preview_after = client.get(
            f"/api/media-preview?w=256&inbox_file={stored}", headers=AUTH
        )
        assert preview_after.status_code == 200, preview_after.text
        assert preview_after.content != preview_before.content, "源变化后必须出真实新图"


def test_collect_inbox_preview_is_scoped_to_current_owner(inbox_runtime):
    """【QIN-588 AC6】遗留收集条目的预览按主体分账：自己的文件可预览，别人的不下发。"""
    owner_directory = hashlib.sha256(_owner_key().encode("utf-8")).hexdigest()
    legacy = inbox_runtime["data"] / "registry_uploads" / "collect_inbox" / owner_directory
    legacy.mkdir(parents=True)
    (legacy / "old.png").write_bytes(_png_bytes((0, 128, 255)))
    stranger = inbox_runtime["data"] / "registry_uploads" / "collect_inbox" / ("e" * 64)
    stranger.mkdir(parents=True)
    (stranger / "secret.png").write_bytes(_png_bytes((255, 255, 0)))

    with TestClient(create_app()) as client:
        listing = client.get(BASE, headers=AUTH).json()
        entries = [item for item in listing["items"] if item["source"] == "collect_inbox"]
        assert [item["name"] for item in entries] == ["old.png"]
        query = entries[0]["preview_query"]
        assert query and query.startswith("collect_inbox_file="), entries[0]

        preview = client.get(
            f"/api/media-preview?w=256&{query}", headers=AUTH
        )
        assert preview.status_code == 200, preview.text
        assert preview.headers["content-type"].startswith("image/")

        # 无主体上下文请求 collect_inbox_file：失败关闭（403，绝不返回字节）。
        from gw.asset_library import inbox as inbox_module

        with pytest.raises(Exception) as excinfo:
            inbox_module.resolve_preview_request(
                {"collect_inbox_file": "old.png"}
            )
        assert excinfo.value.status_code == 403
        assert excinfo.value.code == "INBOX_OWNER_REQUIRED"
