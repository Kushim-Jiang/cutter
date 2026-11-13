"""对齐表接口：跨来源逐行核对（一列字形/文本 + 每卷切图）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from starlette.concurrency import run_in_threadpool

from .. import align, config, store
from ..pipelines import registry

router = APIRouter(prefix="/api/books", tags=["align"])

# 只有中华字海有三方来源（字海1.0 / 卷1 / 卷2）可交叉核对
ALIGN_BOOKS = {"zihai"}


def _pipeline(book: str):
    if book not in ALIGN_BOOKS:
        raise HTTPException(status_code=404, detail=f"{book} 没有对齐表（只有中华字海有三方来源）")
    try:
        return registry.get_pipeline(book)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _boxes_of(book: str, pipeline: Any, vol: str, printed: int) -> tuple[list[dict[str, Any]], str]:
    """取某一卷某印刷页的框（页内像素坐标）。

    本地存盘优先，其次上游定稿——这样在编辑器里改完框，回到表里看到的就是新结果，
    不必再跑一遍上游那 2.5 分钟的切图条重建。
    """
    page_id = _page_id(pipeline, vol, printed)
    saved = store.load_boxes(book, page_id)
    if saved and saved.get("boxes"):
        rows: list[Any] = saved["boxes"]
        origin = "local"
    else:
        rows = pipeline._edits(vol).get(str(printed)) or []
        origin = "seed"
    out = []
    for i, row in enumerate(rows):
        try:
            x, y = float(row["x"]), float(row["y"])
            w, h = float(row["w"]), float(row["h"])
        except (KeyError, TypeError, ValueError):
            continue
        if w <= 0 or h <= 0:
            continue
        out.append({"idx": i, "x": round(x, 1), "y": round(y, 1), "w": round(w, 1), "h": round(h, 1)})
    return out, origin


def _page_id(pipeline: Any, vol: str, printed: int) -> str:
    """印刷页 → `<卷>:<PDF页>`（卷1 有 offset，卷2 没有）。"""
    for item in pipeline.volumes():
        if item["vol"] == vol:
            return f"{vol}:{printed + int(item.get('offset', 0))}"
    return f"{vol}:{printed}"


def _page(book: str, printed: int) -> dict[str, Any]:
    pipeline = _pipeline(book)
    v1, o1 = _boxes_of(book, pipeline, "1", printed)
    v2, o2 = _boxes_of(book, pipeline, "2", printed)
    if not v1 and not v2 and not align.zh_index().get(printed):
        raise HTTPException(status_code=404, detail=f"第 {printed} 页三方都没有数据")

    result = align.build_rows(printed, v1, v2, align.notes().get(str(printed)) or [])
    v1_page = _page_id(pipeline, "1", printed)
    v2_page = _page_id(pipeline, "2", printed)
    for row in result["rows"]:
        for side, page_id in (("v1", v1_page), ("v2", v2_page)):
            box = row[side]
            if box:
                box["page"] = page_id
    return {
        "page": printed,
        "v1_page": v1_page,
        "v2_page": v2_page,
        "origin": {"v1": o1, "v2": o2},
        **result,
    }


@router.get("/{book}/align")
async def align_page(book: str, page: int = Query(1, ge=1, le=2000)) -> dict[str, Any]:
    try:
        return await run_in_threadpool(_page, book, page)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - 数据文件损坏等
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{book}/align/sprite")
async def align_sprite(
    book: str, page: int = Query(1, ge=1, le=2000), pos: int = Query(1, ge=1, le=99)
) -> Response:
    """字海1.0 书影（Z/P 系 ziid 不含码位，字形只能靠这张图）。"""
    _pipeline(book)
    data = await run_in_threadpool(align.sprite_png, page, pos)
    if not data:
        raise HTTPException(status_code=404, detail="这个位置没有雪碧图")
    return Response(content=data, media_type="image/png")


@router.get("/{book}/align/summary")
async def align_summary(book: str) -> dict[str, Any]:
    """全书逐页的对齐状态，用来挑出「对不上」的页。"""

    def build() -> dict[str, Any]:
        pipeline = _pipeline(book)
        total = 0
        bad: list[int] = []
        counts = {"zh": 0, "v1": 0, "v2": 0}
        for printed in range(1, 1782):
            v1, _ = _boxes_of(book, pipeline, "1", printed)
            v2, _ = _boxes_of(book, pipeline, "2", printed)
            if not v1 and not v2:
                continue
            total += 1
            result = align.build_rows(printed, v1, v2, align.notes().get(str(printed)) or [])
            for key in counts:
                counts[key] += result["counts"][key]
            if not result["aligned"]:
                bad.append(printed)
        return {
            "total": total,
            "aligned": total - len(bad),
            "bad": bad,
            "counts": counts,
            "pages": 1781,
            "data_dir": str(config.DATA_DIR),
        }

    try:
        return await run_in_threadpool(build)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc
