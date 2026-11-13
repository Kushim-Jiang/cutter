"""导出：阅读顺序排序 + 裁图落盘。"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import imaging
from .box import Box


def sort_reading_order(
    boxes: list[Box], image_width: int, column_count: int, line_tol: int = 10
) -> list[Box]:
    """移植自原桌面端的阅读顺序排序：先分栏，再按行（基线）从左到右。"""
    if column_count <= 1:
        return _sort_single(boxes, line_tol)

    col_width = image_width / float(column_count)
    columns: list[list[Box]] = [[] for _ in range(column_count)]
    for box in boxes:
        center = box.x + box.w / 2
        idx = min(int(center // col_width), column_count - 1)
        columns[idx].append(box)

    ordered: list[Box] = []
    for column in columns:
        ordered.extend(_sort_single(column, line_tol))
    return ordered


def _sort_single(boxes: list[Box], line_tol: int) -> list[Box]:
    entries = sorted(boxes, key=lambda b: b.y + b.h)
    lines: list[list[Box]] = []
    for box in entries:
        baseline = box.y + box.h
        for line in lines:
            if abs(baseline - (line[0].y + line[0].h)) <= line_tol:
                line.append(box)
                break
        else:
            lines.append([box])
    out: list[Box] = []
    for line in lines:
        out.extend(sorted(line, key=lambda b: b.x))
    return out


def sort_by_band(boxes: list[Box]) -> list[Box]:
    """中华字海：按栏（band）→ 行 → 横坐标。"""
    return sorted(boxes, key=lambda b: (b.band if b.band is not None else 0, b.y, b.x))


def sort_by_col(boxes: list[Box]) -> list[Box]:
    """汉字海：按栏（col）→ 纵坐标。"""
    return sorted(boxes, key=lambda b: (b.col if b.col is not None else 0, b.y, b.x))


def export_boxes(
    gray: np.ndarray,
    boxes: list[Box],
    out_dir: Path,
    *,
    prefix: str,
    page_tag: str,
    start_index: int = 1,
    digits: int = 3,
) -> list[dict]:
    """把每个框裁成 PNG 落盘，返回导出清单。文件名不与已有文件冲突。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    used: set[int] = set()
    for existing in out_dir.glob(f"{prefix}_*.png"):
        tail = existing.stem.rsplit("_", 1)[-1]
        if tail.isdigit():
            used.add(int(tail))

    records: list[dict] = []
    idx = start_index
    for box in boxes:
        while idx in used:
            idx += 1
        name = f"{prefix}_{idx:0{digits}d}.png"
        patch = imaging.crop(gray, box.x, box.y, box.w, box.h)
        imaging.save_png(out_dir / name, patch)
        records.append(
            {"file": name, "page": page_tag, "index": idx, "x": box.x, "y": box.y, "w": box.w, "h": box.h}
        )
        used.add(idx)
        idx += 1
    return records
