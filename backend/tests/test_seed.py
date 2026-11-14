"""上游定稿数据（seed）的读取与优先级。"""

from __future__ import annotations

import json
from pathlib import Path

from app.pipelines.hd import HdPipeline
from app.pipelines.hzh import HzhPipeline
from app.pipelines.kx import KxPipeline
from app.pipelines.zihai import ZihaiPipeline


def _write(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_zihai_seed_from_edits(tmp_path):
    _write(
        tmp_path / "edits.json",
        {"1": [{"x": 10, "y": 20, "w": 30, "h": 40}, {"x": 60, "y": 20, "w": 30, "h": 40}]},
    )
    _write(tmp_path / "progress.json", {"1": "2026-11-02T10:22:51.571Z"})
    pipeline = ZihaiPipeline({"seed_dir": str(tmp_path), "pdf": ""})

    seeded = pipeline.seed_boxes("1:84")  # offset 83 → 印刷页 1
    assert seeded is not None
    boxes, extra = seeded
    assert [(b.x, b.y, b.w, b.h) for b in boxes] == [(10, 20, 30, 40), (60, 20, 30, 40)]
    assert extra["side"] == round(1.30 * 40)
    assert pipeline.seed_done("1:84") is True
    assert pipeline.seed_done("1:85") is False


def test_zihai_seed_volume2_uses_edits_v2(tmp_path):
    _write(tmp_path / "edits_v2.json", {"1": [{"x": 5, "y": 6, "w": 7, "h": 8}]})
    volumes = {"2": {"pdf": "", "first_page": 80, "last_page": 90, "offset": 79, "title": "卷 2"}}
    pipeline = ZihaiPipeline({"seed_dir": str(tmp_path), "volumes": volumes})

    seeded = pipeline.seed_boxes("2:80")
    assert seeded is not None
    assert seeded[0][0].as_dict()["x"] == 5


def test_hzh_seed_from_zi_zj(tmp_path):
    rows = [
        {
            "entry": 2,
            "ziid": "ZI0001",
            "page": 452,
            "seq": 2,
            "col": 1,
            "x": 165,
            "y": 2710,
            "w": 128,
            "h": 95,
        },
        {
            "entry": 1,
            "ziid": "ZI0000",
            "page": 452,
            "seq": 1,
            "col": 1,
            "x": 158,
            "y": 907,
            "w": 133,
            "h": 23,
        },
    ]
    _write(tmp_path / "ZI.ndjson", "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    _write(
        tmp_path / "ZJ.ndjson",
        json.dumps(
            {"entry": 65537, "ziid": "ZJ0000", "page": 900, "seq": 1, "x": 1, "y": 2, "w": 3, "h": 4},
            ensure_ascii=False,
        )
        + "\n",
    )
    pipeline = HzhPipeline({"seed_dir": str(tmp_path)})

    seeded = pipeline.seed_boxes("452")
    assert seeded is not None
    boxes, extra = seeded
    # 按 entry 排序，且带 Z 编号
    assert [b.pos for b in boxes] == ["ZI0000", "ZI0001"]
    assert extra["seed"] == "ZI/ZJ"
    assert pipeline.seed_done("452") is True
    assert pipeline.seed_done("900") is True
    assert pipeline.seed_done("999") is False


def test_kx_seed_from_csv(tmp_path):
    csv_text = (
        "字,Unicode,类别,册,PDF页,页面x0,页面y0,页面x1,页面y1,页内序号\n"
        "一,U+4E00,部首标题,07,100,10,10,40,40,\n"
        "𤴗,U+24D17,字头,07,100,3532,702,3694,866,1\n"
        "疎,U+758E,字头,07,100,3356,2549,3512,2703,3\n"
        "疏,U+758F,字头,07,100,3159,705,3313,865,2\n"
    )
    _write(tmp_path / "字头清单.csv", csv_text)
    pipeline = KxPipeline({"seed_file": str(tmp_path / "字头清单.csv")})

    seeded = pipeline.seed_boxes("07:100")
    assert seeded is not None
    boxes, _ = seeded
    # 部首标题被排除；按页内序号排序（1=𤴗, 2=疏, 3=疎）
    assert [b.w for b in boxes] == [162, 154, 156]
    assert boxes[0].x == 3532
    assert boxes[0].pos == "K10001"
    assert pipeline.seed_done("07:100") is True
    assert pipeline.seed_done("07:101") is False


def test_seed_absent_returns_none(tmp_path):
    for pipeline, page in (
        (ZihaiPipeline({"seed_dir": str(tmp_path)}), "1:84"),
        (HzhPipeline({"seed_dir": str(tmp_path)}), "452"),
        (KxPipeline({"seed_file": str(tmp_path / "none.csv")}), "07:100"),
    ):
        assert pipeline.seed_boxes(page) is None
        assert pipeline.seed_done(page) is False


def test_state_write_keeps_seed_boxes(client, tmp_path, monkeypatch):
    """标记完成只该改状态：本页只有定稿时，不能写出一条空框记录把定稿顶掉。"""
    from app import config

    _write(
        tmp_path / "edits.json",
        {"1": [{"x": 10, "y": 20, "w": 30, "h": 40}, {"x": 60, "y": 20, "w": 30, "h": 40}]},
    )
    monkeypatch.setattr(
        config,
        "book_setting",
        lambda book: {"seed_dir": str(tmp_path), "pdf": "", "use_seed": True} if book == "zihai" else {},
    )
    monkeypatch.setattr(config, "load_settings", lambda: {"use_seed": True})
    # 本地存盘目录也关进 tmp_path，别把共用的测试工作目录弄脏
    monkeypatch.setattr(config, "BOX_DIR", tmp_path / "boxes")

    before = client.get("/api/books/zihai/pages/1:84/boxes").json()
    assert before["origin"] == "seed"
    assert len(before["boxes"]) == 2

    res = client.post("/api/books/zihai/pages/1:84/state", json={"done": True})
    assert res.status_code == 200
    assert res.json()["state"]["done"] is True

    after = client.get("/api/books/zihai/pages/1:84/boxes").json()
    assert len(after["boxes"]) == 2
    assert [b["x"] for b in after["boxes"]] == [10, 60]
    assert after["state"]["done"] is True


def test_boxes_endpoint_prefers_local_then_seed(client, tmp_path, monkeypatch):
    """GET /boxes：本地存盘 > 预置 > 空，且 origin 能区分。"""
    from app import config

    _write(tmp_path / "edits.json", {"1": [{"x": 10, "y": 20, "w": 30, "h": 40}]})
    monkeypatch.setattr(
        config,
        "book_setting",
        lambda book: {"seed_dir": str(tmp_path), "pdf": "", "use_seed": True} if book == "zihai" else {},
    )
    monkeypatch.setitem(config.load_settings(), "use_seed", True)
    monkeypatch.setattr(config, "load_settings", lambda: {"use_seed": True})

    seeded = client.get("/api/books/zihai/pages/1:84/boxes").json()
    assert seeded["origin"] == "seed"
    assert len(seeded["boxes"]) == 1

    client.put(
        "/api/books/zihai/pages/1:84/boxes",
        json={
            "boxes": [{"x": 1, "y": 2, "w": 3, "h": 4}],
            "width": 100,
            "height": 100,
        },
    )
    local = client.get("/api/books/zihai/pages/1:84/boxes").json()
    assert local["origin"] == "local"
    assert local["boxes"][0]["x"] == 1


def test_hd_page_range_is_enforced(grid_pdf, tmp_path):
    """只开放配置里的 PDF 页码；范围外直接报错，不当成空白页。"""
    pipeline = HdPipeline({"volumes": {"1": {"pdf": str(grid_pdf), "first_page": 1, "last_page": 1}}})

    pages = pipeline.pages()
    assert [p["id"] for p in pages] == ["1:1"]

    for bad in ("1:0", "1:2"):
        try:
            pipeline.gray(bad)
        except ValueError as exc:
            assert "只开放" in str(exc)
        else:
            raise AssertionError(f"{bad} 应该报错")


def test_hd_seed_is_not_treated_as_reviewed(tmp_path):
    """第一版定稿是自动检测初稿：有框、可以当 seed，但不算「已校对完」。"""
    row = {"pdf": 309, "box": [10, 20, 30, 40], "col": 1}
    seed_file = _write(tmp_path / "hd1-heads.ndjson", json.dumps(row, ensure_ascii=False) + "\n")
    pipeline = HdPipeline({"seed_file": str(seed_file), "first_page": 297, "last_page": 5106})

    assert pipeline.seed_done("1:310") is True
    assert pipeline.seed_counts_done() is False
    assert HdPipeline({"seed_file": str(seed_file), "seed_counts_done": True}).seed_counts_done() is True
