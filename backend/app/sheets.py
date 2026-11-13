"""手工对齐表：一列图 + 一列文本，人工把图配到正确的字上。

对应旧 PySide6 桌面端被删掉的 `ui_table/table_view.py`（导入图片/TSV、合并、拆分、
上下换行、导出 TSV），但数据存在服务端、界面在同一个 SPA 里。

和 `align.py` 的区别：那个是**只读**的三方核对（字海1.0 / 卷1 / 卷2），
这个是**可编辑**的通用工具——图片可以来自任何一页的切图或本地文件夹，
文本可以是粘进来的多行，也可以是 TSV。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from . import config, store
from .core import imaging

SHEET_DIR = config.WORK_DIR / "sheets"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


def _sheet_path(sheet_id: str) -> Path:
    return SHEET_DIR / f"{store._safe_name(sheet_id)[:80]}.json"


def _natural_key(name: str) -> list[Any]:
    """p1, p2, p10 这种按数字排，别按字符串排。"""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def list_sheets() -> list[dict[str, Any]]:
    out = []
    if SHEET_DIR.is_dir():
        for path in sorted(SHEET_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            out.append(
                {
                    "id": data.get("id", path.stem),
                    "name": data.get("name", ""),
                    "updated": data.get("updated", ""),
                    "rows": len(data.get("rows", [])),
                    "images": len(data.get("images", [])),
                }
            )
    out.sort(key=lambda item: item.get("updated", ""), reverse=True)
    return out


def load_sheet(sheet_id: str) -> dict[str, Any] | None:
    path = _sheet_path(sheet_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_sheet(sheet: dict[str, Any], base_rev: int | None = None) -> dict[str, Any]:
    """整份写盘（行数不多，整存整取最简单，也不会出现半张表）。"""
    current = load_sheet(sheet["id"]) or {}
    rev = int(current.get("rev", 0) or 0)
    if base_rev is not None and base_rev != rev:
        raise store.RevisionConflict(rev, current)
    sheet["rev"] = rev + 1
    sheet["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    store._atomic_write(_sheet_path(sheet["id"]), json.dumps(sheet, ensure_ascii=False))
    return sheet


def _texts_from(raw: str) -> list[str]:
    """粘贴/TSV 文本 → 每行一个。

    认两种常见形状：
    * 本工具导出的 `号 \\t 图 \\t 文本 \\t 备注`（4 列）→ 取第 3 列；
    * `号 \\t 文本`（2 列）→ 取第 2 列；
    其它情况（纯文本、单列）取整行第一列。
    """
    out = []
    for line in (raw or "").splitlines():
        if not line.strip():
            continue
        cells = line.split("\t")
        if cells[0].strip().isdigit():
            if len(cells) >= 3:
                out.append(cells[2])
                continue
            if len(cells) == 2:
                out.append(cells[1])
                continue
        out.append(cells[0])
    return [cell.strip() for cell in out]


def _page_images(book: str, page: str) -> list[dict[str, Any]]:
    from .pipelines import registry

    pipeline = registry.get_pipeline(book)
    saved = store.load_boxes(book, page)
    if saved and saved.get("boxes"):
        boxes = saved["boxes"]
    else:
        seed = pipeline.seed_boxes(page)
        if not seed:
            return []
        boxes = [b.as_dict() for b in seed[0]]
    return [
        {
            "page": page,
            "book": book,
            "x": round(float(b["x"])),
            "y": round(float(b["y"])),
            "w": round(float(b["w"])),
            "h": round(float(b["h"])),
        }
        for b in boxes
        if float(b.get("w") or 0) > 0 and float(b.get("h") or 0) > 0
    ]


def _dir_images(folder: str) -> list[dict[str, Any]]:
    base = Path(folder)
    if not base.is_dir():
        raise FileNotFoundError(f"目录不存在：{folder}")
    files = [p for p in base.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT]
    files.sort(key=lambda p: _natural_key(p.name))
    return [{"path": str(p)} for p in files]


def _parse_tsv(raw: str) -> list[dict[str, Any]]:
    """读 TSV：`# / img / text / note`，跳表头。

    `img` 列可以是文件名（配合 `folder`）或 `<页>#<序号>`（配合 `book`/`page`），
    空着就是不配图。
    """
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cells = line.split("\t")
        cells += [""] * (4 - len(cells))
        img = cells[1].strip()
        rows.append(
            {
                "img_ref": img,
                "img": int(img) if img.isdigit() else None,
                "page_ref": img if "#" in img else "",
                "text": cells[2].strip(),
                "note": cells[3].strip(),
            }
        )
    return rows


def _bind_images(
    rows: list[dict[str, Any]],
    images: list[dict[str, Any]],
) -> None:
    """把 TSV 里的图引用对到 images 下标上。"""
    by_name = {}
    for i, ref in enumerate(images):
        if ref.get("path"):
            by_name[Path(ref["path"]).name] = i
    for row in rows:
        target = row.pop("img_ref", "")
        page_ref = row.pop("page_ref", "")
        if page_ref and "#" in page_ref:
            try:
                row["img"] = int(page_ref.rsplit("#", 1)[1])
                continue
            except ValueError:
                row["img"] = None
                continue
        if target and target in by_name:
            row["img"] = by_name[target]
        elif row.get("img") is not None and not (0 <= int(row["img"]) < len(images)):
            row["img"] = None


def create_sheet(
    *,
    name: str = "",
    book: str = "",
    page: str = "",
    folder: str = "",
    texts: str = "",
    tsv_path: str = "",
) -> dict[str, Any]:
    """从「某页切图」或「本地文件夹」取图，配上文本建表。

    文本有两种来源：粘贴的多行，或一份 TSV（`tsv_path`，本工具导出的格式）。
    """
    if folder:
        images = _dir_images(folder)
        source = {"kind": "dir", "dir": folder}
    elif book and page:
        images = _page_images(book, page)
        source = {"kind": "page", "book": book, "page": page}
    else:
        images = []
        source = {"kind": "empty"}

    if tsv_path:
        path = Path(tsv_path)
        if not path.is_absolute():
            path = config.WORK_DIR / path
        if not path.is_file():
            raise FileNotFoundError(f"没有这个 TSV：{path}")
        rows = _parse_tsv(path.read_text(encoding="utf-8"))
        _bind_images(rows, images)
        source["tsv"] = str(path)
    else:
        text_list = _texts_from(texts)
        rows = []
        for i in range(max(len(images), len(text_list), 1)):
            rows.append(
                {
                    "img": i if i < len(images) else None,
                    "text": text_list[i] if i < len(text_list) else "",
                    "note": "",
                }
            )

    sheet = {
        "id": uuid.uuid4().hex[:12],
        "name": name or f"{source.get('page') or source.get('dir') or '未命名'} 对齐",
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "images": images,
        "rows": rows or [{"img": None, "text": "", "note": ""}],
    }
    return save_sheet(sheet)


def image_png(sheet: dict[str, Any], index: int, max_width: int = 160) -> bytes:
    images = sheet.get("images") or []
    if not (0 <= index < len(images)):
        raise KeyError(f"没有第 {index} 张图")
    ref = images[index]
    if ref.get("path"):
        import cv2
        import numpy as np

        arr = cv2.imdecode(np.fromfile(ref["path"], dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if arr is None:
            raise ValueError(f"读不出图片：{ref['path']}")
        return imaging.encode_png(imaging.downscale(arr, max_width))
    from .pipelines import registry

    pipeline = registry.get_pipeline(ref["book"])
    return pipeline.crop_png(
        ref["page"], int(ref["x"]), int(ref["y"]), int(ref["w"]), int(ref["h"]), max_width
    )


def export_tsv(sheet: dict[str, Any], out_path: str) -> dict[str, Any]:
    path = Path(out_path)
    if not path.is_absolute():
        # 相对路径落到工作目录，和 boxes / history 放一起
        path = config.WORK_DIR / path
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["#\timg\ttext\tnote"]
    images = sheet.get("images") or []
    for i, row in enumerate(sheet.get("rows") or [], 1):
        slot = row.get("img")
        if slot is None or not (0 <= int(slot) < len(images)):
            label = ""
        else:
            ref = images[int(slot)]
            label = Path(ref["path"]).name if ref.get("path") else f"{ref['page']}#{slot}"
        lines.append(f"{i}\t{label}\t{row.get('text', '')}\t{row.get('note', '')}".replace("\n", " "))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"path": str(path), "rows": len(lines) - 1}
