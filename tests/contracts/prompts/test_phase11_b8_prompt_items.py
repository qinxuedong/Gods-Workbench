# -*- coding: utf-8 -*-
"""Phase 12 A3 提示词条目契约测试（真实落盘）。

覆盖：401 / 403 / 创建写后读回 / 更新 CAS / 删除后不再返回 / 批量删除 /
不生成任何提示词文本的证据边界。
"""

import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

from fastapi.testclient import TestClient

from gw.api.app import create_app

ROOT = Path(__file__).resolve().parents[3]
C = ROOT / "docs/contracts/PROMPT-LIBRARY-B8-ITEM-INTERFACE-CATALOG.yaml"
F = ROOT / "docs/fixtures"
A = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
R = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "readonly"}


def ps():
    return [(m, p) for m, p in re.findall(r"method: (\w+), path: (\S+)", C.read_text(encoding="utf-8"))]


def norm(p):
    return p[:-1] if p.endswith("}") and not p.endswith("{p}") else p


def real(p):
    return norm(p).replace("{p}", "pitem_0001")


def test_contract():
    x = ps()
    assert len(x) == 5 and len({p for _, p in x}) == 3
    assert json.loads((F / "phase11-b8-boundary.json").read_text(encoding="utf-8"))["path_count"] == 3
    text = C.read_text(encoding="utf-8")
    assert "不生成任何提示词文本" in text


def test_auth_boundary():
    with TestClient(create_app()) as c:
        for m, p in ps():
            u = real(p)
            payload = {} if m in ("POST", "PATCH", "DELETE") else None
            r = c.request(m, u, json=payload) if payload is not None else c.request(m, u)
            assert r.status_code == 401, "%s %s" % (m, u)
            if m in ("POST", "PATCH", "DELETE"):
                assert c.request(m, u, headers=R, json=payload).status_code == 403


def test_create_read_update_delete_roundtrip():
    """真实父库/分类存在时，条目创建、读取、更新与删除可观察。"""
    with TestClient(create_app()) as c:
        library_id = _create_library(c, "条目父库")
        category_id = _create_category(c, library_id, "条目分类")
        created_response = c.post("/api/prompt-libraries/items", headers=A, json={
            "library_id": library_id, "category_id": category_id,
            "name": "镜头描述", "text": "调用方真实提交的文本",
        })
        assert created_response.status_code == 201, created_response.text
        created = created_response.json()["item"]
        iid = created["item_id"]
        assert re.fullmatch(r"pitem_\d{4}", iid)
        assert created["text"] == "调用方真实提交的文本"

        listed = c.get("/api/prompt-libraries/items?library_id=" + library_id, headers=A).json()
        assert any(item["item_id"] == iid for item in listed["items"])
        assert listed["data_gaps"] == []

        patched = c.patch("/api/prompt-libraries/items/%s" % iid, headers=A,
                          json={"name": "新名称", "expected_version": 1})
        assert patched.status_code == 200, patched.text
        assert patched.json()["item"]["name"] == "新名称" and patched.json()["item"]["version"] == 2

        assert c.delete("/api/prompt-libraries/items/%s" % iid, headers=A).status_code == 200
        after = c.get("/api/prompt-libraries/items", headers=A).json()
        assert all(item["item_id"] != iid for item in after["items"])
def test_bulk_delete_only_removes_existing_ids():
    """批量删除只删除真实存在的 ID，不伪造删除成功。"""
    with TestClient(create_app()) as c:
        library_id = _create_library(c, "批量删除父库")
        first = c.post("/api/prompt-libraries/items", headers=A,
                       json={"library_id": library_id, "name": "A"}).json()["item"]["item_id"]
        second = c.post("/api/prompt-libraries/items", headers=A,
                        json={"library_id": library_id, "name": "B"}).json()["item"]["item_id"]
        res = c.post("/api/prompt-libraries/items/delete", headers=A,
                     json={"ids": [first, "pitem_9999"]}).json()
        assert res["deleted"] == [first]
        assert res["deleted_count"] == 1
        remaining = {item["item_id"] for item in c.get("/api/prompt-libraries/items", headers=A).json()["items"]}
        assert second in remaining and first not in remaining
def test_not_found_and_cas_semantics():
    with TestClient(create_app()) as c:
        assert c.patch("/api/prompt-libraries/items/pitem_9999", headers=A,
                       json={"name": "x"}).status_code == 404
        assert c.delete("/api/prompt-libraries/items/pitem_9999", headers=A).status_code == 404
        library_id = _create_library(c, "CAS父库")
        iid = c.post("/api/prompt-libraries/items", headers=A,
                     json={"library_id": library_id, "name": "C"}).json()["item"]["item_id"]
        conflict = c.patch("/api/prompt-libraries/items/%s" % iid, headers=A,
                           json={"name": "D", "expected_version": 99})
        assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "VERSION_CONFLICT"


