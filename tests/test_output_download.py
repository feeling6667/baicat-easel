"""内容库「下载到本地」：单文件原样下载，文件夹打包成 zip（含穿越/受保护目录）。"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "web"))

import web.app as web  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    outs = tmp_path / "outputs"
    (outs / "proj-a" / "assets").mkdir(parents=True)
    (outs / "proj-a" / "note.md").write_text("hello", encoding="utf-8")
    (outs / "proj-a" / "assets" / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (outs / "proj-a" / ".hidden").write_text("secret", encoding="utf-8")
    (outs / "_login").mkdir()
    (outs / "_login" / "xhs.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(web, "OUTPUTS_DIR", outs)
    with TestClient(web.app, base_url="http://127.0.0.1:7860",
                    client=("127.0.0.1", 51234)) as c:
        yield c, outs


def test_download_single_file_as_attachment(client):
    c, _ = client
    r = c.get("/api/output/download/proj-a/note.md")
    assert r.status_code == 200, r.text
    assert r.content == b"hello"
    disposition = r.headers["content-disposition"]
    assert "attachment" in disposition and "note.md" in disposition


def test_download_folder_returns_zip_of_contents(client):
    c, _ = client
    r = c.get("/api/output/download/proj-a")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert "proj-a.zip" in r.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        names = sorted(z.namelist())
        assert names == ["assets/pic.png", "note.md"]          # 隐藏文件不入包
        assert z.read("note.md") == b"hello"


def test_download_rejects_protected_folder_and_traversal(client):
    c, _ = client
    assert c.get("/api/output/download/_login").status_code == 403
    assert c.get("/api/output/download/../outside").status_code in (403, 404)
    assert c.get("/api/output/download/missing.md").status_code == 404


def test_download_leaves_no_temp_archive(client, tmp_path):
    c, _ = client
    r = c.get("/api/output/download/proj-a")
    assert r.status_code == 200
    leftover = [p for p in Path(web.tempfile.gettempdir()).glob("easel-download-*")]
    assert all(not (p / "proj-a.zip").exists() for p in leftover)
