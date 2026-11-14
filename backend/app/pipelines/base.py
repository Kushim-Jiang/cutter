"""书源（切图管线）公共接口。

统一成「书 → 页 → 框 → 导出」四步，所有字典在界面上操作方式一致：
中华字海是基准（版面网格检测），汉字海/康熙/汉语大字典各自提供 detection 与导出细节。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..core import imaging
from ..core.box import Box


@dataclass
class DetectResult:
    boxes: list[Box]
    meta: dict[str, Any] = field(default_factory=dict)


def param(key: str, label: str, type: str, default: Any, **extra: Any) -> dict[str, Any]:
    data = {"key": key, "label": label, "type": type, "default": default}
    data.update(extra)
    return data


class Pipeline:
    id: str = ""
    name: str = ""
    description: str = ""

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings

    # ── 状态 ──────────────────────────────────────────────
    def status(self) -> dict[str, Any]:
        raise NotImplementedError

    def params_spec(self) -> list[dict[str, Any]]:
        return []

    def defaults(self) -> dict[str, Any]:
        return {p["key"]: p["default"] for p in self.params_spec()}

    # ── 页 ────────────────────────────────────────────────
    def pages(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def gray(self, page_id: str) -> np.ndarray:
        raise NotImplementedError

    def ink(self, page_id: str) -> np.ndarray:
        """二值墨迹（1=墨）。默认从灰度阈值来，书源有原始 1bit 图时覆写。"""
        return imaging.gray_to_ink(self.gray(page_id))

    def page_size(self, page_id: str) -> tuple[int, int]:
        gray = self.gray(page_id)
        return int(gray.shape[1]), int(gray.shape[0])

    def preview(self, page_id: str, max_width: int) -> bytes:
        return imaging.encode_jpeg(imaging.downscale(self.gray(page_id), max_width))

    def crop_png(self, page_id: str, x: int, y: int, w: int, h: int, max_width: int) -> bytes:
        patch = imaging.crop(self.gray(page_id), x, y, w, h)
        return imaging.encode_png(imaging.downscale(patch, max_width))

    # ── 检测 / 导出 ───────────────────────────────────────
    def detect(self, page_id: str, params: dict[str, Any]) -> DetectResult:
        raise NotImplementedError

    # ── 预置数据（上游已切好的框） ─────────────────────────
    def seed_boxes(self, page_id: str) -> tuple[list[Box], dict[str, Any]] | None:
        """返回上游已经切好/校对好的框；没有就 None。

        `yedict-data` 里各书源都有定稿数据（中华字海 edits.json、汉字海 ZI/ZJ、
        康熙 sources/字头清单.csv）。默认用它，打开页面即是既有成果，而不是当场重跑检测。
        """
        return None

    def seed_done(self, page_id: str) -> bool:
        """上游是否把这一页标记为已完成（有定稿即视为完成）。"""
        return False

    def seed_counts_done(self) -> bool:
        """「有定稿」算不算「已校对完」。

        中华字海/汉字海/康熙的定稿来自已经做完的人工校对，算完成；
        汉语大字典第一版的定稿是自动检测的初稿，还要人工过一遍，所以不算。
        """
        return True

    def seed_kind(self, page_id: str) -> str:
        """这一页的预置框是从哪来的（各书源自定义，界面用来区分「定稿/预测」）。"""
        return "upstream" if self.seed_done(page_id) else ""

    def first_seeded_page(self, page_ids: list[str]) -> str | None:
        """第一个有定稿数据的页（界面打开书籍时直接落在这一页）。"""
        for page_id in page_ids:
            if self.seed_done(page_id):
                return page_id
        return None

    def refine_boxes(self, page_id: str, boxes: list[Box], params: dict[str, Any]) -> list[Box]:
        """贴合成墨迹（对应校对台的「贴合墨迹 / 本页全部重贴」）。"""
        from ..core import refine

        ink = self.ink(page_id)
        margin = int(params.get("margin", 2) or 2)
        raw = [b.as_dict() for b in boxes]
        tightened = refine.tighten_boxes(ink, raw, margin=margin)
        tightened = refine.refine_to_ink(ink, tightened, margin=margin)
        out: list[Box] = []
        for i, data in enumerate(tightened, 1):
            box = Box.from_dict(data)
            box.seq = i
            out.append(box)
        return out

    def review_flags(self, page_id: str, boxes: list[Box]) -> list[int]:
        from ..core import refine

        return refine.review_flags(self.ink(page_id), [b.as_dict() for b in boxes])

    def order(self, boxes: list[Box]) -> list[Box]:
        return sorted(boxes, key=lambda b: (b.y, b.x))

    def file_prefix(self, page_id: str) -> str:
        return page_id

    def export_image(
        self, page_id: str, box: Box, params: dict[str, Any], page_extra: dict[str, Any]
    ) -> np.ndarray:
        return imaging.crop(self.gray(page_id), box.x, box.y, box.w, box.h)

    def export_images(
        self, page_id: str, boxes: list[Box], params: dict[str, Any], page_extra: dict[str, Any]
    ) -> list[np.ndarray]:
        """批量导出钩子：默认逐框裁；渲染开销大的书源可覆写成一次渲染。"""
        return [self.export_image(page_id, box, params, page_extra) for box in boxes]

    def export_digits(self) -> int:
        return 3

    # ── 辅助 ──────────────────────────────────────────────
    def page_label(self, page_id: str) -> str:
        return page_id

    def _resolve(self, params: dict[str, Any], key: str) -> Any:
        if key in params and params[key] is not None:
            return params[key]
        return self.defaults().get(key)

    def _require_file(self, path: str) -> Any | None:
        from pathlib import Path

        if not path:
            return None
        p = Path(path)
        return p if p.exists() else None
