"""导出 API：单页导出（同步）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from .. import config, models, store
from ..core import imaging
from ..core.box import Box
from ..pipelines import registry

router = APIRouter(prefix="/api/books", tags=["export"])


@router.post("/{book}/pages/{page}/export")
async def export_page(book: str, page: str, request: models.ExportRequest) -> dict[str, Any]:
    try:
        pipeline = registry.get_pipeline(book)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    saved = store.load_boxes(book, page)
    if not saved:
        raise HTTPException(status_code=409, detail="该页还没有保存字框，先检测或手动画框")

    settings = config.load_settings()
    out_dir = Path(request.out_dir or settings.get("export_dir") or config.EXPORT_DIR_DEFAULT)
    out_dir.mkdir(parents=True, exist_ok=True)

    boxes = [Box.from_dict(b) for b in saved["boxes"]]
    ordered = pipeline.order(boxes)
    if not ordered:
        return {"book": book, "page": page, "count": 0, "files": []}

    try:
        images = pipeline.export_images(page, ordered, request.params, saved.get("extra", {}))
    except (KeyError, ValueError, IndexError) as exc:
        raise HTTPException(status_code=404, detail=f"页面编号无效：{exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    prefix = pipeline.file_prefix(page)
    digits = pipeline.export_digits()
    records = []
    for idx, (box, image) in enumerate(zip(ordered, images, strict=True), 1):
        name = f"{prefix}_{idx:0{digits}d}.png"
        target = out_dir / name
        imaging.save_png(target, image)
        records.append(
            {
                "file": str(target),
                "book": book,
                "page": page,
                "index": idx,
                "x": box.x,
                "y": box.y,
                "w": box.w,
                "h": box.h,
                "pos": box.pos,
                "band": box.band,
                "col": box.col,
            }
        )
    store.export_log.append(records)
    return {"book": book, "page": page, "count": len(records), "dir": str(out_dir), "files": records}
