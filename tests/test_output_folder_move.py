"""内容库：新建文件夹 + 把文件移动到另一个文件夹（含穿越/重名/受保护目录）。"""
from __future__ import annotations

import sys
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
    (outs / "proj-b").mkdir()
    (outs / "_login").mkdir()
    monkeypatch.setattr(web, "OUTPUTS_DIR", outs)
    with TestClient(web.app, base_url="http://127.0.0.1:7860",
                    client=("127.0.0.1", 51234)) as c:
        yield c, outs


def test_mkdir_at_root_and_nested(client):
    c, outs = client
    r = c.post("/api/output/mkdir", json={"parent": "", "name": "新项目"})
    assert r.status_code == 200, r.text
    assert r.json()["path"] == "新项目"
    assert (outs / "新项目").is_dir()

    r = c.post("/api/output/mkdir", json={"parent": "proj-a", "name": "镜头素材"})
    assert r.status_code == 200, r.text
    assert r.json()["path"] == "proj-a/镜头素材"
    assert (outs / "proj-a" / "镜头素材").is_dir()


@pytest.mark.parametrize("name", ["", "   ", ".", "..", "_系统", ".隐藏", "a/b", "a\\b", "bad\x00name"])
def test_mkdir_rejects_bad_names(client, name):
    c, _ = client
    r = c.post("/api/output/mkdir", json={"parent": "", "name": name})
    assert r.status_code == 400, r.text


def test_mkdir_rejects_duplicate_and_protected_parent(client):
    c, outs = client
    assert c.post("/api/output/mkdir", json={"parent": "proj-b", "name": "proj-a"}).status_code == 200
    r = c.post("/api/output/mkdir", json={"parent": "", "name": "proj-a"})
    assert r.status_code == 409
    assert not (outs / "proj-a" / "proj-a").exists()

    r = c.post("/api/output/mkdir", json={"parent": "_login", "name": "x"})
    assert r.status_code == 403
    assert not (outs / "_login" / "x").exists()


def test_mkdir_rejects_traversal(client):
    c, _ = client
    assert c.post("/api/output/mkdir", json={"parent": "../outside", "name": "x"}).status_code in (403, 404)
    assert c.post("/api/output/mkdir", json={"parent": "", "name": "../../evil"}).status_code == 400


def test_move_file_between_folders(client):
    c, outs = client
    r = c.post("/api/output/move", json={"path": "proj-a/note.md", "dest": "proj-b"})
    assert r.status_code == 200, r.text
    assert r.json()["path"] == "proj-b/note.md"
    assert not (outs / "proj-a" / "note.md").exists()
    assert (outs / "proj-b" / "note.md").read_text(encoding="utf-8") == "hello"


def test_move_keeps_both_when_name_conflicts(client):
    c, outs = client
    (outs / "proj-b" / "note.md").write_text("other", encoding="utf-8")
    r = c.post("/api/output/move", json={"path": "proj-a/note.md", "dest": "proj-b"})
    assert r.status_code == 200, r.text
    assert r.json()["path"] == "proj-b/note (2).md"
    assert (outs / "proj-b" / "note.md").read_text(encoding="utf-8") == "other"
    assert (outs / "proj-b" / "note (2).md").read_text(encoding="utf-8") == "hello"


def test_move_folder_into_another_folder(client):
    c, outs = client
    r = c.post("/api/output/move", json={"path": "proj-a/assets", "dest": "proj-b"})
    assert r.status_code == 200, r.text
    assert r.json()["path"] == "proj-b/assets"
    assert (outs / "proj-b" / "assets").is_dir()
    assert not (outs / "proj-a" / "assets").exists()


def test_move_rejects_same_folder_root_protected_and_cycles(client):
    c, outs = client
    same = c.post("/api/output/move", json={"path": "proj-a/note.md", "dest": "proj-a"})
    assert same.status_code == 200 and same.json().get("unchanged") is True

    assert c.post("/api/output/move", json={"path": "proj-a/note.md", "dest": ""}).status_code == 400
    assert c.post("/api/output/move", json={"path": "proj-a/note.md", "dest": "_login"}).status_code == 403
    assert c.post("/api/output/move",
                  json={"path": "proj-a", "dest": "proj-a/assets"}).status_code == 400
    assert c.post("/api/output/move",
                  json={"path": "proj-a", "dest": "proj-a"}).status_code == 400
    assert c.post("/api/output/move", json={"path": "missing.md", "dest": "proj-b"}).status_code == 404
    assert c.post("/api/output/move",
                  json={"path": "../escape.md", "dest": "proj-b"}).status_code in (403, 404)
    # 一个都不能被移走
    assert (outs / "proj-a" / "note.md").is_file()
    assert (outs / "proj-a" / "assets").is_dir()


def test_move_protected_source_rejected(client):
    c, outs = client
    (outs / "_login" / "xhs.json").write_text("{}", encoding="utf-8")
    r = c.post("/api/output/move", json={"path": "_login/xhs.json", "dest": "proj-b"})
    assert r.status_code == 403
    assert (outs / "_login" / "xhs.json").is_file()
