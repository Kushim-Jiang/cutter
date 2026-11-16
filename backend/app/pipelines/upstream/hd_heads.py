"""《汉语大字典》字头候选检测（移植自 `yedict-data/_up/handa/scripts/hd_heads.py`）。

算法与上游逐条一致，仅去掉对 `hd_common`（项目路径、OCR、CLI）的依赖：

    1. 在 1/shrink 分辨率上做连通域（大核膨胀在 300dpi 上太慢）；
    2. 正文中位字高取全分布众数（正文总是页面上出现次数最多的字块）；
    3. 大字候选 = 高 >= min_ratio × 正文，同一行被拆开的左右两半先并回一个字，
       再把宽/高超上限的块沿空白切开，最后才按边长、宽高比筛选；
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


def _tighten(mask: np.ndarray, box: tuple[int, int, int, int]) -> tuple[int, int, int, int] | None:
    """把框收紧到框内真实墨迹的外接框（和图块取出时做的收紧同一件事）。"""
    x, y, w, h = box
    if w <= 0 or h <= 0:
        return None
    sub = mask[y : y + h, x : x + w]
    ys, xs = np.flatnonzero(sub.any(axis=1)), np.flatnonzero(sub.any(axis=0))
    if ys.size == 0:
        return None
    return (int(x + xs[0]), int(y + ys[0]), int(xs[-1] - xs[0] + 1), int(ys[-1] - ys[0] + 1))


def _gaps(prof: np.ndarray, min_gap: int) -> list[tuple[int, int]]:
    """投影里的空白段：返回 [(空白起点, 空白宽度)]。"""
    out: list[tuple[int, int]] = []
    n = prof.size
    i = 1
    while i < n:
        if prof[i] == 0:
            start = i
            while i < n and prof[i] == 0:
                i += 1
            run = i - start
            if run >= min_gap:
                out.append((start, run))
        else:
            i += 1
    return out


def _cut(
    mask: np.ndarray,
    box: tuple[int, int, int, int],
    axis: str,
    limit: int,
    target: int,
) -> int:
    """在 `axis` 方向找一条最像「字头边界」的空白缝，返回切点（相对框起点），没有就 0。"""
    x, y, w, h = box
    size = w if axis == "x" else h
    if size <= limit:
        return 0
    sub = mask[y : y + h, x : x + w]
    prof = sub.sum(axis=0) if axis == "x" else sub.sum(axis=1)
    lo = max(10, int(limit * 0.15))
    # 字头后面的注音、旁边的字形示例离得很近，空白只有十来个像素：
    # 门槛按比例取（字头宽的 3%），比固定 5% 更容易切对 p1316 那种 5 列的窄缝。
    min_gap = max(2, int(min(target, limit) * 0.03))
    best_cut, best_score = 0, 1e9
    for pos, run in _gaps(prof, min_gap):
        if pos < lo or size - pos < lo:
            continue
        score = abs(pos - target) - run * 2  # 越接近字头尺寸、空白越宽越好
        if score < best_score:
            best_score, best_cut = score, pos
    return best_cut


def split_overlong(
    mask: np.ndarray,
    box: tuple[int, int, int, int],
    limit: int,
    ref_w: int,
    ref_h: int,
    depth: int = 0,
) -> list[tuple[int, int, int, int]]:
    """把超出上限（`limit`）的块沿空白处切开，直到每块都装得下字头。

    膨胀核会把「字头 + 紧跟的小字注音」「字头 + 旁边的古文字形示例」粘成一块，
    宽或高超出上限就整块丢掉（p774 丢 5 个字头、p1346 那个女+夾连字形示例
    粘成 448×446 的块）。切开后每一块都收紧到墨迹，继续参与正常筛选。
    """
    x, y, w, h = box
    if w <= limit and h <= limit:
        return [box]
    if depth >= 3:
        return []
    best: tuple[float, str, int] | None = None
    for axis in ("x", "y"):
        target = ref_w if axis == "x" else ref_h
        cut = _cut(mask, box, axis, limit, target)
        if cut <= 0:
            continue
        score = abs(cut - target)
        if best is None or score < best[0]:
            best = (score, axis, cut)
    if best is None:
        return []
    _, axis, cut = best
    if axis == "x":
        parts = [(x, y, cut, h), (x + cut, y, w - cut, h)]
    else:
        parts = [(x, y, w, cut), (x, y + cut, w, h - cut)]
    out: list[tuple[int, int, int, int]] = []
    for part in parts:
        tight = _tighten(mask, part)
        if tight is None:
            continue
        out.extend(split_overlong(mask, tight, limit, ref_w, ref_h, depth + 1))
    return out


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


def column_tolerance(inside: list[tuple[int, int, int, int]]) -> int:
    """同一列允许的左边界抖动（字头宽度的 60%）。"""
    if not inside:
        return 10
    return max(10, int(sorted(b[2] for b in inside)[len(inside) // 2] * 0.60))


def leftmost_column(inside: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """每栏只留最左一列（字头一律起栏，古文字形示例向右排开）。

    上游是「以最左的那个块为基准」，一个走偏的块（古文字形、注文碎片）
    就能把整栏字头顶掉。所以加了兜底：只有当这一列只剩零星几个块时，
    才改用「最宽的那批块」定左边界——字头在一页上总是最大的。
    """
    if not inside:
        return []
    # 容差按字头宽度的 60%：字头之间本身就有 ±15px 的左边界抖动，
    # 上游取 25% 时同一栏里靠右的几个会被判成「缩进」而丢掉。
    tol = column_tolerance(inside)
    widths = sorted(b[2] for b in inside)
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
    # 只看「比正文大的块」：字头、古文字形示例、被膨胀粘在一起的碎片都在里面。
    # 先并合左右两半再筛选——分开的半个字各自都不合格（宽高比 < 0.5），
    # p1320 的「女 + 比」就是这样整块漏掉的。
    tall = [b for b in components(mask, k) if b[3] >= min_ratio * body_h]
    tall = merge_row_parts(tall, limit)

    def accept(b: tuple[int, int, int, int]) -> bool:
        return (
            min_ratio * body_h <= b[3] <= limit
            and 45 <= b[2] <= limit
            and 0.5 <= b[2] / max(b[3], 1) <= 1.9
        )

    big = [b for b in tall if accept(b)]
    # 超宽或超高的块多半是「字头 + 紧跟的小字注音 / 旁边的字形示例」被膨胀粘住：
    # 在空白处切开，切出来的每一块收紧到墨迹后再按正常条件参与筛选。
    # 切出来的块单独放着——它们只能补进「原生块已经定好的那一列」，
    # 不能反过来当锚点（碎片往左一挪，整栏字头就全被判成缩进丢掉）。
    parts: list[tuple[int, int, int, int]] = []
    over = [b for b in tall if b[2] > limit or b[3] > limit]
    if over:
        ref_w = int(sorted(b[2] for b in big)[len(big) // 2]) if big else int(limit * 0.75)
        ref_h = int(sorted(b[3] for b in big)[len(big) // 2]) if big else int(limit * 0.75)
        # 整片图版、密集注文、书眉横线会连成比字头大几十倍的大块，切开只会得到碎片
        # （p1355 那页右栏就被这种 1671×646 的块带偏）。只切「大致是字头 + 邻块」的量级。
        max_area = 6 * ref_w * ref_h
        for b in over:
            if b[2] * b[3] > max_area:
                continue
            for part in split_overlong(mask, b, limit, ref_w, ref_h):
                if accept(part):
                    parts.append(part)
    if footer < 1.0 or edge > 0.0:
        mh, mw = mask.shape

        def in_body(b: tuple[int, int, int, int]) -> bool:
            return (
                (b[1] + b[3] / 2) <= footer * mh
                and b[0] >= edge * mw
                and b[0] + b[2] <= (1 - edge) * mw
            )

        big = [b for b in big if in_body(b)]
        parts = [b for b in parts if in_body(b)]
    big = dedupe(big)
    if parts:
        # 切出来的块只在没被原生块覆盖时才算数
        keep = dedupe(big + parts)
        prim = set(big)
        parts = [b for b in keep if b not in prim]

    blocks = [(a // shrink, b // shrink) for a, b in column_blocks(mask, shrink)]
    heads: list[tuple[int, int, int, int]] = []
    cols: list[int] = []
    for ci, (bx0, bx1) in enumerate(blocks):
        inside = [b for b in big if bx0 <= b[0] + b[2] / 2 <= bx1]
        frags = [b for b in parts if bx0 <= b[0] + b[2] / 2 <= bx1]
        kept = leftmost_column(inside)
        if kept:
            # 切出来的块补进这一列：左边界要落在锚点附近，否则当碎片丢掉
            tol = column_tolerance(inside)
            anchor = sorted(b[0] for b in kept)[len(kept) // 2]
            kept = kept + [b for b in frags if abs(b[0] - anchor) <= tol]
        elif frags:
            kept = leftmost_column(frags)
        if not kept:
            continue
        heads.extend(kept)
        cols.extend([ci] * len(kept))

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
