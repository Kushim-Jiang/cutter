"""通用连通域检测（移植自原桌面端 methods/detector.py）。

用于没有专用版面算法的书源（目前是汉语大字典），按宽高区间把相邻笔画合并成字框。
"""

from __future__ import annotations

from collections.abc import Iterable

import cv2
import numpy as np
from scipy.spatial import KDTree

from .box import BORDER, IOU_THRESH, Box

WHITE_GAP_RATIO: float = 0.12
MIN_AREA: int = 20


def _flatten(indices) -> list[int]:
    if isinstance(indices, (int, np.integer)):
        return [int(indices)]
    if isinstance(indices, np.ndarray):
        indices = indices.tolist()
    if isinstance(indices, Iterable) and not isinstance(indices, (str, bytes)):
        out: list[int] = []
        for item in indices:
            out.extend(_flatten(item))
        return out
    return [int(indices)]


def has_white_gap(image: np.ndarray, box: Box, *, white_ratio_thresh: float = 0.03) -> bool:
    crop = image[max(0, box.y) : box.y + box.h, max(0, box.x) : box.x + box.w]
    if crop.size == 0:
        return True
    binary = crop < 128
    h, w = crop.shape[:2]

    row_mask = binary.sum(axis=1) / max(w, 1) <= white_ratio_thresh
    row_diff = np.diff(np.concatenate([[0], row_mask.astype(int), [0]]))
    starts, ends = np.where(row_diff == 1)[0], np.where(row_diff == -1)[0]
    if len(starts) and (ends - starts).max() / h >= WHITE_GAP_RATIO:
        return True

    col_mask = binary.sum(axis=0) / max(h, 1) <= white_ratio_thresh
    col_diff = np.diff(np.concatenate([[0], col_mask.astype(int), [0]]))
    starts, ends = np.where(col_diff == 1)[0], np.where(col_diff == -1)[0]
    return bool(len(starts) and (ends - starts).max() / w >= WHITE_GAP_RATIO)


def detect_components(
    gray: np.ndarray,
    w_range: tuple[int, int] | None = None,
    h_range: tuple[int, int] | None = None,
    border: int = BORDER,
) -> list[Box]:
    """在灰度图上找字框。坐标原点为图像左上角。"""
    _, bw = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY_INV)
    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)

    components: list[Box] = []
    for i in range(1, num_labels):
        x, y, w, h = (
            int(stats[i, k])
            for k in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT)
        )
        if int(stats[i, cv2.CC_STAT_AREA]) >= MIN_AREA:
            components.append(Box(x, y, w, h))
    if not components:
        return []

    centers = np.array([(b.x + b.w / 2, b.y + b.h / 2) for b in components], dtype=np.float32)
    tree = KDTree(centers)

    def merge(boxes: list[Box]) -> Box:
        xs = [b.x for b in boxes]
        ys = [b.y for b in boxes]
        x1 = max(b.x + b.w for b in boxes)
        y1 = max(b.y + b.h for b in boxes)
        return Box(min(xs), min(ys), x1 - min(xs), y1 - min(ys))

    has_w = w_range is not None
    has_h = h_range is not None
    candidates: list[Box] = []

    for i, seed in enumerate(components):
        used = {i}
        used_boxes = [seed]
        best: Box | None = None
        found_new = True
        while found_new:
            current = merge(used_boxes)
            if (has_w and current.w > w_range[1]) or (has_h and current.h > h_range[1]):
                break
            if (
                has_w
                and has_h
                and w_range[0] <= current.w <= w_range[1]
                and h_range[0] <= current.h <= h_range[1]
            ):
                best = current
            k = min(8, len(components) - len(used))
            if k <= 0:
                break
            _, indices = tree.query([[current.x + current.w / 2, current.y + current.h / 2]], k=k)
            found_new = False
            for idx in np.atleast_1d(indices).reshape(-1):
                idx = int(idx)
                if idx in used:
                    continue
                trial = merge([*used_boxes, components[idx]])
                if (has_w and trial.w > w_range[1]) or (has_h and trial.h > h_range[1]):
                    continue
                used.add(idx)
                used_boxes.append(components[idx])
                found_new = True
                break
        if best:
            candidates.append(best)

    if not candidates:
        return []

    boxes_np = np.array([[b.x, b.y, b.x + b.w, b.y + b.h] for b in candidates], dtype=np.float32)
    scores = [float(b.area) for b in candidates]
    indices = cv2.dnn.NMSBoxes(boxes_np.tolist(), scores, score_threshold=0.0, nms_threshold=IOU_THRESH)
    final: list[Box] = []
    for idx in _flatten(indices):
        box = candidates[int(idx)]
        if has_white_gap(gray, box):
            continue
        final.append(Box(box.x - border, box.y - border, box.w + border * 2, box.h + border * 2))
    return final


def detect_selection(gray: np.ndarray, rect: Box, border: int = BORDER) -> Box | None:
    """把手画的矩形收紧到其中的墨迹。"""
    x0, y0 = rect.x, rect.y
    crop = gray[max(0, y0) : y0 + rect.h, max(0, x0) : x0 + rect.w]
    mask = crop < 128
    if not mask.any():
        return None
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    if not rows.any() or not cols.any():
        return None
    min_y, max_y = np.where(rows)[0][[0, -1]]
    min_x, max_x = np.where(cols)[0][[0, -1]]
    return Box(
        x=x0 + int(min_x) - border,
        y=y0 + int(min_y) - border,
        w=int(max_x - min_x) + 1 + border * 2,
        h=int(max_y - min_y) + 1 + border * 2,
        source="manual",
    )