def _create_library(client: TestClient, name: str) -> str:
    response = client.post("/api/prompt-libraries", headers=A, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["prompt_library"]["library_id"]


def _create_category(client: TestClient, library_id: str, name: str) -> str:
    response = client.post("/api/prompt-libraries/categories", headers=A,
                           json={"library_id": library_id, "name": name, "expected_version": 1})
    assert response.status_code == 201, response.text
    return response.json()["category"]["category_id"]


def test_item_parent_and_category_references_fail_closed():
    """未知/已删父库及未知/跨库分类都不能产生孤儿条目。"""
    with TestClient(create_app()) as c:
        first_library = _create_library(c, "父库一")
        second_library = _create_library(c, "父库二")
        first_category = _create_category(c, first_library, "分类一")
        second_category = _create_category(c, second_library, "分类二")

        unknown_parent = c.post("/api/prompt-libraries/items", headers=A,
                                json={"library_id": "plib_9999", "name": "孤儿"})
        assert unknown_parent.status_code == 404
        assert unknown_parent.json()["detail"]["code"] == "LIBRARY_NOT_FOUND"

        unknown_category = c.post("/api/prompt-libraries/items", headers=A,
                                  json={"library_id": first_library, "category_id": "pcat_9999", "name": "分类孤儿"})
        assert unknown_category.status_code == 404
        assert unknown_category.json()["detail"]["code"] == "CATEGORY_NOT_FOUND"

        cross_library = c.post("/api/prompt-libraries/items", headers=A,
                               json={"library_id": first_library, "category_id": second_category, "name": "跨库"})
        assert cross_library.status_code == 404
        assert cross_library.json()["detail"]["code"] == "CATEGORY_NOT_FOUND"

        created = c.post("/api/prompt-libraries/items", headers=A,
                         json={"library_id": first_library, "category_id": first_category, "name": "受保护条目"})
        assert created.status_code == 201, created.text
        item_id = created.json()["item"]["item_id"]
        category_delete = c.delete("/api/prompt-libraries/categories/%s?expected_version=1" % first_category,
                                   headers=A)
        assert category_delete.status_code == 409
        assert category_delete.json()["detail"]["code"] == "CATEGORY_NOT_EMPTY"
        library_delete = c.delete("/api/prompt-libraries/%s?expected_version=2" % first_library, headers=A)
        assert library_delete.status_code == 409
        assert library_delete.json()["detail"]["code"] == "LIBRARY_NOT_EMPTY"
        assert any(item["item_id"] == item_id for item in c.get("/api/prompt-libraries/items", headers=A).json()["items"])

        deletable = _create_library(c, "删除后不能挂接")
        removed = c.delete("/api/prompt-libraries/%s?expected_version=1" % deletable, headers=A)
        assert removed.status_code == 200, removed.text
        after_delete = c.post("/api/prompt-libraries/items", headers=A,
                              json={"library_id": deletable, "name": "不得挂接"})
        assert after_delete.status_code == 404
        assert after_delete.json()["detail"]["code"] == "LIBRARY_NOT_FOUND"


def test_orphan_legacy_parent_ids_are_preserved_but_never_reused(tmp_path, monkeypatch):
    """升级保留孤儿条目原引用，并从遗留父 ID 提取高水位避免复用。"""
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "prompt-data"))

    from gw.core import storage
    from gw.prompt_library.items_repository import list_items
    from gw.prompt_library.models import PromptCategoryCreateRequest, PromptLibraryCreateRequest
    from gw.prompt_library.service import PromptLibraryService

    legacy_item = {
        "item_id": "pitem_0007",
        "library_id": "plib_0042",
        "category_id": "pcat_0017",
        "name": "legacy-orphan",
        "text": "caller-provided legacy text",
        "version": 1,
    }
    storage.JsonState("prompt_library", lambda: {"items": {}}).write({
        "revision": 1,
        "items": {legacy_item["item_id"]: legacy_item},
    })

    service = PromptLibraryService()
    library, _ = service.create_library(PromptLibraryCreateRequest(name="post-upgrade"))
    category, _ = service.create_category(PromptCategoryCreateRequest(
        library_id=library.library_id, name="post-upgrade-category", expected_version=library.version,
    ))
    orphan = list_items("plib_0042")

    assert library.library_id == "plib_0043"
    assert category.category_id == "pcat_0018"
    assert orphan["items"] == [{
        "item_id": "pitem_0007", "library_id": "plib_0042", "category_id": "pcat_0017",
        "name": "legacy-orphan", "tags": [], "version": 1, "created_at": None,
        "updated_at": None, "text": "caller-provided legacy text",
    }]
    assert orphan["data_gaps"] == [{"item_id": "pitem_0007", "code": "LIBRARY_NOT_FOUND"}]


