"""《中华字海》切图管线（基准，参考 _zihai/editor 校对台）。

沿用 ``tools/zihai_grid.py`` 的版面网格算法：三栏定位 → 栏内行距/相位拟合 →
在栏左边距找字头。

* 支持多卷（卷1 / 卷2 …）：``settings.books.zihai.volumes`` 里每卷一份 PDF 与偏移；
  页号写作 ``<卷>:<PDF页>``，只配 ``pdf`` 时视为第 1 卷。
* 卷 2 这类扫描可配 ``box_margin``：自动框会再按墨迹收紧（tighten + refine），
  与 _zihai/editor 的「吸附墨迹」口径一致。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from .. import config
from ..core import imaging, refine
from ..core.box import Box
from .base import DetectResult, Pipeline, param
from .upstream import zihai_grid

# (目录, 卷) → {印刷页: [{x,y,w,h}]}（_zihai/editor/edits.json / edits_v2.json）
_EDITS_CACHE: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = {}
# (目录, 卷) → {印刷页: 完成时间}（progress.json / progress_v2.json）
_PROGRESS_CACHE: dict[tuple[str, str], dict[str, str]] = {}


class ZihaiPipeline(Pipeline):
    id = "zihai"
    name = "中华字海"
    description = "《中华字海》扫描 PDF 三栏版面：按栏内行网格找字头，可多卷、可按墨迹收紧。"

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings

    # ── 卷 ────────────────────────────────────────────────
    def volumes(self) -> list[dict[str, Any]]:
        configured = self.settings.get("volumes")
        out: list[dict[str, Any]] = []
        if isinstance(configured, dict) and configured:
            for key in sorted(configured, key=str):
                cfg = configured[key] or {}
                out.append(
                    {
                        "vol": str(key),
                        "pdf": cfg.get("pdf") or self.settings.get("pdf", ""),
                        "offset": int(cfg.get("offset", self.settings.get("offset", 83))),
                        "first_page": int(cfg.get("first_page", self.settings.get("first_page", 84))),
                        "last_page": int(cfg.get("last_page", self.settings.get("last_page", 1864))),
                        "box_margin": int(cfg.get("box_margin", 0)),
                        "title": cfg.get("title") or f"卷 {key}",
                    }
                )
        else:
            out.append(
                {
                    "vol": "1",
                    "pdf": self.settings.get("pdf", ""),
                    "offset": int(self.settings.get("offset", 83)),
                    "first_page": int(self.settings.get("first_page", 84)),
                    "last_page": int(self.settings.get("last_page", 1864)),
                    "box_margin": int(self.settings.get("box_margin", 0)),
                    "title": self.settings.get("title") or "卷 1",
                }
            )
        return out

    def _volume(self, vol: str) -> dict[str, Any] | None:
        for item in self.volumes():
            if item["vol"] == vol:
                return item
        return None

    def status(self) -> dict[str, Any]:
        available = []
        for item in self.volumes():
            path = self._require_file(item["pdf"])
            if path:
                available.append(
                    {
                        "vol": item["vol"],
                        "title": item["title"],
                        "label": item.get("label") or item["title"],
                        "pdf": str(path),
                    }
                )
        if not available:
            return {
                "available": False,
                "reason": "未找到扫描 PDF，请在设置里指定《中华字海》PDF 路径",
                "source": str(self.settings.get("pdf", "")),
            }
        return {
            "available": True,
            "reason": "",
            "source": available[0]["pdf"],
            "volumes": available,
            "pages": len(self.pages()),
        }

    def params_spec(self) -> list[dict[str, Any]]:
        return [
            param(
                "crop_mode",
                "裁切方式",
                "enum",
                "cell",
                options=[
                    {"value": "cell", "label": "字格（固定正方形）"},
                    {"value": "glyph", "label": "墨迹外扩"},
                ],
                help="cell：边长 = 2×行距，整页同尺寸；glyph：按字头墨迹外扩。",
            ),
            param(
                "margin",
                "外扩比例",
                "number",
                0.15,
                min=0.0,
                max=0.6,
                step=0.05,
                help="仅「墨迹外扩」方式有效。",
            ),
            param(
                "tighten",
                "自动框按墨迹收紧",
                "bool",
                False,
                help="对应校对台的「吸附墨迹」：丢掉框边的碎块再取墨迹外接框。",
            ),
            param("flag_review", "标红待查框", "bool", True, help="给尺寸离群或带孤立小墨点的框打标记。"),
        ]

    # ── 页 ────────────────────────────────────────────────
    def _split(self, page_id: str) -> tuple[dict[str, Any], int]:
        if ":" in page_id:
            vol, pno = page_id.split(":", 1)
        else:
            vol, pno = self.volumes()[0]["vol"], page_id
        volume = self._volume(vol)
        if not volume:
            raise KeyError(f"未知分卷：{vol}")
        return volume, int(pno)

    def pages(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for volume in self.volumes():
            pdf = self._require_file(volume["pdf"])
            if not pdf:
                continue
            total = imaging.page_count(Path(pdf))
            last = min(volume["last_page"], total)
            for pno in range(volume["first_page"], last + 1):
                printed = pno - volume["offset"]
                out.append(
                    {
                        "id": f"{volume['vol']}:{pno}",
                        "pdf_page": pno,
                        "printed_page": printed,
                        "volume": volume["vol"],
                        "label": f"{volume['title']} · 第 {printed} 页",
                    }
                )
        return out

    def _pdf(self, page_id: str) -> tuple[Path, int, dict[str, Any]]:
        volume, pno = self._split(page_id)
        pdf = self._require_file(volume["pdf"])
        if not pdf:
            raise FileNotFoundError(f"《中华字海》{volume['title']} PDF 未配置")
        return Path(pdf), pno, volume

    def ink(self, page_id: str) -> np.ndarray:
        pdf, pno, _ = self._pdf(page_id)
        return imaging.embedded_ink(pdf, pno)

    def gray(self, page_id: str) -> np.ndarray:
        return imaging.ink_to_gray(self.ink(page_id))

    def page_label(self, page_id: str) -> str:
        volume, pno = self._split(page_id)
        return f"{volume['title']} · 第 {pno - volume['offset']} 页"

    # ── 检测 ──────────────────────────────────────────────
    def detect(self, page_id: str, params: dict[str, Any]) -> DetectResult:
        ink = self.ink(page_id)
        heads = zihai_grid.find_heads(ink)
        side = zihai_grid.page_side(heads)
        boxes = [
            Box(
                x=int(h["x"]),
                y=int(h["y"]),
                w=int(h["w"]),
                h=int(h["h"]),
                band=int(h.get("band", 0)),
                line=int(h.get("line", 0)),
                seq=i,
                pitch=float(h.get("pitch", 0)) or None,
                cw=float(h.get("cw", 0)) or None,
            )
            for i, h in enumerate(heads, 1)
        ]

        _, _, volume = self._pdf(page_id)
        margin = int(params.get("box_margin", volume.get("box_margin", 0)) or 0)
        if params.get("tighten") or margin:
            raw = [b.as_dict() for b in boxes]
            tightened = refine.tighten_boxes(ink, raw, margin=margin or 2)
            tightened = refine.refine_to_ink(ink, tightened, margin=margin or 2)
            boxes = [Box.from_dict(data) for data in tightened]
            for i, box in enumerate(boxes, 1):
                box.seq = i

        flags: list[int] = []
        if params.get("flag_review", True):
            flags = refine.review_flags(ink, [b.as_dict() for b in boxes])

        return DetectResult(
            boxes=boxes, meta={"side": side, "count": len(boxes), "review": flags, "volume": volume["vol"]}
        )

    def order(self, boxes: list[Box]) -> list[Box]:
        from ..core.export import sort_by_band

        return sort_by_band(boxes)

    # ── 预置数据 ───────────────────────────────────────────
    def _seed_dir(self) -> Path:
        configured = self.settings.get("seed_dir")
        return Path(configured) if configured else config.DATA_DIR / "_zihai" / "editor"

    def _edits(self, vol: str) -> dict[str, list[dict[str, Any]]]:
        folder = self._seed_dir()
        key = (str(folder), vol)
        if key in _EDITS_CACHE:
            return _EDITS_CACHE[key]
        name = "edits.json" if vol == "1" else f"edits_v{vol}.json"
        path = folder / name
        data: dict[str, list[dict[str, Any]]] = {}
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data = raw
            except (OSError, json.JSONDecodeError):
                data = {}
        _EDITS_CACHE[key] = data
        return data

    def _progress(self, vol: str) -> dict[str, str]:
        folder = self._seed_dir()
        key = (str(folder), vol)
        if key in _PROGRESS_CACHE:
            return _PROGRESS_CACHE[key]
        name = "progress.json" if vol == "1" else f"progress_v{vol}.json"
        path = folder / name
        data: dict[str, str] = {}
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data = raw
            except (OSError, json.JSONDecodeError):
                data = {}
        _PROGRESS_CACHE[key] = data
        return data

    def _printed(self, page_id: str) -> tuple[dict[str, Any], int]:
        volume, pno = self._split(page_id)
        return volume, pno - volume["offset"]

    def seed_boxes(self, page_id: str) -> tuple[list[Box], dict[str, Any]] | None:
        volume, printed = self._printed(page_id)
        rows = self._edits(volume["vol"]).get(str(printed))
        if not rows:
            return None
        boxes: list[Box] = []
        for i, row in enumerate(rows, 1):
            try:
                w, h = round(float(row["w"])), round(float(row["h"]))
                x, y = round(float(row["x"])), round(float(row["y"]))
            except (KeyError, TypeError, ValueError):
                continue
            if w <= 0 or h <= 0:
                continue
            boxes.append(Box(x=x, y=y, w=w, h=h, seq=i))
        if not boxes:
            return None
        # 校对台导出时整页用同一个边长（96% 分位 × 1.30）
        sizes = sorted(max(b.w, b.h) for b in boxes)
        p95 = sizes[min(len(sizes) - 1, int(len(sizes) * 0.95))]
        side = round(1.30 * p95)
        return boxes, {"side": side, "seed": "edits", "volume": volume["vol"], "printed_page": printed}

    def seed_done(self, page_id: str) -> bool:
        volume, printed = self._printed(page_id)
        return str(printed) in self._progress(volume["vol"])

    # ── 导出 ──────────────────────────────────────────────
    def file_prefix(self, page_id: str) -> str:
        volume, pno = self._split(page_id)
        return f"{volume['vol']}{pno - volume['offset']:04d}"

    def export_image(
        self, page_id: str, box: Box, params: dict[str, Any], page_extra: dict[str, Any]
    ) -> np.ndarray:
        ink = self.ink(page_id)
        mode = params.get("crop_mode") or "cell"
        head: dict[str, Any] = {"x": box.x, "y": box.y, "w": box.w, "h": box.h}
        head["pitch"] = box.pitch or max(box.w, box.h)
        head["cw"] = box.cw or max(box.w, box.h)
        side = None if mode == "glyph" else page_extra.get("side")
        return zihai_grid.head_crop(ink, head, side=side)
