"""框的墨迹贴合与「值得一看」标记（移植自 yedict-data/_zihai/editor/serve.py）。

* ``tighten_boxes``  丢掉框内远小于主墨迹块的碎块（上一行的笔尖、邻字一角、标点），再取并集外接框
* ``refine_to_ink``  把框收到框内实际墨迹的外接框，再留 margin
* ``review_flags``   给尺寸离群 / 带孤立小墨点的框打标记
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np


def tighten_boxes(
    ink: np.ndarray,
    boxes: list[dict[str, Any]],
    *,
    margin: int = 2,
    gap: int = 3,
    min_frac: float = 0.22,
    min_px: int = 40,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for b in boxes:
        x, y, w, h = int(b["x"]), int(b["y"]), int(b["w"]), int(b["h"])
        x0, y0 = max(0, x - 2), max(0, y - 2)
        x1, y1 = min(ink.shape[1], x + w + 3), min(ink.shape[0], y + h + 3)
        nb = dict(
            b, x=float(x - margin), y=float(y - margin), w=float(w + 2 * margin), h=float(h + 2 * margin)
        )
        if x1 - x0 >= 6 and y1 - y0 >= 6:
            sub = ink[y0:y1, x0:x1]
            kk = cv2.dilate(sub, np.ones((2 * gap + 1, 2 * gap + 1), np.uint8))
            n, lab, st, _ = cv2.connectedComponentsWithStats(kk, connectivity=8)
            parts = sorted(((int(st[i][4]), i) for i in range(1, n) if st[i][4] >= min_px), reverse=True)
            if len(parts) >= 2:
                amax = parts[0][0]
                keep = [i for a, i in parts if a >= min_frac * amax]
                m = np.isin(lab, keep) & (sub > 0)
                ys, xs = np.nonzero(m)
                if len(xs):
                    nb = dict(
                        b,
                        x=float(x0 + xs.min() - margin),
                        y=float(y0 + ys.min() - margin),
                        w=float(xs.max() - xs.min() + 1 + 2 * margin),
                        h=float(ys.max() - ys.min() + 1 + 2 * margin),
                    )
        out.append(nb)
    return out


def refine_to_ink(
    ink: np.ndarray, boxes: list[dict[str, Any]], *, margin: int = 2, min_frac: float = 0.45
) -> list[dict[str, Any]]:
    H, W = ink.shape
    out: list[dict[str, Any]] = []
    for b in boxes:
        x0, y0 = max(0, round(b["x"])), max(0, round(b["y"]))
        x1, y1 = min(W, round(b["x"] + b["w"])), min(H, round(b["y"] + b["h"]))
        nb = b
        if x1 - x0 >= 4 and y1 - y0 >= 4:
            ys, xs = np.nonzero(ink[y0:y1, x0:x1])
            if len(xs):
                nx0, ny0 = x0 + int(xs.min()), y0 + int(ys.min())
                nx1, ny1 = x0 + int(xs.max()) + 1, y0 + int(ys.max()) + 1
                if (nx1 - nx0) * (ny1 - ny0) >= min_frac * b["w"] * b["h"]:
                    nb = dict(
                        b,
                        x=float(nx0 - margin),
                        y=float(ny0 - margin),
                        w=float(nx1 - nx0 + 2 * margin),
                        h=float(ny1 - ny0 + 2 * margin),
                    )
        out.append(nb)
    return out


def review_flags(
    ink: np.ndarray, boxes: list[dict[str, Any]], *, max_frac: float = 0.22, min_px: int = 40
) -> list[int]:
    n = len(boxes)
    if n == 0:
        return []
    H, W = ink.shape
    size_med = float(np.median([max(b["w"], b["h"]) for b in boxes])) or 1.0
    out: list[int] = []
    for i, b in enumerate(boxes):
        m = max(b["w"], b["h"])
        if m > 1.30 * size_med or m < 0.65 * size_med:
            out.append(i)
            continue
        x0, y0 = max(0, int(b["x"])), max(0, int(b["y"]))
        x1, y1 = min(W, int(b["x"] + b["w"]) + 1), min(H, int(b["y"] + b["h"]) + 1)
        if x1 - x0 < 6 or y1 - y0 < 6:
            continue
        sub = ink[y0:y1, x0:x1]
        nn, _, st, _ = cv2.connectedComponentsWithStats(sub, connectivity=8)
        parts = sorted(((int(st[j][4]), j) for j in range(1, nn) if st[j][4] >= min_px), reverse=True)
        if len(parts) < 2:
            continue
        main_a, main_i = parts[0]
        mx, my, mw, mh = (int(v) for v in st[main_i][:4])
        for area, idx in parts[1:]:
            if area > max_frac * main_a:
                continue
            cx, cy, cw, ch = (int(v) for v in st[idx][:4])
            # 与主块之间要有白缝：横向或纵向不重叠
            apart_x = cx + cw <= mx or cx >= mx + mw
            apart_y = cy + ch <= my or cy >= my + mh
            if apart_x or apart_y:
                out.append(i)
                break
    return out
