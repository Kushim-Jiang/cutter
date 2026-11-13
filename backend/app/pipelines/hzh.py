"""《汉字海》切图管线。

沿用 ``tools/hzh_crop.py`` 的定位算法：字头大字贴在每栏左缘、右侧紧跟条目序号，
用「栏左缘的大字墨迹 + 右侧有数字序号」定位字头框，并标出疑似漏切的属性行。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from .. import config
from ..core import imaging
from ..core.box import Box
from .base import DetectResult, Pipeline, param
from .upstream import hzh_crop

# {目录: {页号: [Box]}}，来自 _zihai/hh/ZI.ndjson + ZJ.ndjson（最终 Z 编号成果）
_SEED_CACHE: dict[str, dict[int, list[Box]]] = {}


class HzhPipeline(Pipeline):
    id = "hzh"
    name = "汉字海"
    description = "《汉字海》扫描 PDF：按栏左缘大字 + 右侧条目序号定位字头框。"

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings

    def _pdf(self) -> Path | None:
        pdf = self._require_file(str(self.settings.get("pdf", "")))
        return Path(pdf) if pdf else None

    def status(self) -> dict[str, Any]:
        pdf = self._pdf()
        if not pdf:
            return {
                "available": False,
                "reason": "未找到扫描 PDF，请在设置里指定《汉字海》PDF 路径",
                "source": str(self.settings.get("pdf", "")),
            }
        return {"available": True, "reason": "", "source": str(pdf), "pages": len(self.pages())}

    def params_spec(self) -> list[dict[str, Any]]:
        return [
            param(
                "pad",
                "裁切外扩",
                "number",
                0.0,
                min=0.0,
                max=0.5,
                step=0.02,
                help="按框尺寸比例向外扩，0 表示严格按框裁。",
            ),
        ]

    def _range(self) -> tuple[int, int, int]:
        return (
            int(self.settings.get("first_page", 452)),
            int(self.settings.get("last_page", 2648)),
            int(self.settings.get("offset", 451)),
        )

    def pages(self) -> list[dict[str, Any]]:
        pdf = self._pdf()
        if not pdf:
            return []
        first, last, offset = self._range()
        last = min(last, imaging.page_count(pdf))
        return [
            {"id": str(pno), "pdf_page": pno, "printed_page": pno - offset, "label": f"第 {pno - offset} 页"}
            for pno in range(first, last + 1)
        ]

    def _pno(self, page_id: str) -> int:
        return int(page_id)

    def _ink(self, page_id: str) -> np.ndarray:
        pdf = self._pdf()
        if not pdf:
            raise FileNotFoundError("《汉字海》PDF 未配置")
        return imaging.embedded_ink(pdf, self._pno(page_id))

    def gray(self, page_id: str) -> np.ndarray:
        return imaging.ink_to_gray(self._ink(page_id))

    def page_label(self, page_id: str) -> str:
        _, _, offset = self._range()
        return f"第 {self._pno(page_id) - offset} 页"

    def detect(self, page_id: str, params: dict[str, Any]) -> DetectResult:
        ink = self._ink(page_id)
        heads, pitch, margins, suspects = hzh_crop.page_boxes(ink)
        boxes = [
            Box(
                x=int(h["x"]),
                y=int(h["y"]),
                w=int(h["w"]),
                h=int(h["h"]),
                col=int(h.get("col", 0)) + 1,
                seq=i,
                pitch=float(pitch) or None,
            )
            for i, h in enumerate(heads, 1)
        ]
        return DetectResult(
            boxes=boxes,
            meta={
                "pitch": round(float(pitch), 1),
                "count": len(boxes),
                "margins": [int(m) for m in margins],
                "suspects": [
                    {k: int(v) for k, v in s.items() if isinstance(v, (int, float))} for s in suspects
                ],
            },
        )

    def order(self, boxes: list[Box]) -> list[Box]:
        from ..core.export import sort_by_col

        return sort_by_col(boxes)

    # ── 预置数据 ───────────────────────────────────────────
    def _seed_dir(self) -> Path:
        configured = self.settings.get("seed_dir")
        return Path(configured) if configured else config.DATA_DIR / "_zihai" / "hh"

    def _seed_index(self) -> dict[int, list[Box]]:
        base = self._seed_dir()
        if str(base) in _SEED_CACHE:
            return _SEED_CACHE[str(base)]

        grouped: dict[int, list[tuple[int, Box]]] = {}
        for name in ("ZI.ndjson", "ZJ.ndjson"):
            path = base / name
            if not path.is_file():
                continue
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                        page = int(row.get("page") or 0)
                        w, h = int(row["w"]), int(row["h"])
                        x, y = int(row["x"]), int(row["y"])
                    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                        continue
                    if not page or w <= 0 or h <= 0:
                        continue
                    entry = int(row.get("entry") or 0)
                    box = Box(x=x, y=y, w=w, h=h, col=row.get("col"), pos=row.get("ziid"), seq=row.get("seq"))
                    grouped.setdefault(page, []).append((entry, box))

        index: dict[int, list[Box]] = {}
        for page, rows in grouped.items():
            rows.sort(key=lambda item: (item[0] if item[0] > 0 else 10**9, item[1].y, item[1].x))
            for i, (_, box) in enumerate(rows, 1):
                box.seq = i
            index[page] = [box for _, box in rows]
        _SEED_CACHE[str(base)] = index
        return index

    def seed_boxes(self, page_id: str) -> tuple[list[Box], dict[str, Any]] | None:
        try:
            page = int(page_id)
        except (TypeError, ValueError):
            return None
        boxes = self._seed_index().get(page)
        if not boxes:
            return None
        sizes = sorted(max(b.w, b.h) for b in boxes)
        p50 = sizes[len(sizes) // 2]
        return list(boxes), {"seed": "ZI/ZJ", "pitch": p50, "page": page}

    def seed_done(self, page_id: str) -> bool:
        try:
            page = int(page_id)
        except (TypeError, ValueError):
            return False
        return bool(self._seed_index().get(page))

    def file_prefix(self, page_id: str) -> str:
        _, _, offset = self._range()
        return f"{self._pno(page_id) - offset:04d}"

    def export_image(
        self, page_id: str, box: Box, params: dict[str, Any], page_extra: dict[str, Any]
    ) -> np.ndarray:
        pad = float(params.get("pad", 0.0) or 0.0)
        if pad <= 0:
            return imaging.crop(self.gray(page_id), box.x, box.y, box.w, box.h)
        dx, dy = int(box.w * pad), int(box.h * pad)
        return imaging.crop(self.gray(page_id), box.x - dx, box.y - dy, box.w + 2 * dx, box.h + 2 * dy)
