from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image
from scipy.spatial import KDTree

from models.box import IOU_THRESH, Box, iou

BORDER: int = 5


def has_white_gap(
    image: Image.Image,
    box: Box,
    *,
    white_ratio_thresh: float = 0.03,
    min_gap_ratio: float = 0.12,
    margin_ratio: float = 0.15,
) -> bool:
    crop = image.crop((box.x, box.y, box.x + box.w, box.y + box.h)).convert("L")
    arr = np.array(crop)
    h, w = arr.shape

    binary = arr < 128
    black_ratio_per_row, black_ratio_per_column = binary.sum(axis=1) / w, binary.sum(axis=0) / h

    top, bottom = int(h * margin_ratio), int(h * (1 - margin_ratio))
    left, right = int(w * margin_ratio), int(w * (1 - margin_ratio))

    row_gap_start = None
    row_gap_max = 0
    for y in range(top, bottom):
        if black_ratio_per_row[y] <= white_ratio_thresh:
            if row_gap_start is None:
                row_gap_start = y
        else:
            if row_gap_start is not None:
                row_gap_max = max(row_gap_max, y - row_gap_start)
                row_gap_start = None
    if row_gap_start is not None:
        row_gap_max = max(row_gap_max, bottom - row_gap_start)
    has_horizontal_gap = (row_gap_max / h) >= min_gap_ratio

    col_gap_start = None
    col_gap_max = 0
    for x in range(left, right):
        if black_ratio_per_column[x] <= white_ratio_thresh:
            if col_gap_start is None:
                col_gap_start = x
        else:
            if col_gap_start is not None:
                col_gap_max = max(col_gap_max, x - col_gap_start)
                col_gap_start = None
    if col_gap_start is not None:
        col_gap_max = max(col_gap_max, right - col_gap_start)
    has_vertical_gap = (col_gap_max / w) >= min_gap_ratio

    return has_horizontal_gap or has_vertical_gap


def detect_image(
    image_path: Path,
    W_RANGE: Optional[tuple[int, int]] = None,
    H_RANGE: Optional[tuple[int, int]] = None,
) -> list[Box]:
    # Step 1. binarization and connected component analysis
    image = Image.open(image_path).convert("L")
    bw = image.point(lambda x: 0 if x < 128 else 255, "1")
    pixels = bw.load()
    width, height = bw.size

    visited = set()
    components: list[Box] = []

    def neighbors(x: int, y: int):
        for nx in (x - 1, x, x + 1):
            for ny in (y - 1, y, y + 1):
                if 0 <= nx < width and 0 <= ny < height:
                    yield nx, ny

    def bfs(sx: int, sy: int) -> Box:
        stack = [(sx, sy)]
        visited.add((sx, sy))
        min_x = max_x = sx
        min_y = max_y = sy

        while stack:
            x, y = stack.pop()
            for nx, ny in neighbors(x, y):
                if (nx, ny) not in visited and pixels[nx, ny] == 0:
                    visited.add((nx, ny))
                    stack.append((nx, ny))
                    min_x = min(min_x, nx)
                    max_x = max(max_x, nx)
                    min_y = min(min_y, ny)
                    max_y = max(max_y, ny)

        return Box(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)

    for x in range(width):
        for y in range(height):
            if pixels[x, y] == 0 and (x, y) not in visited:
                box = bfs(x, y)
                if box.w > 2 and box.h > 2:
                    components.append(box)

    if not components:
        return []

    # Step 2. KD-tree prepare
    centers = np.array([(b.x + b.w / 2, b.y + b.h / 2) for b in components])
    tree = KDTree(centers)

    def merge_boxes(boxes: list[Box]) -> Box:
        x0 = min(b.x for b in boxes)
        y0 = min(b.y for b in boxes)
        x1 = max(b.x + b.w for b in boxes)
        y1 = max(b.y + b.h for b in boxes)
        return Box(x0, y0, x1 - x0, y1 - y0)

    # Step 3. region growing
    candidates: list[Box] = []

    for i, seed in enumerate(components):
        used_indices = {i}
        used_boxes = [seed]
        best_valid: Optional[Box] = None

        while True:
            current = merge_boxes(used_boxes)

            if W_RANGE and (current.w > W_RANGE[1]) or H_RANGE and (current.h > H_RANGE[1]):
                break

            if (
                W_RANGE
                and H_RANGE
                and (W_RANGE[0] <= current.w <= W_RANGE[1] and H_RANGE[0] <= current.h <= H_RANGE[1])
            ):
                best_valid = current

            center = np.array([[current.x + current.w / 2, current.y + current.h / 2]])
            _, indices = tree.query(center, k=min(8, len(components)))
            found = False
            for idx in indices[0]:
                if idx not in used_indices:
                    used_indices.add(idx)
                    used_boxes.append(components[idx])
                    found = True
                    break
            if not found:
                break

        if best_valid:
            candidates.append(best_valid)

    # Step 4. deduplication
    sorted_candidates = sorted(candidates, key=lambda b: b.w * b.h, reverse=True)
    final: list[Box] = []

    for box in sorted_candidates:
        if has_white_gap(image, box):
            continue
        if any(iou(box, kept) >= IOU_THRESH for kept in final):
            continue
        final.append(Box(box.x - BORDER, box.y - BORDER, box.w + BORDER * 2, box.h + BORDER * 2))
    return final


def detect_selection(image: Image.Image, rect: Box) -> Optional[Box]:
    x0, y0 = rect.x, rect.y
    x1, y1 = rect.x + rect.w, rect.y + rect.h

    crop = image.crop((x0, y0, x1, y1)).convert("L")
    arr = np.array(crop)
    mask = arr < 128

    if not mask.any():
        return None

    ys, xs = np.where(mask)
    min_x, max_x = xs.min(), xs.max()
    min_y, max_y = ys.min(), ys.max()

    return Box(
        x=x0 + min_x - BORDER,
        y=y0 + min_y - BORDER,
        w=max_x - min_x + 1 + BORDER * 2,
        h=max_y - min_y + 1 + BORDER * 2,
        selected=True,
        source="manual",
    )
