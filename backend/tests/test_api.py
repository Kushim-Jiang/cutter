"""管线与 API 端到端测试（使用合成 PDF，不依赖外部数据仓库）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.main import app
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("grid_pdf")


@pytest.fixture(scope="module", autouse=True)
def _configure_hd(grid_pdf: Path) -> None:
    client = TestClient(app)
    response = client.put(
        "/api/settings",
        json={
            "books": {
                "hd": {
                    "pdf": str(grid_pdf),
                    "dpi": 72,
                    "volumes": {
                        "1": {
                            "pdf": str(grid_pdf),
                            "first_page": 1,
                            "last_page": 1,
                            "offset": 0,
                            "label": "???",
                        }
                    },
                }
            },
        },
    )
    assert response.status_code == 200


def test_health(client: TestClient):
    data = client.get("/api/health").json()
    assert data["status"] == "ok"


def test_books_listed(client: TestClient):
    books = {b["id"]: b for b in client.get("/api/books").json()["books"]}
    assert set(books) == {"zihai", "hzh", "kx", "hd"}
    assert books["hd"]["status"]["available"] is True


def test_pages_and_meta(client: TestClient):
    pages = client.get("/api/books/hd/pages").json()
    assert pages["total"] == 1
    meta = client.get("/api/books/hd/pages/1:1/meta").json()
    assert (meta["width"], meta["height"]) == (400, 600)
    assert meta["saved"] is False


def test_preview_and_crop(client: TestClient):
    preview = client.get("/api/books/hd/pages/1:1/preview?max_width=200")
    assert preview.status_code == 200
    assert preview.content[:3] == b"\xff\xd8\xff"
    crop = client.get("/api/books/hd/pages/1:1/crop?x=80&y=100&w=60&h=60&max_width=200")
    assert crop.status_code == 200
    assert crop.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_detect_save_export_flow(client: TestClient, tmp_path: Path):
    detect = client.post(
        "/api/books/hd/pages/1:1/detect",
        json={
            "params": {"w_min": 50, "w_max": 100, "h_min": 50, "h_max": 100, "deskew": False},
            "save": True,
        },
    )
    assert detect.status_code == 200, detect.text
    payload = detect.json()
    assert payload["count"] == 4

    saved = client.get("/api/books/hd/pages/1:1/boxes").json()
    assert saved["saved"] is True and len(saved["boxes"]) == 4

    out = client.post("/api/books/hd/pages/1:1/export", json={"params": {}, "out_dir": str(tmp_path)})
    assert out.status_code == 200, out.text
    files = out.json()["files"]
    assert len(files) == 4
    assert all(Path(f["file"]).exists() for f in files)
    assert files[0]["file"].endswith("H1-0001_001.png")


def test_export_without_boxes_conflicts(client: TestClient, tmp_path: Path):
    response = client.post(
        "/api/books/hd/pages/1:1/export", json={"params": {}, "out_dir": str(tmp_path / "none")}
    )
    assert response.status_code in (200, 409)


def test_put_boxes_manual(client: TestClient):
    response = client.put(
        "/api/books/hd/pages/1:1/boxes",
        json={
            "boxes": [{"x": 10, "y": 10, "w": 20, "h": 20}],
            "width": 400,
            "height": 600,
            "extra": {"note": "manual"},
        },
    )
    assert response.status_code == 200
    saved = client.get("/api/books/hd/pages/1:1/boxes").json()
    assert saved["extra"] == {"note": "manual"}
    assert saved["boxes"][0]["source"] == "manual"


def test_job_detect_runs(client: TestClient):
    created = client.post(
        "/api/jobs/detect",
        json={
            "book": "hd",
            "pages": ["1"],
            "params": {"w_min": 50, "w_max": 100, "h_min": 50, "h_max": 100, "deskew": False},
        },
    )
    assert created.status_code == 200, created.text
    job_id = created.json()["id"]

    import time

    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}?result=1").json()
        if job["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert job["status"] == "done", job
    assert job["done"] == 1
    assert job["result"][0]["count"] == 4


def test_unknown_book_404(client: TestClient):
    assert client.get("/api/books/nope/pages").status_code == 404


def test_refine_boxes_endpoint(client: TestClient):
    client.put(
        "/api/books/hd/pages/1:1/boxes",
        json={
            "boxes": [{"x": 70, "y": 90, "w": 80, "h": 80}],
            "width": 400,
            "height": 600,
        },
    )
    response = client.post(
        "/api/books/hd/pages/1:1/refine",
        json={
            "boxes": [{"x": 70, "y": 90, "w": 80, "h": 80}],
            "params": {"margin": 0},
        },
    )
    assert response.status_code == 200, response.text
    box = response.json()["boxes"][0]
    # 合成页上的方块是 (80,100)-(140,160)，收紧后应贴到墨迹上
    assert box["x"] <= 82 and box["y"] <= 102
    assert box["x"] + box["w"] >= 138


def test_page_state_and_nav(client: TestClient):
    done = client.post("/api/books/hd/pages/1:1/state", json={"done": True})
    assert done.status_code == 200
    meta = client.get("/api/books/hd/pages/1:1/meta").json()
    assert meta["done"] is True

    # 唯一一页已完成 → 没有「下一未完成」
    nav = client.get("/api/books/hd/nav", params={"after": "1:1", "mode": "undone"}).json()
    assert nav["found"] is False

    suspect = client.post("/api/books/hd/pages/1:1/state", json={"suspect": True})
    assert suspect.json()["state"]["suspect"] is True
    nav = client.get("/api/books/hd/nav", params={"after": "", "mode": "suspect"}).json()
    assert nav == {"page": "1:1", "found": True}


def test_nav_review_mode(client: TestClient):
    # 存一页带 review 标记的框，nav?mode=review 应该找得到它
    client.put(
        "/api/books/hd/pages/1:1/boxes",
        json={
            "boxes": [{"x": 80, "y": 100, "w": 60, "h": 60}],
            "width": 400,
            "height": 600,
            "extra": {"review": [0]},
        },
    )
    nav = client.get("/api/books/hd/nav", params={"after": "", "mode": "review"}).json()
    assert nav == {"page": "1:1", "found": True}


def test_bad_page_id_is_404(client: TestClient):
    # 康熙页号必须是「分册:页」
    assert client.get("/api/books/kx/pages/452/meta").status_code == 404
    assert client.get("/api/books/hd/pages/not-a-page/meta").status_code == 404
