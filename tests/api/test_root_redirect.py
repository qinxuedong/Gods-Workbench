"""QIN-197 根入口与默认启动页回归。"""

from pathlib import Path


def test_root_redirects_to_home(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/static/pages/index.html"


def test_cli_browser_default_points_to_home(repo_root: Path):
    source = (repo_root / "run.py").read_text(encoding="utf-8")

    assert 'browser_url = f"http://{host}:{port}/static/pages/index.html"' in source
