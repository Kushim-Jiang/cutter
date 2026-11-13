"""《汉语大字典》第二版字头定位（移植自 `yedict-data/_up/handa/scripts/hd_v2.py`）。

第二版 PDF 自带 OCR 文字层：正文约 15pt、字头 30–31pt，古文字形示例也在 30pt 左右。
上游的做法是「文字层给候选 + 索引给期望序列 + 顺序对齐」；这里保留前三步：

    1. 候选 = 字号在 [min_pt, max_pt] 的单字 span；
    2. 按 x 中心最大空隙分两栏；
    3. 每栏只留最左一列（与第一版的版面约束同一条，用来筛掉栏内右排的古文字形）；
    4. 用整页墨迹把 span 框收紧成真实字框。

索引对齐（`hd2.csv`）在这里是可选项：候选自带 OCR 文字，若给了期望字序，
直接按字符匹配过滤，比上游的字形特征 DP 轻得多。
"""

from __future__ import annotations

from typing import Any, cast

import numpy as np


def head_spans(page: Any, min_pt: float = 26.0, max_pt: float = 40.0) -> list[dict[str, Any]]:
    """取页面文字层里字号落在 [min_pt, max_pt] 的单字 span（PD F点坐标）。"""
    out: list[dict[str, Any]] = []
    data = cast(dict[str, Any], page.get_text("dict"))
    for block in data.get("blocks", []):
        if not isinstance(block, dict) or block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = str(span.get("text", "")).strip()
                if len(text) != 1:
                    continue
                size = float(span.get("size", 0.0))
                if not (min_pt <= size <= max_pt):
                    continue
                bb = span["bbox"]
                x0, y0, x1, y1 = (float(v) for v in bb)
                out.append(
                    {
                        "char": text,
                        "bbox": (x0, y0, x1, y1),
                        "cx": (x0 + x1) / 2,
                        "cy": (y0 + y1) / 2,
                        "size": size,
                    }
                )
    return out


def split_columns(items: list[dict[str, Any]], min_gap: float = 20.0) -> list[int]:
    """按 x 中心最大空隙分两栏。"""
    if len(items) < 4:
        return [0] * len(items)
    xs = sorted(it["cx"] for it in items)
    gaps = [(xs[i + 1] - xs[i], i) for i in range(len(xs) - 1)]
    if not gaps:
        return [0] * len(items)
    gap, k = max(gaps)
    if gap < min_gap:
        return [0] * len(items)
    cut = (xs[k] + xs[k + 1]) / 2
    return [0 if it["cx"] < cut else 1 for it in items]


def leftmost_column(items: list[dict[str, Any]], cols: list[int], tol_ratio: float = 0.25) -> list[int]:
    """每栏只保留最左一列，返回保留项的下标（按栏、y 排序）。"""
    keep: list[int] = []
    for col in sorted(set(cols)):
        idx = [i for i, c in enumerate(cols) if c == col]
        xmin = min(items[i]["bbox"][0] for i in idx)
        widths = sorted(items[i]["bbox"][2] - items[i]["bbox"][0] for i in idx)
        tol = max(4.0, widths[len(widths) // 2] * tol_ratio)
        for i in idx:
            if items[i]["bbox"][0] - xmin <= tol:
                keep.append(i)
    keep.sort(key=lambda i: (cols[i], items[i]["cy"], items[i]["bbox"][0]))
    return keep


def to_ink_box(img: np.ndarray, bbox: tuple[float, float, float, float], scale: float, pad: float = 1.0) -> list[int] | None:
    """把 PDF 点坐标的 span 框收紧到实际墨迹，返回整页图像素框 [x,y,w,h]。"""
    x0 = max(0, int((bbox[0] - pad) * scale))
    y0 = max(0, int((bbox[1] - pad) * scale))
    x1 = min(img.shape[1], int((bbox[2] + pad) * scale))
    y1 = min(img.shape[0], int((bbox[3] + pad) * scale))
    if x1 <= x0 or y1 <= y0:
        return None
    sub = img[y0:y1, x0:x1]
    mask = sub < 128
    ys, xs = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
    if ys.size == 0:
        return [x0, y0, x1 - x0, y1 - y0]
    return [
        x0 + int(xs[0]),
        y0 + int(ys[0]),
        int(xs[-1] - xs[0] + 1),
        int(ys[-1] - ys[0] + 1),
    ]


def detect(
    page: Any,
    img: np.ndarray,
    scale: float,
    min_pt: float = 26.0,
    max_pt: float = 40.0,
    expect: list[str] | None = None,
    tol_ratio: float = 0.25,
) -> dict[str, Any]:
    """文字层定位字头，返回 `{"cands":[{"col","box","char"}], "spans", "kept"}`。"""
    spans = head_spans(page, min_pt, max_pt)
    if not spans:
        return {"cands": [], "spans": 0, "kept": 0}
    if expect:
        wanted = set(expect)
        hits = [s for s in spans if s["char"] in wanted]
        if hits:
            spans = hits
    cols = split_columns(spans)
    keep = leftmost_column(spans, cols, tol_ratio)
    cands = []
    for i in keep:
        box = to_ink_box(img, spans[i]["bbox"], scale)
        if not box or box[2] < 8 or box[3] < 8:
            continue
        cands.append({"col": int(cols[i]), "box": box, "char": spans[i]["char"]})
    return {"cands": cands, "spans": len(spans), "kept": len(keep)}