def test_prompt_library_and_items_survive_real_process_restart_without_id_reuse(tmp_path):
    """独立进程重启后父树与条目一致，新库不复用旧 ID、不读取旧条目。"""
    data_dir = tmp_path / "prompt-data"
    env = os.environ.copy()
    env["GW_DATA_DIR"] = str(data_dir)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    create_code = r"""
import json
from gw.prompt_library.models import PromptCategoryCreateRequest, PromptLibraryCreateRequest
from gw.prompt_library.service import PromptLibraryService
from gw.prompt_library import items_repository
service = PromptLibraryService()
library, _ = service.create_library(PromptLibraryCreateRequest(name="旧库"))
category, _ = service.create_category(PromptCategoryCreateRequest(library_id=library.library_id, name="旧分类", expected_version=library.version))
item = items_repository.create_item({"library_id": library.library_id, "category_id": category.category_id, "name": "旧条目", "text": "调用方文本"})["item"]
print(json.dumps({"library_id": library.library_id, "category_id": category.category_id, "item_id": item["item_id"]}, ensure_ascii=False))
"""
    created_proc = subprocess.run([sys.executable, "-c", create_code], cwd=ROOT, env=env,
                                  capture_output=True, text=True, timeout=15)
    assert created_proc.returncode == 0, created_proc.stderr
    old = json.loads(created_proc.stdout)
    assert old["library_id"] == "plib_0001" and old["category_id"] == "pcat_0001"

    restart_code = r"""
import json
from gw.prompt_library.models import PromptLibraryCreateRequest
from gw.prompt_library.service import PromptLibraryService
from gw.prompt_library import items_repository
service = PromptLibraryService()
before = service.get_snapshot()
new_library, snapshot = service.create_library(PromptLibraryCreateRequest(name="新库"))
old_items = items_repository.list_items("plib_0001")["items"]
new_items = items_repository.list_items(new_library.library_id)["items"]
print(json.dumps({"libraries": [item.library_id for item in before.libraries], "new_library_id": new_library.library_id, "old_items": old_items, "new_items": new_items, "category_ids": [category.category_id for item in before.libraries for category in item.categories]}, ensure_ascii=False))
"""
    restarted_proc = subprocess.run([sys.executable, "-c", restart_code], cwd=ROOT, env=env,
                                    capture_output=True, text=True, timeout=15)
    assert restarted_proc.returncode == 0, restarted_proc.stderr
    restarted = json.loads(restarted_proc.stdout)
    assert restarted["libraries"] == [old["library_id"]]
    assert restarted["category_ids"] == [old["category_id"]]
    assert restarted["new_library_id"] == "plib_0002"
    assert len(restarted["old_items"]) == 1
    assert restarted["old_items"][0]["item_id"] == old["item_id"]
    assert restarted["new_items"] == []


class _ObservedPromptDomainLock:
    """竞争测试用的可观测 RLock，记录对手线程何时尝试进入域锁。"""

    def __init__(self, observed_thread_names):
        self._lock = threading.RLock()
        self._metadata_lock = threading.Lock()
        self._owner_ident = None
        self._owner_name = None
        self._depth = 0
        self.attempted = {name: threading.Event() for name in observed_thread_names}

    @property
    def owner_name(self):
        with self._metadata_lock:
            return self._owner_name

    def __enter__(self):
        name = threading.current_thread().name
        attempted = self.attempted.get(name)
        if attempted is not None:
            attempted.set()
        self._lock.acquire()
        ident = threading.get_ident()
        with self._metadata_lock:
            if self._owner_ident == ident:
                self._depth += 1
            else:
                self._owner_ident = ident
                self._owner_name = name
                self._depth = 1
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        with self._metadata_lock:
            self._depth -= 1
            if self._depth == 0:
                self._owner_ident = None
                self._owner_name = None
        self._lock.release()


