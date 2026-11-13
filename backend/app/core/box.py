"""矩形框模型与去重（移植自原桌面端 models/box.py）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

IOU_THRESH: float = 0.7
COVER_THRESH: float = 0.95
BORDER: int = 5


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int
    selected: bool = True
    source: Literal["auto", "manual"] = "auto"
    # 书源附加信息：中华字海用 band/line，汉字海用 col，康熙用 pos
    band: int | None = None
    line: int | None = None
    col: int | None = None
    seq: int | None = None
    pos: str | None = None
    # 书源版面参数（导出裁切要用）：行距、字格宽
    pitch: float | None = None
    cw: float | None = None

    @property
    def area(self) -> int:
        return self.w * self.h

    def as_dict(self) -> dict:
        data = {
            "x": self.x,
            "y": self.y,
            "w": self.w,
            "h": self.h,
            "selected": self.selected,
            "source": self.source,
        }
        for name in ("band", "line", "col", "seq", "pos", "pitch", "cw"):
            value = getattr(self, name)
            if value is not None:
                data[name] = value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Box:
        return cls(
            x=int(data["x"]),
            y=int(data["y"]),
            w=int(data["w"]),
            h=int(data["h"]),
            selected=bool(data.get("selected", True)),
            source=data.get("source", "auto"),
            band=data.get("band"),
            line=data.get("line"),
            col=data.get("col"),
            seq=data.get("seq"),
            pos=data.get("pos"),
            pitch=data.get("pitch"),
            cw=data.get("cw"),
        )


def _intersection(a: Box, b: Box) -> tuple[float, float, float]:
    x0 = max(a.x, b.x)
    y0 = max(a.y, b.y)
    x1 = min(a.x + a.w, b.x + b.w)
    y1 = min(a.y + a.h, b.y + b.h)
    if x1 <= x0 or y1 <= y0:
        return 0.0, a.area + b.area, 0.0
    inter = (x1 - x0) * (y1 - y0)
    union = a.area + b.area - inter
    return float(inter), float(union), float(min(a.area, b.area))


def iou(a: Box, b: Box) -> float:
    inter, union, _ = _intersection(a, b)
    return 0.0 if union < 0.1 else inter / union


def coverage_ratio(a: Box, b: Box) -> float:
    inter, _, min_area = _intersection(a, b)
    return 0.0 if min_area < 0.1 else inter / min_area


def coverage_deduplication(boxes: list[Box], thresh: float = COVER_THRESH) -> list[Box]:
    """后框若被先框覆盖 95% 以上则丢弃（先按面积小的优先保留）。"""
    ordered = sorted(boxes, key=lambda b: b.x * b.y)
    kept: list[Box] = []
    for box in ordered:
        if any(coverage_ratio(box, existing) > thresh for existing in kept):
            continue
        kept.append(box)
    return kept
