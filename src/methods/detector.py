from pathlib import Path
from typing import Iterable, Optional

import cv2
import numpy as np
from PIL import Image
from scipy.spatial import KDTree

from models.box import IOU_THRESH, Box

BORDER: int = 5
WHITE_GAP_RATIO: float = 0.12
MIN_AREA: int = 20


def flatten(indices: int | np.ndarray | Iterable[int]) -> list[int]:
    if isinstance(indices, (int, np.integer)):
        return [int(indices)]
    if isinstance(indices, np.ndarray):
        indices = indices.tolist()
    if isinstance(indices, Iterable) and not isinstance(indices, (str, bytes)):
        result = []
        for item in indices:
            result.extend(flatten(item))
        return result
    return [int(indices)]


def has_white_gap(
    image: Image.Image,
    box: Box,
    *,
    white_ratio_thresh: float = 0.03,
    margin_ratio: float = 0,
) -> bool:
    crop = image.crop((box.x, box.y, box.x + box.w, box.y + box.h)).convert("L")
    arr = np.array(crop)
    h, w = arr.shape

    binary = arr < 128
    black_ratio_per_row = binary.sum(axis=1) / w
    black_ratio_per_column = binary.sum(axis=0) / h

    top, bottom = int(h * margin_ratio), int(h * (1 - margin_ratio))
    left, right = int(w * margin_ratio), int(w * (1 - margin_ratio))

    row_slice: np.ndarray = black_ratio_per_row[top:bottom]
    row_mask = row_slice <= white_ratio_thresh
    row_diff = np.diff(np.concatenate([[0], row_mask.astype(int), [0]]))
    row_starts = np.where(row_diff == 1)[0]
    row_ends = np.where(row_diff == -1)[0]
    row_gap_max = (row_ends - row_starts).max() if len(row_starts) > 0 else 0
    has_horizontal_gap = (row_gap_max / h) >= WHITE_GAP_RATIO

    col_slice: np.ndarray = black_ratio_per_column[left:right]
    col_mask = col_slice <= white_ratio_thresh
    col_diff = np.diff(np.concatenate([[0], col_mask.astype(int), [0]]))
    col_starts = np.where(col_diff == 1)[0]
    col_ends = np.where(col_diff == -1)[0]
    col_gap_max = (col_ends - col_starts).max() if len(col_starts) > 0 else 0
    has_vertical_gap = (col_gap_max / w) >= WHITE_GAP_RATIO

    return has_horizontal_gap or has_vertical_gap


def detect_image(
    image_path: Path,
    W_RANGE: Optional[tuple[int, int]] = None,
    H_RANGE: Optional[tuple[int, int]] = None,
) -> list[Box]:
    image = Image.open(image_path).convert("L")
    cv_image = np.array(image)
    _, bw_arr = cv2.threshold(cv_image, 128, 255, cv2.THRESH_BINARY_INV)
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(bw_arr, connectivity=8)

    components: list[Box] = []
    for i in range(1, num_labels):
        x = stats[i, cv2.CC_STAT_LEFT]
        y = stats[i, cv2.CC_STAT_TOP]
        w = stats[i, cv2.CC_STAT_WIDTH]
        h = stats[i, cv2.CC_STAT_HEIGHT]

        area = stats[i, cv2.CC_STAT_AREA]
        if area >= MIN_AREA:
            components.append(Box(x, y, w, h))
    if not components:
        return []

    centers = np.array([(b.x + b.w / 2, b.y + b.h / 2) for b in components], dtype=np.float32)
    tree = KDTree(centers)

    def merge_boxes(boxes: list[Box]) -> Box:
        if not boxes:
            return Box(0, 0, 0, 0)
        xs = np.array([b.x for b in boxes])
        ys = np.array([b.y for b in boxes])
        x1s = np.array([b.x + b.w for b in boxes])
        y1s = np.array([b.y + b.h for b in boxes])

        x0 = xs.min()
        y0 = ys.min()
        x1 = x1s.max()
        y1 = y1s.max()
        return Box(x0, y0, x1 - x0, y1 - y0)

    candidates: list[Box] = []
    has_w_range = W_RANGE is not None
    has_h_range = H_RANGE is not None

    for i, seed in enumerate(components):
        used_indices = {i}
        used_boxes = [seed]
        best_valid: Optional[Box] = None
        found_new = True

        while found_new:
            current = merge_boxes(used_boxes)
            if (has_w_range and current.w > W_RANGE[1]) or (has_h_range and current.h > H_RANGE[1]):
                break
            if has_w_range and has_h_range:
                if W_RANGE[0] <= current.w <= W_RANGE[1] and H_RANGE[0] <= current.h <= H_RANGE[1]:
                    best_valid = current

            center = np.array([[current.x + current.w / 2, current.y + current.h / 2]])
            k = min(8, len(components) - len(used_indices))
            if k <= 0:
                break

            _, indices = tree.query(center, k=k)
            indices = np.atleast_1d(indices).reshape(-1)
            found_new = False
            for idx in indices:
                idx = int(idx)
                if idx in used_indices:
                    continue

                candidate_box = components[idx]
                tmp_boxes = used_boxes + [candidate_box]
                tmp_merged = merge_boxes(tmp_boxes)
                if (has_w_range and tmp_merged.w > W_RANGE[1]) or (has_h_range and tmp_merged.h > H_RANGE[1]):
                    continue

                used_indices.add(idx)
                used_boxes.append(candidate_box)
                found_new = True
                break

        if best_valid:
            candidates.append(best_valid)

    if not candidates:
        return []

    boxes_np = np.array([[b.x, b.y, b.x + b.w, b.y + b.h] for b in candidates], dtype=np.float32)
    scores = np.array([b.area for b in candidates], dtype=np.float32)
    indices = cv2.dnn.NMSBoxes(boxes_np[:, :4].tolist(), scores.tolist(), score_threshold=0.0, nms_threshold=IOU_THRESH)

    final: list[Box] = []
    for idx in flatten(indices):
        idx_int = int(idx)
        box = candidates[idx_int]
        if has_white_gap(image, box):
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

    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    if not np.any(rows) or not np.any(cols):
        return None

    min_y, max_y = np.where(rows)[0][[0, -1]]
    min_x, max_x = np.where(cols)[0][[0, -1]]
    return Box(
        x=x0 + min_x - BORDER,
        y=y0 + min_y - BORDER,
        w=max_x - min_x + 1 + BORDER * 2,
        h=max_y - min_y + 1 + BORDER * 2,
        selected=True,
        source="manual",
    )
