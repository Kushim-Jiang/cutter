"""对齐表的行对齐规则（纯函数，不需要真实数据仓库）。"""

from __future__ import annotations

import pytest
from app import align


def _box(x: int, y: int, w: int = 100, h: int = 100) -> dict:
    return {"idx": 0, "x": x, "y": y, "w": w, "h": h}


@pytest.fixture()
def fake_zh(monkeypatch: pytest.MonkeyPatch):
    table = {1: [(1, "一", "U04E00", ""), (2, "二", "U04E8C", ""), (3, "三", "U04E09", "")]}
    monkeypatch.setattr(align, "zh_index", lambda: table)
    return table


def test_reading_order_is_column_then_row(fake_zh):
    """三栏页面：先整栏从上到下，再下一栏。"""
    boxes = [
        _box(900, 10),
        _box(100, 400),
        _box(900, 300),
        _box(100, 10),
        _box(500, 10),
        _box(500, 200),
    ]
    ordered = align.reading_order(boxes)
    assert [(b["x"], b["y"]) for b in ordered] == [
        (100, 10),
        (100, 400),
        (500, 10),
        (500, 200),
        (900, 10),
        (900, 300),
    ]


def test_rows_align_three_sources(fake_zh):
    v1 = [_box(100, 10), _box(100, 200), _box(100, 400)]
    v2 = [_box(100, 10), _box(100, 200), _box(100, 400)]
    result = align.build_rows(1, v1, v2, [])
    assert [r["char"] for r in result["rows"]] == ["一", "二", "三"]
    assert result["aligned"] is True
    assert result["counts"] == {"zh": 3, "v1": 3, "v2": 3}


def test_missing_side_leaves_gap_and_shifts(fake_zh):
    """卷1 第 2 位确实失收：该行留空，后面的框顺移上来。"""
    v1 = [_box(100, 10), _box(100, 400)]  # 只有 2 个框
    v2 = [_box(100, 10), _box(100, 200), _box(100, 400)]
    notes = [{"pos": 2, "char": "", "miss": "v1", "note": "卷1 缺此条"}]
    result = align.build_rows(1, v1, v2, notes)
    rows = result["rows"]
    assert rows[1]["v1"] is None  # 第 2 位空出来
    assert rows[2]["v1"] is not None  # 第 3 位用卷1 的第二个框
    assert rows[2]["v1"]["y"] == 400
    assert result["aligned"] is True


def test_extra_box_is_skipped(fake_zh):
    """卷1 多切了一个重复框：那个框不算数，后面的整体前移。"""
    v1 = [_box(100, 10), _box(100, 200), _box(100, 300), _box(100, 400)]
    v2 = [_box(100, 10), _box(100, 200), _box(100, 400)]
    notes = [{"pos": 2, "char": "鲥", "extra": "v1", "note": "卷1 重复收"}]
    result = align.build_rows(1, v1, v2, notes)
    rows = result["rows"]
    assert len(rows) == 3
    assert [r["v1"]["y"] for r in rows] == [10, 300, 400]
    assert result["aligned"] is True


def test_counts_mismatch_is_flagged(fake_zh):
    v1 = [_box(100, 10), _box(100, 200), _box(100, 400)]
    v2 = [_box(100, 10), _box(100, 200)]
    result = align.build_rows(1, v1, v2, [])
    assert result["aligned"] is False


def test_natural_zid_matches_upstream_convention():
    """Z 号 = Z + 页/100 的 36 进制 + (页%100)*100+位置。"""
    assert align.natural_zid(1, 1) == "Z00101"
    assert align.natural_zid(0, 99) == "Z00099"
    assert align.natural_zid(1800, 1) == ""  # 越界不给号


def test_sprite_flag_comes_from_manifest(fake_zh, monkeypatch: pytest.MonkeyPatch):
    """Z/P 系没有码位字，第一列只能靠雪碧图，所以要有 sprite 标记。"""
    monkeypatch.setattr(
        align, "sprite_manifest", lambda: {align.natural_zid(1, 2): ["zh10/sheets/x.png", 0, 0]}
    )
    v1 = [_box(100, 10), _box(100, 200), _box(100, 400)]
    result = align.build_rows(1, v1, v1, [])
    assert [r["sprite"] for r in result["rows"]] == [False, True, False]
