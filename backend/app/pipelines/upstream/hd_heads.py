"""《汉语大字典》字头候选检测（移植自 `yedict-data/_up/handa/scripts/hd_heads.py`）。

算法与上游逐条一致，仅去掉对 `hd_common`（项目路径、OCR、CLI）的依赖：

    1. 在 1/shrink 分辨率上做连通域（大核膨胀在 300dpi 上太慢）；
    2. 正文中位字高取 40..100px 的块（分析图分辨率）；
    3. 大字候选 = 高 >= min_ratio × 正文，且边长、宽高比在范围内；
    4. 按竖投影在中部找订口切成左右两栏；
    5. **每栏只保留最左一列**（字头一律起栏，古文字形示例在栏内向右排开）。
"""

from __future__ import annotations

from collections import Counter

import numpy as np


def components(mask: np.ndarray, k: int) -> list[tuple[int, int, int, int]]:
    """膨胀 k×k 后取连通域外接框（已收紧到原墨迹）。"""
    import cv2

    dil = cv2.dilate(mask, np.ones((k, k), np.uint8))
    n, _lab, st, _ = cv2.connectedComponentsWithStats(dil, connectivity=8)
    out: list[tuple[int, int, int, int]] = []
    for i in range(1, n):
        x, y, w, h, _a = st[i]
        if w < 12 or h < 12:
            continue
        sub = mask[y : y + h, x : x + w]
        ys, xs = np.flatnonzero(sub.any(axis=1)), np.flatnonzero(sub.any(axis=0))
        if ys.size == 0:
            continue
        out.append(
            (
                int(x + xs[0]),
                int(y + ys[0]),
                int(xs[-1] - xs[0] + 1),
                int(ys[-1] - ys[0] + 1),
            )
        )
    return out


def dedupe(boxes: list[tuple[int, int, int, int]], overlap: float = 0.72) -> list[tuple[int, int, int, int]]:
    """去掉被更大框几乎完全包含的重复框（同一字的碎片）。"""
    boxes = sorted(boxes, key=lambda b: -(b[2] * b[3]))
    keep: list[tuple[int, int, int, int]] = []
    for b in boxes:
        x, y, w, h = b
        dup = False
        for kx, ky, kw, kh in keep:
            ix = max(0, min(x + w, kx + kw) - max(x, kx))
            iy = max(0, min(y + h, ky + kh) - max(y, ky))
            if ix * iy >= overlap * w * h:
                dup = True
                break
        if not dup:
            keep.append(b)
    return keep


def column_blocks(mask: np.ndarray, shrink: int = 1) -> list[tuple[int, int]]:
    """两栏：在页面中部找墨量最低的订口，左右各成一栏（返回原图坐标）。"""
    h, w = mask.shape
    prof = mask.sum(axis=0).astype(np.float64) / h
    width = max(5, w // 500)
    ker = np.ones(width) / width
    sm = np.convolve(prof, ker, mode="same")
    cols_any = np.flatnonzero(sm > max(0.004, sm.max() * 0.03))
    if cols_any.size == 0:
        return [(0, w * shrink)]
    x0, x1 = int(cols_any[0]), int(cols_any[-1])
    lo, hi = int(w * 0.32), int(w * 0.68)
    if hi <= lo:
        return [(x0 * shrink, x1 * shrink)]
    gutter = lo + int(np.argmin(sm[lo:hi]))
    return [(x0 * shrink, gutter * shrink), (gutter * shrink, x1 * shrink)]


def body_height(probe: list[tuple[int, int, int, int]], fallback_shape: tuple[int, int]) -> int:
    """本页正文中位字高（分析图分辨率）。

    上游写死 `40 <= h <= 100`——那是按第一版（300dpi 正文约 106px）定的。
    第二版正文只有约 65px，落在范围外，会把字头当正文。所以改成取全分布众数：
    正文是页面上出现次数最多的字块，两版都成立。
    """
    heights = [b[3] for b in probe if 12 <= b[3] <= 200]
    if not heights:
        return max(20, fallback_shape[0] // 90)
    return Counter(heights).most_common(1)[0][0]


def detect(
    img: np.ndarray,
    shrink: int = 2,
    max_side: int = 340,
    min_ratio: float = 1.45,
    body_h: int = 0,
    footer: float = 0.9,
    edge: float = 0.02,
) -> dict:
    """在 1/shrink 分辨率上做连通域，框再放大回原图坐标。

    `img` 是整页灰度图（本项目的书源统一按 300dpi 渲染，与上游数据同坐标）。
    `footer` 丢掉页脚（页码、印章）区域的块——它们常挤在栏内最左边，
    会把真正的字头挤出「最左一列」。`edge` 丢掉贴左右页边的块。
    返回 `{"body_h", "kernel", "blocks", "cands":[{"col", "box"}]}`，`box` 为原图像素。
    """
    import cv2

    mask_full = (img < 128).astype(np.uint8)
    if shrink > 1:
        h, w = mask_full.shape
        small = cv2.resize(mask_full, (w // shrink, h // shrink), interpolation=cv2.INTER_AREA)
        mask = (small > 0.45).astype(np.uint8)
    else:
        mask = mask_full

    if not body_h:
        body_h = body_height(components(mask, 3), mask.shape)
    k = max(7, int(round(body_h * 0.16)) | 1)

    limit = max_side // shrink
    big = [
        b
        for b in components(mask, k)
        if min_ratio * body_h <= b[3] <= limit
        and 45 <= b[2] <= limit
        and 0.5 <= b[2] / max(b[3], 1) <= 1.9
    ]
    if footer < 1.0 or edge > 0.0:
        mh, mw = mask.shape
        big = [
            b
            for b in big
            if (b[1] + b[3] / 2) <= footer * mh and b[0] >= edge * mw and b[0] + b[2] <= (1 - edge) * mw
        ]
    big = dedupe(big)

    blocks = [(a // shrink, b // shrink) for a, b in column_blocks(mask, shrink)]
    heads: list[tuple[int, int, int, int]] = []
    cols: list[int] = []
    for ci, (bx0, bx1) in enumerate(blocks):
        inside = [b for b in big if bx0 <= b[0] + b[2] / 2 <= bx1]
        if not inside:
            continue
        xmin = min(b[0] for b in inside)
        widths = sorted(b[2] for b in inside)
        tol = max(10, int(widths[len(widths) // 2] * 0.25))
        for b in inside:
            if b[0] - xmin <= tol:
                heads.append(b)
                cols.append(ci)

    order = sorted(range(len(heads)), key=lambda i: (cols[i], heads[i][1], heads[i][0]))
    cands = []
    for i in order:
        x, y, w, h = heads[i]
        cands.append(
            {
                "col": int(cols[i]),
                "box": [int(x) * shrink, int(y) * shrink, int(w) * shrink, int(h) * shrink],
            }
        )
    return {
        "body_h": int(body_h * shrink),
        "kernel": k,
        "blocks": blocks,
        "cands": cands,
    }
