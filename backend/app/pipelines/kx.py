"""康熙字典（内府本）切图管线。

沿用 ``tools/kx_locate.py::big_glyphs``：粗笔画掩码 → 合并成「大字」连通块 →
拆开粘连的竖排块。检出的是页面上的字头大字框，供人工复核后导出。

坐标空间固定为 ``detect_zoom``（默认 300dpi，与上游口径一致），导出时按同一缩放取图。
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

import numpy as np

from .. import config
from ..core import imaging
from ..core.box import Box
from .base import DetectResult, Pipeline, param
from .upstream import kx_locate

EXPORT_ZOOM = 300 / 72.0

# {源文件: {(册, PDF页): [Box]}}，来自 kangxi-1716/sources/字头清单.csv（或 manifest.json）
_SEED_CACHE: dict[str, dict[tuple[str, int], list[Box]]] = {}
_HEAD_CATEGORIES = {"字头", "重复字头"}


class KxPipeline(Pipeline):
    id = "kx"
    name = "康熙字典（内府本）"
    description = "康熙内府本 13 册 300dpi 扫描：按粗笔画定位字头大字，位置号 K+页+位。"

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings

    # ── 书源 ──────────────────────────────────────────────
    def _book_dir(self) -> Path | None:
        path = self.settings.get("book_dir") or ""
        p = Path(path)
        return p if path and p.is_dir() else None

    def _volumes(self) -> list[dict[str, Any]]:
        book_dir = self._book_dir()
        if not book_dir:
            return []
        out = []
        for pdf in sorted(book_dir.glob("*.pdf")):
            match = re.search(r"康熙字典\.(\d+)", pdf.name)
            vol = match.group(1) if match else pdf.stem
            title = pdf.stem.split(".", 2)[-1]
            # 「子集.三卷.清.张玉书等纂.清康熙五十五年内府刊本」→「子集」
            short = title.split(".")[0][:6] or title[:6]
            out.append({"vol": vol, "title": title, "label": f"{vol} {short}", "pdf": pdf})
        return out

    def _volume(self, vol: str) -> dict[str, Any] | None:
        for item in self._volumes():
            if item["vol"] == vol:
                return item
        return None

    def status(self) -> dict[str, Any]:
        volumes = self._volumes()
        if not volumes:
            return {
                "available": False,
                "reason": "未找到康熙字典 PDF 目录（settings.books.kx.book_dir）",
                "source": str(self.settings.get("book_dir", "")),
            }
        return {
            "available": True,
            "reason": "",
            "source": str(self._book_dir()),
            "pages": sum(len(self._pages_of(v)) for v in volumes),
            "volumes": [{"vol": v["vol"], "title": v["title"], "label": v["label"]} for v in volumes],
        }

    def zoom(self) -> float:
        return float(self.settings.get("zoom", EXPORT_ZOOM))

    def params_spec(self) -> list[dict[str, Any]]:
        return [
            param(
                "thick_px",
                "粗笔画核",
                "int",
                9,
                min=3,
                max=30,
                step=1,
                help="小字的细笔画会被这个核滤掉，只留字头大字。",
            ),
            param("join_px", "合并距离", "int", 14, min=3, max=40, step=1),
            param("min_side", "最小边长", "int", 95, min=20, max=400, step=5),
            param("min_area", "最小面积", "int", 1500, min=100, max=20000, step=100),
            param("pad", "裁切外扩", "number", 0.06, min=0.0, max=0.5, step=0.01),
        ]

    # ── 页 ────────────────────────────────────────────────
    def _pages_of(self, volume: dict[str, Any]) -> list[dict[str, Any]]:
        total = imaging.page_count(volume["pdf"])
        return [
            {
                "id": f"{volume['vol']}:{pno}",
                "pdf_page": pno,
                "printed_page": pno,
                "volume": volume["vol"],
                "label": f"{volume['title']} · 第 {pno} 页",
            }
            for pno in range(1, total + 1)
        ]

    def pages(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for volume in self._volumes():
            out.extend(self._pages_of(volume))
        return out

    def _split(self, page_id: str) -> tuple[dict[str, Any], int]:
        if ":" not in page_id:
            raise KeyError(f"康熙页号格式是「分册:页」，例如 07:100（收到 {page_id!r}）")
        vol, pno = page_id.split(":", 1)
        volume = self._volume(vol)
        if not volume:
            # 没配 PDF 目录时也允许读预置数据（seed）；取图会因 pdf 为空而报错
            if not self._volumes():
                volume = {"vol": vol, "title": f"卷 {vol}", "pdf": ""}
            else:
                raise KeyError(f"未知分册：{vol}")
        return volume, int(pno)

    def gray(self, page_id: str) -> np.ndarray:
        volume, pno = self._split(page_id)
        return imaging.render_gray(volume["pdf"], pno, self.zoom())

    def page_label(self, page_id: str) -> str:
        volume, pno = self._split(page_id)
        return f"{volume['title']} · 第 {pno} 页"

    # ── 检测 ──────────────────────────────────────────────
    def detect(self, page_id: str, params: dict[str, Any]) -> DetectResult:
        gray = self.gray(page_id)
        rects = kx_locate.big_glyphs(
            gray,
            thick_px=int(params.get("thick_px", 9)),
            join_px=int(params.get("join_px", 14)),
            min_side=int(params.get("min_side", 95)),
            min_area=int(params.get("min_area", 1500)),
        )
        volume, pno = self._split(page_id)
        boxes = []
        for i, (x0, y0, x1, y1) in enumerate(rects, 1):
            boxes.append(
                Box(
                    x=int(x0),
                    y=int(y0),
                    w=int(x1 - x0 + 1),
                    h=int(y1 - y0 + 1),
                    seq=i,
                    pos=kx_pos_key(pno, i),
                )
            )
        return DetectResult(
            boxes=boxes, meta={"count": len(boxes), "zoom": self.zoom(), "volume": volume["vol"], "page": pno}
        )

    def order(self, boxes: list[Box]) -> list[Box]:
        # 内府本竖排右起：先按 x 中心从右到左，再按 y
        if not boxes:
            return []
        return sorted(boxes, key=lambda b: (-(b.x + b.w), b.y))

    def file_prefix(self, page_id: str) -> str:
        volume, pno = self._split(page_id)
        return f"K{volume['vol']}P{pno:04d}"

    # ── 预置数据（字典已经切好的字头矩形） ─────────────────
    def _seed_source(self) -> Path:
        configured = self.settings.get("seed_file")
        if configured:
            return Path(configured)
        base = self.settings.get("seed_dir")
        folder = Path(base) if base else config.DATA_DIR / "_up" / "kangxi" / "kangxi-1716" / "sources"
        csv_path = folder / "字头清单.csv"
        return csv_path if csv_path.is_file() else folder / "manifest.json"

    @staticmethod
    def _rows_from_source(path: Path) -> list[dict[str, Any]]:
        if path.suffix.lower() == ".csv":
            with path.open(encoding="utf-8-sig", newline="") as fh:
                return list(csv.DictReader(fh))
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [r for r in raw if isinstance(r, dict)]

    def _seed_index(self) -> dict[tuple[str, int], list[Box]]:
        path = self._seed_source()
        if str(path) in _SEED_CACHE:
            return _SEED_CACHE[str(path)]

        grouped: dict[tuple[str, int], list[tuple[int, Box]]] = {}
        if path.is_file():
            for row in self._rows_from_source(path):
                if str(row.get("类别", "")).strip() not in _HEAD_CATEGORIES:
                    continue
                try:
                    volume = str(row.get("册", "")).strip().zfill(2)
                    page = int(str(row.get("PDF页", "")).strip())
                    x0 = int(float(row.get("页面x0") or 0))
                    y0 = int(float(row.get("页面y0") or 0))
                    x1 = int(float(row.get("页面x1") or 0))
                    y1 = int(float(row.get("页面y1") or 0))
                except (TypeError, ValueError):
                    continue
                w, h = x1 - x0, y1 - y0
                if not volume or page <= 0 or w <= 0 or h <= 0:
                    continue
                seq_text = str(row.get("页内序号") or "").strip()
                order = int(seq_text) if seq_text.isdigit() else 10**6
                box = Box(x=x0, y=y0, w=w, h=h, pos=row.get("Unicode") or None)
                grouped.setdefault((volume, page), []).append((order, box))

        index: dict[tuple[str, int], list[Box]] = {}
        for key, rows in grouped.items():
            rows.sort(key=lambda item: (item[0], item[1].y, -item[1].x))
            page = key[1]
            for i, (_, box) in enumerate(rows, 1):
                box.seq = i
                box.pos = kx_pos_key(page, i)
            index[key] = [box for _, box in rows]
        _SEED_CACHE[str(path)] = index
        return index

    def seed_boxes(self, page_id: str) -> tuple[list[Box], dict[str, Any]] | None:
        volume, pno = self._split(page_id)
        boxes = self._seed_index().get((volume["vol"], pno))
        if not boxes:
            return None
        return list(boxes), {"seed": "字头清单", "volume": volume["vol"], "page": pno}

    def seed_done(self, page_id: str) -> bool:
        volume, pno = self._split(page_id)
        return bool(self._seed_index().get((volume["vol"], pno)))

    def export_image(
        self, page_id: str, box: Box, params: dict[str, Any], page_extra: dict[str, Any]
    ) -> np.ndarray:
        pad = float(params.get("pad", 0.06) or 0.0)
        dx, dy = int(box.w * pad), int(box.h * pad)
        return imaging.crop(self.gray(page_id), box.x - dx, box.y - dy, box.w + 2 * dx, box.h + 2 * dy)


def kx_pos_key(page: int, index: int) -> str:
    """隐编号：K + 页百位(0-Z) + 页后两位 + 位次（与上游 build_kx_sprites 同口径）。"""
    b36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return f"K{b36[min(page // 100, 35)]}{page % 100:02d}{index:02d}"
