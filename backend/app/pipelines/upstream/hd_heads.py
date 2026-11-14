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


def trim_wide(mask, box: tuple[int, int, int, int], max_w: int) -> tuple[int, int, int, int]:
    """把「字头 + 后面的注音/释文被膨胀粘在一起」的宽块，切回字头。

    字头后面常常紧跟小字注音，k×k 膨胀会把两者并成一个宽块，
    宽到超出 `max_side` 就被整块丢掉（p774 这一页因此丢了 5 个字头）。

    切口要挑「宽度接近字头、而且空白足够宽」的那一处——汉字内部本来就有空列，
    随便切一刀会把一个字劈成两半（p868 就被切成了 96px 宽的半拉字）。
    """
    x, y, w, h = box
    if w <= max_w:
        return box
    sub = mask[y : y + h, x : x + w]
    col_ink = sub.sum(axis=0)
    min_gap = max(3, int(max_w * 0.05))
    lo = max(1, int(max_w * 0.5))
    hi = min(w, int(max_w * 1.6))
    best_cut, best_score = -1, 1e9
    i = lo
    while i < hi:
        if col_ink[i] == 0:
            start = i
            while i < w and col_ink[i] == 0:
                i += 1
            run = i - start
            if run >= min_gap:
                width = start
                score = abs(width - max_w) - run  # 越接近字头宽度、空白越宽越好
                if width >= max(10, int(max_w * 0.4)) and score < best_score:
                    best_score, best_cut = score, start
        else:
            i += 1
    if best_cut > 0:
        return (x, y, best_cut, h)
    return box


def merge_row_parts(
    boxes: list[tuple[int, int, int, int]], max_w: int
) -> list[tuple[int, int, int, int]]:
    """把同一行、紧挨着的两个块并回一个字。

    有的字笔画离得远（如「𠂇」「卝」这类），膨胀核也连不起来，
    于是同一个字被拆成左右两块，各占一半宽度，谁都不像字头（p868 那个 249px 的字
    被拆成 116 + 82 两块）。同一行、间隙很小、并起来不超过上限，就并。
    """
    out: list[tuple[int, int, int, int]] = []
    for b in sorted(boxes, key=lambda b: (b[1], b[0])):
        for i, m in enumerate(out):
            overlap = min(m[1] + m[3], b[1] + b[3]) - max(m[1], b[1])
            if overlap < 0.6 * min(m[3], b[3]):
                continue
            gap = max(b[0], m[0]) - min(m[0] + m[2], b[0] + b[2])
            if gap > max(6, int(max_w * 0.15)):
                continue
            x0 = min(m[0], b[0])
            x1 = max(m[0] + m[2], b[0] + b[2])
            if x1 - x0 > max_w:
                continue
            y0 = min(m[1], b[1])
            y1 = max(m[1] + m[3], b[1] + b[3])
            out[i] = (x0, y0, x1 - x0, y1 - y0)
            break
        else:
            out.append(b)
    return out


def leftmost_column(inside: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """每栏只留最左一列（字头一律起栏，古文字形示例向右排开）。

    上游是「以最左的那个块为基准」，一个走偏的块（古文字形、注文碎片）
    就能把整栏字头顶掉。所以加了兜底：只有当这一列只剩零星几个块时，
    才改用「最宽的那批块」定左边界——字头在一页上总是最大的。
    """
    widths = sorted(b[2] for b in inside)
    # 容差按字头宽度的 60%：字头之间本身就有 ±15px 的左边界抖动，
    # 上游取 25% 时同一栏里靠右的几个会被判成「缩进」而丢掉。
    tol = max(10, int(widths[len(widths) // 2] * 0.60))
    xmin = min(b[0] for b in inside)
    base = [b for b in inside if b[0] - xmin <= tol]
    if base:
        # 用「已选中的这一批」的中位左边界再定一次锚点：最左那个块常常是走偏的
        # （古文字形、注文碎片），拿它当锚点会让同一栏右半边的字头差几像素就出局。
        anchor = sorted(b[0] for b in base)[len(base) // 2]
        base = [b for b in inside if abs(b[0] - anchor) <= tol]
    if len(base) >= 3:
        return base
    wide = [b for b in inside if b[2] >= 0.75 * max(widths)]
    if wide:
        alt = min(b[0] for b in wide)
        alt_keep = [b for b in inside if abs(b[0] - alt) <= tol]
        if len(alt_keep) > len(base):
            return alt_keep
    return base


def detect(
    img: np.ndarray,
    shrink: int = 2,
    max_side: int = 340,
    min_ratio: float = 1.75,
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
    comps = components(mask, k)
    big = [
        b
        for b in comps
        if min_ratio * body_h <= b[3] <= limit
        and 45 <= b[2] <= limit
        and 0.5 <= b[2] / max(b[3], 1) <= 1.9
    ]
    # 超宽的块多半是「字头 + 紧跟的小字注音」被膨胀粘在一起：在空白处切回字头。
    wide = [
        b
        for b in comps
        if min_ratio * body_h <= b[3] <= limit
        and b[2] > limit
        and 0.5 <= b[2] / max(b[3], 1) <= 4.0
    ]
    if wide:
        ref_w = int(sorted(b[2] for b in big)[len(big) // 2]) if big else int(limit * 0.75)
        for b in wide:
            trimmed = trim_wide(mask, b, ref_w)
            if trimmed[2] <= limit:
                big.append(trimmed)
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
        for b in leftmost_column(inside):
            heads.append(b)
            cols.append(ci)

    if heads:
        merged = merge_row_parts(heads, limit)
        if len(merged) != len(heads):
            # 合并会打乱 heads/cols 的对应关系，按合并后的框重新判栏
            heads, cols = [], []
            for b in merged:
                cx = b[0] + b[2] / 2
                ci = min(range(len(blocks)), key=lambda i: 0 if blocks[i][0] <= cx <= blocks[i][1] else 1) if blocks else 0
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
