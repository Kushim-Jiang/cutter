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


def test_hd_leftmost_column_ignores_stray_block():
    """一个走偏的窄块（古文字形/注文碎片）不该把整栏字头顶掉。

    上游规则以「最左的那个块」为基准，p321 这类页整左栏的 5 个 字头
    就是因为最左边多出一个 132px 的小块而全被丢掉。
    """
    from app.pipelines.upstream import hd_heads

    heads = [(860, 600 + i * 500, 255, 250) for i in range(5)]
    stray = (724, 6174, 132, 168)  # 更左、更窄，只有一块
    kept = hd_heads.leftmost_column([*heads, stray])
    # 关键是整栏字头一个不少；多留一个窄块比丢 5 个字头划算
    assert set(heads) <= set(kept)
    assert len(kept) <= len(heads) + 1


def test_hd_leftmost_column_keeps_upstream_behaviour_when_clean():
    """正常页面上（最左一列本身就成规模）结果与上游一致：只留最左一列。"""
    from app.pipelines.upstream import hd_heads

    left = [(860, 600, 255, 250), (858, 1400, 250, 250)]
    indented = [(1200, 900, 240, 240)]  # 向右缩进的块（古文字形示例）
    kept = hd_heads.leftmost_column(left + indented)
    assert sorted(kept) == sorted(left)


def test_hd_split_overlong_cuts_head_from_note():
    """字头后面紧跟小字注音时，膨胀会把两者粘成一个宽块；超宽块要在空白处切回字头。"""
    from app.pipelines.upstream import hd_heads

    mask = np.zeros((60, 300), np.uint8)
    mask[10:50, 5:55] = 1  # 字头
    mask[10:50, 80:280] = 1  # 右侧连着的注文（中间 25 列空白）
    parts = hd_heads.split_overlong(mask, (0, 0, 300, 60), limit=80, ref_w=50, ref_h=40)
    assert parts
    head = min(parts, key=lambda p: p[0])
    assert head[0] == 5 and head[2] <= 80


def test_hd_split_overlong_cuts_narrow_gap():
    """字头后的注音离得很近时空白只有十来列，也要能切开（p1316 的 5 列窄缝）。"""
    from app.pipelines.upstream import hd_heads

    mask = np.zeros((60, 200), np.uint8)
    mask[10:50, 5:55] = 1
    mask[10:50, 60:200] = 1  # 只隔 5 列
    parts = hd_heads.split_overlong(mask, (0, 0, 200, 60), limit=80, ref_w=50, ref_h=40)
    assert parts and min(p[0] for p in parts) == 5


def test_hd_layout_detect_joins_split_glyph():
    """同一个字被拆成左右两半（各自宽高比都不合规）时，先并合再筛选才检得出来。"""
    from app.pipelines.upstream import hd_heads

    ink = np.zeros((600, 400), np.uint8)
    for y in (300, 400, 500):
        for x in range(20, 380, 40):
            ink[y : y + 26, x : x + 26] = 1
    for y in (60, 180):
        ink[y : y + 100, 60:88] = 1
        ink[y : y + 100, 96:126] = 1
    gray = imaging.ink_to_gray(ink)
    found = hd_heads.detect(gray, shrink=1)
    widths = sorted(c["box"][2] for c in found["cands"])
    assert len(found["cands"]) == 2
    assert widths[-1] > 60  # 两半并回了一个字


def test_hd_merge_row_parts_joins_split_glyph():
    """同一个字被拆成左右两块时要并回一个字（p868 的 249px 字被拆成 116+82）。"""
    from app.pipelines.upstream import hd_heads

    left = (1850, 295, 58, 125)
    right = (1920, 293, 41, 121)
    merged = hd_heads.merge_row_parts([left, right], max_w=170)
    assert len(merged) == 1
    x, _y, w, _h = merged[0]
    assert w == 111 and x == 1850