def _run_prompt_reference_write_race(monkeypatch, writer_call, delete_call):
    """在父引用校验后暂停写入，确保对手操作确实尝试竞争共享域锁。"""
    from gw.prompt_library import coordination, items_repository

    writer_name = "reference-writer"
    delete_name = "parent-delete"
    domain_lock = _ObservedPromptDomainLock([delete_name])
    monkeypatch.setattr(coordination, "PROMPT_LIBRARY_DOMAIN_LOCK", domain_lock)
    original_validate = items_repository._validate_parent_refs
    validated = threading.Event()
    continue_write = threading.Event()
    writer_result = {}
    delete_result = {}

    def validate_then_pause(library_id, category_id=None):
        result = original_validate(library_id, category_id)
        if threading.current_thread().name == writer_name:
            validated.set()
            if not continue_write.wait(5):
                raise TimeoutError("等待竞争测试释放写入超时")
        return result

    monkeypatch.setattr(items_repository, "_validate_parent_refs", validate_then_pause)

    def capture(target, result):
        try:
            result["value"] = target()
        except BaseException as exc:  # 在线程结果中回传断言所需的服务异常。
            result["error"] = exc

    writer = threading.Thread(target=capture, args=(writer_call, writer_result), name=writer_name)
    deleter = threading.Thread(target=capture, args=(delete_call, delete_result), name=delete_name)
    writer.start()
    try:
        assert validated.wait(3), "写入未完成父引用校验"
        assert domain_lock.owner_name == writer_name, "父引用校验到提交期间未持有提示词库域锁"
        deleter.start()
        assert domain_lock.attempted[delete_name].wait(3), "删除线程未尝试进入提示词库域锁"
    finally:
        continue_write.set()
        writer.join(5)
        if deleter.ident is not None:
            deleter.join(5)

    assert not writer.is_alive(), "条目写入发生死锁"
    assert not deleter.is_alive(), "父项删除发生死锁"
    assert "error" not in writer_result, repr(writer_result.get("error"))
    assert "value" in writer_result
    return writer_result["value"], delete_result.get("error")


def test_library_delete_and_item_create_share_one_atomic_domain_lock(monkeypatch):
    """条目先完成父库校验并提交后，竞争删除必须因非空返回 409。"""
    from gw.core.errors import CleanroomException
    from gw.prompt_library import items_repository
    from gw.prompt_library.models import PromptLibraryCreateRequest
    from gw.prompt_library.service import PromptLibraryService

    service = PromptLibraryService()
    library, _ = service.create_library(PromptLibraryCreateRequest(name="并发父库"))
    created, delete_error = _run_prompt_reference_write_race(
        monkeypatch,
        lambda: items_repository.create_item({
            "library_id": library.library_id, "name": "并发条目", "text": "真实文本",
        }),
        lambda: service.delete_library(library.library_id, expected_version=library.version),
    )

    assert created["item"]["library_id"] == library.library_id
    assert isinstance(delete_error, CleanroomException)
    assert delete_error.status_code == 409
    assert delete_error.code == "LIBRARY_NOT_EMPTY"
    assert library.library_id in {item.library_id for item in service.get_snapshot().libraries}
    listing = items_repository.list_items(library.library_id)
    assert len(listing["items"]) == 1
    assert listing["items"][0]["item_id"] == created["item"]["item_id"]
    assert listing["data_gaps"] == []


def test_category_delete_and_item_reclassification_share_one_atomic_domain_lock(monkeypatch):
    """改分类先完成引用校验并提交后，竞争分类删除必须因非空返回 409。"""
    from gw.core.errors import CleanroomException
    from gw.prompt_library import items_repository
    from gw.prompt_library.models import PromptCategoryCreateRequest, PromptLibraryCreateRequest
    from gw.prompt_library.service import PromptLibraryService

    service = PromptLibraryService()
    library, _ = service.create_library(PromptLibraryCreateRequest(name="并发分类父库"))
    category, _ = service.create_category(PromptCategoryCreateRequest(
        library_id=library.library_id, name="目标分类", expected_version=library.version,
    ))
    item = items_repository.create_item({
        "library_id": library.library_id, "name": "待改分类", "text": "真实文本",
    })["item"]

    updated, delete_error = _run_prompt_reference_write_race(
        monkeypatch,
        lambda: items_repository.update_item(item["item_id"], {
            "category_id": category.category_id, "expected_version": item["version"],
        }),
        lambda: service.delete_category(category.category_id, expected_version=category.version),
    )

    assert updated["item"]["category_id"] == category.category_id
    assert updated["item"]["version"] == item["version"] + 1
    assert isinstance(delete_error, CleanroomException)
    assert delete_error.status_code == 409
    assert delete_error.code == "CATEGORY_NOT_EMPTY"
    snapshot = service.get_snapshot()
    assert any(
        child.category_id == category.category_id
        for parent in snapshot.libraries for child in parent.categories
    )
    listing = items_repository.list_items(library.library_id)
    assert len(listing["items"]) == 1
    assert listing["items"][0]["category_id"] == category.category_id
    assert listing["data_gaps"] == []
