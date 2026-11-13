"""核心图像与几何工具。"""

from __future__ import annotations

import numpy as np
from app.core import detector, export, imaging
from app.core.box import Box, coverage_deduplication, coverage_ratio, iou
from app.core.deskew import auto_deskew


def test_box_helpers():
    a = Box(0, 0, 10, 10)
    b = Box(0, 0, 10, 10)
    c = Box(100, 100, 10, 10)
    assert iou(a, b) == 1.0
    assert iou(a, c) == 0.0
    assert coverage_ratio(a, b) == 1.0

    dup = coverage_deduplication([a, b, c])
    assert len(dup) == 2


def test_box_roundtrip():
    box = Box(1, 2, 3, 4, source="manual", band=1, line=2, pitch=33.5, cw=30.0)
    again = Box.from_dict(box.as_dict())
    assert again == box


def test_imaging_ink_roundtrip():
    ink = np.zeros((20, 30), np.uint8)
    ink[5:10, 6:12] = 1
    gray = imaging.ink_to_gray(ink)
    assert gray[7, 7] == 0 and gray[0, 0] == 255
    assert (imaging.crop(gray, 6, 5, 3, 3) == 0).all()
    assert (imaging.crop(gray, -50, -50, 5, 5) == 255).all()


def test_downscale_keeps_aspect():
    gray = np.full((300, 600), 200, np.uint8)
    small = imaging.downscale(gray, 200)
    assert small.shape == (100, 200)


def test_detect_components_finds_squares():
    gray = np.full((300, 300), 255, np.uint8)
    for y in (40, 140):
        for x in (40, 140):
            gray[y : y + 60, x : x + 60] = 0

    boxes = detector.detect_components(gray, w_range=(40, 100), h_range=(40, 100))
    assert len(boxes) == 4
    # 加了 5px 边框
    assert {b.w for b in boxes} == {70}


def test_detect_selection_trims_to_ink():
    gray = np.full((100, 100), 255, np.uint8)
    gray[40:60, 30:50] = 0
    box = detector.detect_selection(gray, Box(20, 20, 60, 60))
    assert box is not None
    assert (box.x, box.y) == (25, 35)
    assert (box.w, box.h) == (30, 30)


def test_auto_deskew_straightens():
    canvas = np.full((400, 400), 255, np.uint8)
    for y in range(60, 340, 40):
        canvas[y : y + 12, 60:340] = 0
    m = __import__("cv2").getRotationMatrix2D((200, 200), 6.0, 1.0)
    skewed = __import__("cv2").warpAffine(
        canvas, m, (400, 400), flags=__import__("cv2").INTER_NEAREST, borderValue=255
    )
    straight = auto_deskew(skewed)

    # 纠斜后行投影应该是清晰的峰谷（方差大），斜着的时候每行都摊开一点（方差小）
    def spread(img):
        rows = (img < 128).sum(axis=1).astype(float)
        return float(rows.var())

    assert spread(straight) > spread(skewed)


def test_reading_order_columns():
    boxes = [Box(210, 10, 20, 20), Box(10, 10, 20, 20), Box(10, 100, 20, 20)]
    ordered = export.sort_reading_order(boxes, image_width=400, column_count=2)
    assert [(b.x, b.y) for b in ordered] == [(10, 10), (10, 100), (210, 10)]


def test_sort_by_band_and_col():
    boxes = [Box(0, 50, 10, 10, band=1), Box(0, 10, 10, 10, band=0), Box(5, 10, 10, 10, band=0)]
    assert [b.band for b in export.sort_by_band(boxes)] == [0, 0, 1]
    col_boxes = [Box(0, 50, 10, 10, col=2), Box(0, 10, 10, 10, col=1), Box(0, 20, 10, 10, col=1)]
    assert [b.col for b in export.sort_by_col(col_boxes)] == [1, 1, 2]


def test_refine_tighten_and_flags():
    from app.core import refine

    ink = np.zeros((200, 200), np.uint8)
    ink[50:120, 50:120] = 1  # 主墨块
    ink[30:38, 52:60] = 1  # 与主块隔开 12px 的小碎块（64 px，够 min_px 但远小于主块）
    boxes = [{"x": 45, "y": 28, "w": 80, "h": 96}]

    tightened = refine.tighten_boxes(ink, boxes, margin=2)
    assert tightened[0]["x"] == 48 and tightened[0]["y"] == 48

    refined = refine.refine_to_ink(ink, boxes, margin=2)
    assert refined[0]["x"] == 48 and refined[0]["w"] == 74

    flags = refine.review_flags(ink, [{"x": 45, "y": 28, "w": 80, "h": 96}])
    assert flags == [0]


def test_export_boxes_avoids_name_collision(tmp_path):
    gray = np.full((50, 50), 255, np.uint8)
    gray[10:20, 10:20] = 0
    boxes = [Box(10, 10, 10, 10), Box(20, 20, 10, 10)]

    first = export.export_boxes(gray, boxes, tmp_path, prefix="p1", page_tag="1")
    second = export.export_boxes(gray, boxes, tmp_path, prefix="p1", page_tag="1")

    assert [r["index"] for r in first] == [1, 2]
    assert [r["index"] for r in second] == [3, 4]
    assert len(list(tmp_path.glob("*.png"))) == 4


def test_hd_body_height_ignores_head_blocks():
    """汉大版面法按「字头比正文大」筛选，正文高度必须取到小字块而不是字头。

    上游写死 40..100，那是第一版（正文约 106px）的值；第二版正文只有约 65px，
    会把字头当正文，导致一个框都检不出来。
    """
    from app.pipelines.upstream import hd_heads

    probe = [(0, 0, 13, 13)] * 20 + [(0, 0, 50, 50)] * 3
    assert hd_heads.body_height(probe, (600, 400)) == 13


def test_hd_layout_detect_finds_heads_only():
    """合成页上版面法只留比正文大的块，且每栏取最左一列。"""
    from app.pipelines.upstream import hd_heads

    ink = np.zeros((600, 400), np.uint8)
    for y in (60, 260):
        for x in (60, 220):
            ink[y : y + 100, x : x + 100] = 1
    for y in (190, 400, 510):
        for x in range(20, 380, 40):
            ink[y : y + 26, x : x + 26] = 1
    gray = imaging.ink_to_gray(ink)
    found = hd_heads.detect(gray, shrink=1)
    assert len(found["cands"]) == 4
    assert found["body_h"] == 26
