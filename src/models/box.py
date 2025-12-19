from dataclasses import dataclass
from typing import Literal


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int
    selected: bool = True
    source: Literal["auto", "manual"] = "auto"

    @property
    def area(self) -> int:
        return self.w * self.h


def iou_data(a: Box, b: Box) -> tuple[float, float, float]:
    x0 = max(a.x, b.x)
    y0 = max(a.y, b.y)
    x1 = min(a.x + a.w, b.x + b.w)
    y1 = min(a.y + a.h, b.y + b.h)
    if x1 <= x0 or y1 <= y0:
        return 0.0, a.area + b.area, 0.0

    inter = (x1 - x0) * (y1 - y0)
    union = a.area + b.area - inter
    min_area = min(a.area, b.area)
    return inter, union, min_area


IOU_THRESH: float = 0.7
COVER_THRESH: float = 0.95


def iou(a: Box, b: Box) -> float:
    inter, union, _ = iou_data(a, b)
    if union < 0.1:
        return 0.0
    return inter / union


def coverage_ratio(a: Box, b: Box) -> float:
    inter, _, min_area = iou_data(a, b)
    if min_area < 0.1:
        return 0.0
    return inter / min_area


def coverage_deduplication(boxes: list[Box]) -> list[Box]:
    sorted_boxes = sorted(boxes, key=lambda b: b.x * b.y)
    final_boxes: list[Box] = []
    for box in sorted_boxes:
        drop = False
        for kept in final_boxes:
            if coverage_ratio(box, kept) > COVER_THRESH:
                drop = True
                break
        if not drop:
            final_boxes.append(box)
    return final_boxes
