"""《汉语大字典》切图管线（第一版 / 第二版两册）。

坐标空间 = 按 `dpi`（默认 **300**，与上游数据一致）渲染的整页灰度图。

两版版式相同：双栏密排，字头比正文大得多，条目开头还排古文字形示例（也是大字）。
上游 `_up/handa/` 的结论是单靠形状分不开，于是：

* **第一版**（无文字层）`hd_heads.py`：连通域取大字块 + 每栏只留最左一列，
  再由 `hd_align.py` 用检字表索引顺序对齐补全 → 定稿 `data/hd1-heads.ndjson`。
  本项目直接用定稿当 seed，检测时跑上游那一步版面法。
* **第二版**（有文字层）`hd_v2.py`：字号 ≥26pt 的单字 span 当候选，同样每栏留最左一列，
  并用 `index/hd2.csv` 里该页字头序列过滤掉古文字形。

页号 `<册>:<1 基 PDF 页>`；第一版印刷页 = PDF 页 − 296，第二版印刷页 = PDF 页。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, ClassVar

import numpy as np

from .. import config
from ..core import imaging
from ..core.box import Box
from .base import DetectResult, Pipeline, param
from .upstream import hd_heads, hd_v2

# {源文件: {1 基 PDF 页: [(col, x, y, w, h)]}}，来自 hd1-heads.ndjson（300dpi 坐标）
_SEED_CACHE: dict[str, dict[int, list[tuple[int, int, int, int, int]]]] = {}
# {csv 路径: {页: [字头…]}}，来自 hd2.csv
_INDEX_CACHE: dict[str, dict[int, list[str]]] = {}
SEED_DPI = 300.0


class HdPipeline(Pipeline):
    id = "hd"
    name = "汉语大字典"
    description = (
        "《汉语大字典》第一版 / 第二版。两版都是双栏密排，字头比正文大，"
        "按「上游版面法（每栏最左一列）」或「第二版文字层」定位。"
    )

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings

    # ── 分册 ──────────────────────────────────────────────
    # 各册的正文页范围（PDF 页，1 基）：第一版正文从 297 页起，第二版从第 1 页起。
    # 配置里写了就以配置为准；没写就用这里的默认，避免把封面/版权页也当成字头页。
    PAGE_RANGE: ClassVar[dict[str, tuple[int, int]]] = {"1": (297, 5106), "2": (1, 5127)}

    def volumes(self) -> list[dict[str, Any]]:
        configured = self.settings.get("volumes")
        out: list[dict[str, Any]] = []
        if isinstance(configured, dict) and configured:
            for key in sorted(configured, key=str):
                cfg = configured[key] or {}
                label = cfg.get("label") or cfg.get("title") or f"第 {key} 册"
                first, last = self.PAGE_RANGE.get(str(key), (1, 0))
                out.append(
                    {
                        "vol": str(key),
                        "pdf": cfg.get("pdf") or self.settings.get("pdf", ""),
                        "first_page": int(cfg.get("first_page", first)),
                        "last_page": int(cfg.get("last_page", last)),
                        "offset": int(cfg.get("offset", 0)),
                        "title": cfg.get("title") or label,
                        "label": label,
                    }
                )
        else:
            label = self.settings.get("title") or "第一版"
            out.append(
                {
                    "vol": "1",
                    "pdf": self.settings.get("pdf", ""),
                    "first_page": int(self.settings.get("first_page", 297)),
                    "last_page": int(self.settings.get("last_page", 5106)),
                    "offset": int(self.settings.get("offset", 296)),
                    "title": label,
                    "label": label,
                }
            )
        return out

    def _volume(self, vol: str) -> dict[str, Any] | None:
        for item in self.volumes():
            if item["vol"] == vol:
                return item
        return None

    def _pdf_of(self, volume: dict[str, Any]) -> Path | None:
        path = self._require_file(str(volume.get("pdf", "")))
        return Path(path) if path else None

    def _volumes_with_pdf(self) -> list[dict[str, Any]]:
        return [item for item in self.volumes() if self._pdf_of(item)]

    def status(self) -> dict[str, Any]:
        available = []
        for item in self.volumes():
            pdf = self._pdf_of(item)
            if pdf:
                available.append(
                    {"vol": item["vol"], "title": item["title"], "label": item["label"], "pdf": str(pdf)}
                )
        if not available:
            return {
                "available": False,
                "reason": "未找到扫描 PDF，请在设置里指定《汉语大字典》第一版 / 第二版路径",
                "source": str(self.settings.get("pdf", "")),
            }
        return {
            "available": True,
            "reason": "",
            "source": available[0]["pdf"],
            "volumes": available,
            "pages": len(self.pages()),
        }

    def dpi(self) -> float:
        return float(self.settings.get("dpi", SEED_DPI))

    def zoom(self) -> float:
        return self.dpi() / 72.0

    def params_spec(self) -> list[dict[str, Any]]:
        return [
            param(
                "method",
                "定位方式",
                "enum",
                "layout",
                options=[
                    {"value": "layout", "label": "版面法（上游 hd_heads，推荐）"},
                    {"value": "text", "label": "文字层（上游 hd_v2）"},
                    {"value": "both", "label": "两者合并"},
                ],
                help="两版版式相同，版面法（每栏最左一列）对两版都有效。"
                "第二版文字层只给出一小部分字头（大字识别率低），只适合补充。",
            ),
            param(
                "min_ratio",
                "字头/正文高度比",
                "number",
                1.45,
                min=1.0,
                max=3.0,
                step=0.05,
                help="上游 hd_heads 的阈值：大字块高度至少是本页正文中位字高的这个倍数。",
            ),
            param("max_side", "字头最大边长", "int", 340, min=80, max=1200, step=10),
            param(
                "footer",
                "页脚忽略线",
                "number",
                0.9,
                min=0.5,
                max=1.0,
                step=0.01,
                help="页面高度这个比例以下的大型块（页码、藏书印）不当字头。设 1.0 关闭。",
            ),
            param(
                "min_pt",
                "文字层字号下限",
                "number",
                26.0,
                min=8.0,
                max=72.0,
                step=0.5,
                help="第二版正文约 15pt、字头约 30pt，上游取 26–40pt。",
            ),
            param("max_pt", "文字层字号上限", "number", 40.0, min=8.0, max=120.0, step=0.5),
            param(
                "use_index",
                "按索引过滤文字层候选",
                "bool",
                True,
                help="用 index/hd2.csv 里本页的字头序列排除古文字形示例（字符不在序列里的候选）。",
            ),
        ]

    # ── 页 ────────────────────────────────────────────────
    def _split(self, page_id: str) -> tuple[dict[str, Any], int]:
        if ":" in page_id:
            vol, pno = page_id.split(":", 1)
        else:
            vol, pno = self.volumes()[0]["vol"], page_id
        volume = self._volume(vol)
        if not volume:
            if self._volumes_with_pdf():
                raise KeyError(f"未知分册：{vol}")
            label = f"第 {vol} 册"
            volume = {
                "vol": vol,
                "title": label,
                "label": label,
                "pdf": "",
                "first_page": 1,
                "last_page": 0,
                "offset": 0,
            }
        page = int(pno)
        # 只认配置范围内的 PDF 页：范围外的页在书里不存在（封面/版权/空白页），
        # 直接报错，别让界面停在渲染不出来的页上。
        first = max(1, int(volume["first_page"]))
        last = int(volume["last_page"]) or 0
        if page < first or (last and page > last):
            span = f"{first}–{last}" if last else f"{first} 起"
            raise ValueError(f"{volume['label']} 只开放第 {span} 页（PDF 页），第 {page} 页不可访问")
        return volume, page

    def pages(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for volume in self.volumes():
            pdf = self._pdf_of(volume)
            if not pdf:
                continue
            total = imaging.page_count(pdf)
            first = max(1, volume["first_page"])
            last = min(volume["last_page"] or total, total)
            for pno in range(first, last + 1):
                printed = pno - volume["offset"]
                out.append(
                    {
                        "id": f"{volume['vol']}:{pno}",
                        "pdf_page": pno,
                        "printed_page": printed,
                        "volume": volume["vol"],
                        "label": f"{volume['label']} · 第 {printed} 页",
                    }
                )
        return out

    def page_label(self, page_id: str) -> str:
        volume, pno = self._split(page_id)
        return f"{volume['label']} · 第 {pno - volume['offset']} 页"

    def _render(self, page_id: str) -> np.ndarray:
        volume, pno = self._split(page_id)
        pdf = self._pdf_of(volume)
        if not pdf:
            raise FileNotFoundError(f"《汉语大字典》{volume['label']} PDF 未配置")
        return imaging.render_gray_cached(pdf, pno, self.zoom())

    def gray(self, page_id: str) -> np.ndarray:
        return self._render(page_id)

    def ink(self, page_id: str) -> np.ndarray:
        return imaging.gray_to_ink(self._render(page_id))

    # ── 预置数据（第一版定稿字头框） ───────────────────────
    def _seed_dir(self) -> Path:
        base = self.settings.get("seed_dir")
        return Path(base) if base else config.DATA_DIR / "_up" / "handa" / "data"

    def _seed_file(self) -> Path:
        configured = self.settings.get("seed_file")
        if configured:
            return Path(configured)
        return self._seed_dir() / "hd1-heads.ndjson"

    def pred_file(self) -> Path:
        """本工具自己跑出来的预测框（`scripts/predict_hd.py` 产出）。

        上游定稿只覆盖人工校过的部分页；没有定稿的页用这里补，
        同样是 `{"pdf": 0 基页号, "box": [x,y,w,h], "col": c}`。
        """
        configured = self.settings.get("predict_file")
        if configured:
            return Path(configured)
        return self._seed_dir() / "hd1-pred.ndjson"

    def _seed_index(self) -> dict[int, list[tuple[int, int, int, int, int]]]:
        files = (self._seed_file(), self.pred_file())
        key = "|".join(str(p) for p in files)
        if key in _SEED_CACHE:
            return _SEED_CACHE[key]

        grouped: dict[int, list[tuple[int, int, int, int, int]]] = {}
        # 上游定稿优先；预测文件只补定稿里没有的页
        for rank, path in enumerate(files):
            if not path.is_file():
                continue
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                        # 上游 hd1-heads.ndjson 的 `pdf` 是 **0 基**页索引
                        pno = int(row["pdf"]) + 1
                        x, y, w, h = (int(v) for v in row["box"])
                    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                        continue
                    if w <= 0 or h <= 0:
                        continue
                    if rank and pno in grouped:
                        continue
                    col = int(row.get("col", 0) or 0)
                    grouped.setdefault(pno, []).append((col, x, y, w, h))
        for rows in grouped.values():
            rows.sort(key=lambda r: (r[0], r[2], r[1]))
        _SEED_CACHE[key] = grouped
        return grouped

    def seed_boxes(self, page_id: str) -> tuple[list[Box], dict[str, Any]] | None:
        volume, pno = self._split(page_id)
        if volume["vol"] != "1":
            return None
        rows = self._seed_index().get(pno)
        if not rows:
            return None
        boxes = [Box(x=x, y=y, w=w, h=h, seq=i) for i, (_col, x, y, w, h) in enumerate(rows, 1)]
        return boxes, {"seed": "hd1-heads", "volume": volume["vol"], "page": pno, "dpi": self.dpi()}

    def seed_done(self, page_id: str) -> bool:
        volume, pno = self._split(page_id)
        if volume["vol"] != "1":
            return False
        return bool(self._seed_index().get(pno))

    def seed_counts_done(self) -> bool:
        # 第一版的定稿是自动检测结果，要人工过一遍才算校对完
        return bool(self.settings.get("seed_counts_done", False))

    # ── 第二版索引（文字层候选过滤） ───────────────────────
    def _index_file(self) -> Path:
        configured = self.settings.get("index_file")
        if configured:
            return Path(configured)
        base = self.settings.get("seed_dir")
        folder = Path(base) if base else config.DATA_DIR / "_up" / "handa" / "index"
        return folder / "hd2.csv"

    def _expect(self, pno: int) -> list[str]:
        path = self._index_file()
        key = str(path)
        table = _INDEX_CACHE.get(key)
        if table is None:
            table = {}
            if path.is_file():
                with path.open(encoding="utf-8-sig") as fh:
                    for row in csv.DictReader(fh):
                        char = (row.get("char") or "").strip()
                        try:
                            page = int(row["page"])
                        except (KeyError, TypeError, ValueError):
                            continue
                        if len(char) == 1:
                            table.setdefault(page, []).append(char)
            _INDEX_CACHE[key] = table
        return table.get(pno, [])

    # ── 检测 ──────────────────────────────────────────────
    def _layout_cands(self, gray: np.ndarray, params: dict[str, Any]) -> list[dict[str, Any]]:
        found = hd_heads.detect(
            gray,
            shrink=2,
            max_side=int(params.get("max_side", 340)),
            min_ratio=float(params.get("min_ratio", 1.45)),
            footer=float(params.get("footer", 0.9)),
        )
        return [
            {"col": int(item["col"]), "box": [int(v) for v in item["box"]], "char": ""}
            for item in found["cands"]
        ]

    def _text_cands(self, page_id: str, gray: np.ndarray, params: dict[str, Any]) -> list[dict[str, Any]]:
        volume, pno = self._split(page_id)
        pdf = self._pdf_of(volume)
        if not pdf:
            return []
        expect = self._expect(pno) if params.get("use_index", True) else None
        page = imaging.open_doc(pdf)[pno - 1]
        found = hd_v2.detect(
            page,
            gray,
            scale=self.zoom(),
            min_pt=float(params.get("min_pt", 26.0)),
            max_pt=float(params.get("max_pt", 40.0)),
            expect=expect,
        )
        return list(found["cands"])

    @staticmethod
    def _merge(cands: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """两路候选合并：同一字头（重叠 ≥50%）只留面积大的那个。"""
        kept: list[dict[str, Any]] = []
        for cand in sorted(cands, key=lambda c: -(c["box"][2] * c["box"][3])):
            x, y, w, h = cand["box"]
            dup = False
            for other in kept:
                ox, oy, ow, oh = other["box"]
                ix = max(0, min(x + w, ox + ow) - max(x, ox))
                iy = max(0, min(y + h, oy + oh) - max(y, oy))
                if ix * iy >= 0.5 * min(w * h, ow * oh):
                    dup = True
                    break
            if not dup:
                kept.append(cand)
        kept.sort(key=lambda c: (c["col"], c["box"][1], c["box"][0]))
        return kept

    def detect(self, page_id: str, params: dict[str, Any]) -> DetectResult:
        method = str(params.get("method", "layout"))
        gray = self.gray(page_id)
        cands: list[dict[str, Any]] = []
        used: list[str] = []
        if method in ("layout", "both"):
            try:
                cands.extend(self._layout_cands(gray, params))
                used.append("layout")
            except Exception:
                pass
        if method in ("text", "both"):
            try:
                found = self._text_cands(page_id, gray, params)
                if found:
                    cands.extend(found)
                    used.append("text")
            except Exception:
                pass

        boxes = [Box(x=b["box"][0], y=b["box"][1], w=b["box"][2], h=b["box"][3]) for b in self._merge(cands)]
        for i, box in enumerate(boxes, 1):
            box.seq = i
        return DetectResult(
            boxes=boxes,
            meta={"count": len(boxes), "dpi": self.dpi(), "method": "+".join(used) or "none"},
        )

    def file_prefix(self, page_id: str) -> str:
        volume, pno = self._split(page_id)
        return f"H{volume['vol']}-{pno:04d}"
