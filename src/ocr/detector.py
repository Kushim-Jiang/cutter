from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageFilter
from scipy.spatial import KDTree

from models.box import Box

BORDER: int = 5


def adaptive_threshold(image: Image.Image, block_size: int = 15, c: int = 10) -> Image.Image:
    img_np = np.array(image)
    mean = Image.fromarray(img_np).filter(ImageFilter.BoxBlur(block_size // 2))
    mean_np = np.array(mean)
    thresh_np = (img_np < (mean_np - c)).astype(np.uint8) * 255
    return Image.fromarray(thresh_np).convert("1")


def has_horizontal_white_gap(
    image: Image.Image,
    box: Box,
    *,
    white_ratio_thresh: float = 0.03,
    min_gap_ratio: float = 0.12,
    margin_ratio: float = 0.15,
) -> bool:
    # crop area
    crop = image.crop((box.x, box.y, box.x + box.w, box.y + box.h)).convert("L")

    arr = np.array(crop)
    h, w = arr.shape

    binary = arr < 128
    black_ratio_per_row = binary.sum(axis=1) / w

    top = int(h * margin_ratio)
    bottom = int(h * (1 - margin_ratio))

    gap_start = None
    max_gap = 0

    for y in range(top, bottom):
        if black_ratio_per_row[y] <= white_ratio_thresh:
            if gap_start is None:
                gap_start = y
        else:
            if gap_start is not None:
                gap_height = y - gap_start
                max_gap = max(max_gap, gap_height)
                gap_start = None

    if gap_start is not None:
        max_gap = max(max_gap, bottom - gap_start)

    return max_gap / h >= min_gap_ratio


def detect_text_regions(
    image_path: Path,
    W_RANGE: Optional[tuple[int, int]] = None,
    H_RANGE: Optional[tuple[int, int]] = None,
) -> list[Box]:
    IOU_THRESH: float = 0.7

    # Step 1. binarization and connected component analysis
    image = Image.open(image_path).convert("L")
    bw = image.point(lambda x: 0 if x < 128 else 255, "1")
    pixels = bw.load()
    width, height = bw.size

    visited: set[tuple[int, int]] = set()
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

    def iou(a: Box, b: Box) -> float:
        x0 = max(a.x, b.x)
        y0 = max(a.y, b.y)
        x1 = min(a.x + a.w, b.x + b.w)
        y1 = min(a.y + a.h, b.y + b.h)
        if x1 <= x0 or y1 <= y0:
            return 0.0
        inter = (x1 - x0) * (y1 - y0)
        union = a.w * a.h + b.w * b.h - inter
        return inter / union

    # Step 3. region growing
    candidates: list[Box] = []

    for i, seed in enumerate(components):
        used_indices = {i}
        used_boxes = [seed]
        best_valid: Box | None = None

        while True:
            current = merge_boxes(used_boxes)

            # upper bound cutoff
            if current.w > W_RANGE[1] or current.h > H_RANGE[1]:
                break

            # valid record
            if W_RANGE[0] <= current.w <= W_RANGE[1] and H_RANGE[0] <= current.h <= H_RANGE[1]:
                best_valid = current

            # KD-tree query for nearest neighbors
            center = np.array([[current.x + current.w / 2, current.y + current.h / 2]])
            _, idxs = tree.query(center, k=min(8, len(components)))
            found = False
            for idx in idxs[0]:
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
    final: list[Box] = []
    for box in sorted(candidates, key=lambda b: b.w * b.h, reverse=True):
        if has_horizontal_white_gap(image, box):
            continue
        if all(iou(box, kept) < IOU_THRESH for kept in final):
            final.append(Box(box.x - BORDER, box.y - BORDER, box.w + BORDER * 2, box.h + BORDER * 2))

    return final


def filter_boxes(
    boxes: list[Box],
    w_range: Optional[tuple[int, int]] = None,
    h_range: Optional[tuple[int, int]] = None,
) -> list[Box]:
    filtered = []
    for box in boxes:
        keep = True
        if w_range is not None:
            keep = keep and (w_range[0] + BORDER * 2 <= box.w <= w_range[1] + BORDER * 2)
        if h_range is not None:
            keep = keep and (h_range[0] + BORDER * 2 <= box.h <= h_range[1] + BORDER * 2)
        if keep:
            filtered.append(box)
    return filtered
