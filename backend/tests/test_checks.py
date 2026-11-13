"""合法性检查与编辑历史。"""

from __future__ import annotations

from app import checks
from app.core.box import Box
from fastapi.testclient import TestClient


def test_geometry_checks_find_problems():
    boxes = [
        Box(10, 10, 60, 60),
        Box(12, 12, 60, 60),  # 与第一个几乎重合
        Box(500, 500, 40, 40),  # 出界（页面 400×600）
        Box(200, 300, 4, 4),  # 过小
    ]
    issues = checks.check_geometry(boxes, 400, 600)
    kinds = {i["kind"] for i in issues}
    assert "duplicate" in kinds
    assert "out-of-bounds" in kinds
    assert "too-small" in kinds


def test_geometry_blank_box():
    import numpy as np

    ink = np.zeros((200, 200), np.uint8)
    ink[50:120, 50:120] = 1
    issues = checks.check_geometry([Box(0, 0, 40, 40), Box(50, 50, 70, 70)], 200, 200, ink)
    assert any(i["kind"] == "blank-box" for i in issues)
    assert not any(i["kind"] == "blank-box" and i["boxes"] == [1] for i in issues)


def test_hzh_numbering_reports_gap_and_duplicate(monkeypatch):
    rows = [
        {"seq": 1, "entry": 10, "radical": "一", "strokes": 1, "code": "4E00"},
        {"seq": 2, "entry": 13, "radical": "一", "strokes": 2, "code": "4E8C"},  # 跳号
        {"seq": 3, "entry": 13, "radical": "", "strokes": 0, "code": "zz"},  # 重复 + 字段坏
    ]
    monkeypatch.setattr(checks, "load_hzh_fields", lambda: {7: rows})
    boxes = [Box(0, 0, 50, 50), Box(0, 100, 50, 50), Box(0, 200, 50, 50)]

    issues = checks.check_hzh_numbering("7", boxes)
    kinds = {i["kind"] for i in issues}
    assert {"entry-gap", "entry-duplicate", "radical-missing", "strokes-range", "code-format"} <= kinds

    gap = next(i for i in issues if i["kind"] == "entry-gap")
    assert gap["detail"] == {"from": 10, "to": 13}
    assert "缺 2 个" in gap["message"]


def test_check_endpoint_on_synthetic_page(client: TestClient, grid_pdf):
    client.put(
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
    client.put(
        "/api/books/hd/pages/1:1/boxes",
        json={
            "boxes": [
                {"x": 80, "y": 100, "w": 60, "h": 60},
                {"x": 82, "y": 102, "w": 60, "h": 60},  # 重复
                {"x": 500, "y": 700, "w": 50, "h": 50},  # 出界
            ],
            "width": 400,
            "height": 600,
        },
    )
    report = client.get("/api/books/hd/pages/1:1/check").json()
    kinds = {i["kind"] for i in report["issues"]}
    assert "duplicate" in kinds and "out-of-bounds" in kinds
    assert report["stats"]["boxes"] == 3


def test_history_snapshots_and_restore(client: TestClient):
    client.put(
        "/api/books/hd/pages/1:1/boxes",
        json={
            "boxes": [{"x": 80, "y": 100, "w": 60, "h": 60}],
            "width": 400,
            "height": 600,
        },
    )
    client.put(
        "/api/books/hd/pages/1:1/boxes",
        json={
            "boxes": [{"x": 80, "y": 100, "w": 60, "h": 60}, {"x": 240, "y": 100, "w": 60, "h": 60}],
            "width": 400,
            "height": 600,
        },
    )
    history = client.get("/api/books/hd/pages/1:1/history").json()
    assert len(history["items"]) >= 2
    assert history["items"][0]["count"] == 2

    oldest = history["items"][-1]
    restored = client.post(f"/api/books/hd/pages/1:1/history/{oldest['id']}/restore").json()
    assert restored["count"] == oldest["count"]
    assert restored["boxes"]


def test_history_missing_snapshot_404(client: TestClient):
    assert client.get("/api/books/hd/pages/1:1/history/nope").status_code == 404
