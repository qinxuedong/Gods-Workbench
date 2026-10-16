"""Windows 数据目录锁文件不得进入持久化机密扫描。"""
from __future__ import annotations

from media_doubles import iter_persisted_files


def test_persisted_file_scan_excludes_data_dir_lock(tmp_path):
    """锁文件由运行中的 TestClient 独占，扫描应只读取业务持久化文件。"""
    data_root = tmp_path / "data"
    task_file = data_root / "tasks" / "task.json"
    task_file.parent.mkdir(parents=True)
    task_file.write_bytes(b"task payload")
    lock_file = data_root / ".gw-data.lock"
    lock_file.write_bytes(b"pid")

    files = iter_persisted_files(data_root)

    assert files == [task_file]
    assert lock_file not in files
