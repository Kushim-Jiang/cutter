"""书源与页面 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from .. import checks, config, models, store
from ..core.box import Box
from ..pipelines import registry

router = APIRouter(prefix="/api/books", tags=["books"])

_meta_cache: dict[tuple[str, str], dict[str, Any]] = {}


def _pipeline(book: str):
    try:
        return registry.get_pipeline(book)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _to_http(exc: Exception) -> HTTPException:
    """页号/书源解析错误是 404，其余是 500。"""
    if isinstance(exc, (KeyError, ValueError, IndexError)):
        return HTTPException(status_code=404, detail=f"页面编号无效：{exc}")
    return HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


@router.get("")
async def list_books() -> dict[str, Any]:
    return {"books": [registry.describe(p) for p in registry.all_pipelines()]}


@router.get("/{book}")
async def get_book(book: str) -> dict[str, Any]:
    return registry.describe(_pipeline(book))


@router.get("/{book}/pages")
async def list_pages(book: str) -> dict[str, Any]:
    pipeline = _pipeline(book)
    pages = await run_in_threadpool(pipeline.pages)
    if config.load_settings().get("use_seed", True):
        for page in pages:
            page["seeded"] = pipeline.seed_done(page["id"])
    return {"book": book, "total": len(pages), "pages": pages}


@router.get("/{book}/pages/{page}/meta")
async def page_meta(book: str, page: str) -> dict[str, Any]:
    pipeline = _pipeline(book)
    key = (book, page)
    meta = _meta_cache.get(key)
    if meta is None:
        try:
            width, height = await run_in_threadpool(pipeline.page_size, page)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise _to_http(exc) from exc
        meta = {
            "book": book,
            "id": page,
            "label": pipeline.page_label(page),
            "width": width,
            "height": height,
        }
        _meta_cache[key] = meta
    saved = store.load_boxes(book, page)
    state = (saved or {}).get("state", {})
    seeded = False
    if not saved and config.load_settings().get("use_seed", True):
        try:
            seeded = await run_in_threadpool(pipeline.seed_done, page)
        except Exception:
            seeded = False
    return {
        **meta,
        "saved": bool(saved),
        "count": len(saved["boxes"]) if saved else 0,
        "done": bool(state.get("done")) or seeded,
        "suspect": bool(state.get("suspect")),
        "seeded": seeded,
    }


@router.get("/{book}/pages/{page}/preview")
async def page_preview(book: str, page: str, max_width: int = Query(0, ge=200, le=6000)) -> Response:
    pipeline = _pipeline(book)
    width = max_width or int(config.load_settings().get("preview_width", 1400))
    try:
        data = await run_in_threadpool(pipeline.preview, page, width)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise _to_http(exc) from exc
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


# 切图只取决于「源页 + 矩形 + 宽度」，参数一样结果就一样，交给浏览器缓存，
# 翻页翻回来、开关右侧标签都不用重新拉。
CROP_CACHE = "private, max-age=86400"


@router.get("/{book}/pages/{page}/crop")
async def page_crop(
    book: str, page: str, x: int, y: int, w: int, h: int, max_width: int = Query(400, ge=32, le=2000)
) -> Response:
    pipeline = _pipeline(book)
    try:
        data = await run_in_threadpool(pipeline.crop_png, page, x, y, w, h, max_width)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise _to_http(exc) from exc
    return Response(content=data, media_type="image/png", headers={"Cache-Control": CROP_CACHE})


@router.get("/{book}/pages/{page}/boxes")
async def get_boxes(book: str, page: str) -> dict[str, Any]:
    """本页字框：本地存盘 > 上游预置（已切好的数据）> 空。"""
    pipeline = _pipeline(book)
    saved = store.load_boxes(book, page)
    if saved:
        return {**saved, "saved": True, "origin": "local"}

    if config.load_settings().get("use_seed", True):
        try:
            seed = await run_in_threadpool(pipeline.seed_boxes, page)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise _to_http(exc) from exc
        if seed:
            boxes, extra = seed
            return {
                "book": book,
                "page": page,
                "boxes": [b.as_dict() for b in boxes],
                "extra": {**extra, "review": []},
                "state": {},
                "saved": True,
                "origin": "seed",
            }

    return {
        "book": book,
        "page": page,
        "boxes": [],
        "extra": {},
        "state": {},
        "saved": False,
        "origin": "none",
    }


@router.put("/{book}/pages/{page}/boxes")
async def put_boxes(book: str, page: str, request: models.SaveBoxesRequest) -> dict[str, Any]:
    _pipeline(book)
    boxes = [Box.from_dict(b.model_dump(exclude_none=True)) for b in request.boxes]
    try:
        payload = store.save_boxes(
            book,
            page,
            boxes,
            width=request.width,
            height=request.height,
            extra=request.extra,
            state=request.state,
            autosave=request.autosave,
            base_rev=request.base_rev,
        )
    except store.RevisionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "conflict": True,
                "rev": exc.rev,
                "count": len(exc.current.get("boxes", [])),
                "message": "本页已被其他窗口修改，请刷新后再改",
            },
        ) from exc
    return {
        "saved": True,
        "count": len(boxes),
        "page": page,
        "rev": payload["rev"],
        "saved_at": payload["saved_at"],
        "extra": payload["extra"],
        "state": payload["state"],
    }


@router.post("/{book}/pages/{page}/refine")
async def refine_page(book: str, page: str, request: models.RefineRequest) -> dict[str, Any]:
    """把框贴合成墨迹（贴合墨迹 / 本页全部重贴）。"""
    pipeline = _pipeline(book)
    boxes = [Box.from_dict(b.model_dump(exclude_none=True)) for b in request.boxes]
    try:
        refined = await run_in_threadpool(pipeline.refine_boxes, page, boxes, request.params)
        flags = await run_in_threadpool(pipeline.review_flags, page, refined)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise _to_http(exc) from exc

    rev = None
    if request.save:
        width, height = await run_in_threadpool(pipeline.page_size, page)
        payload = store.load_boxes(book, page) or {}
        saved = store.save_boxes(
            book,
            page,
            refined,
            width=width,
            height=height,
            extra=payload.get("extra", {}),
            state=payload.get("state", {}),
        )
        rev = saved["rev"]
    return {
        "book": book,
        "page": page,
        "boxes": [b.as_dict() for b in refined],
        "review": flags,
        "count": len(refined),
        "rev": rev,
    }


@router.post("/{book}/pages/{page}/state")
async def set_state(book: str, page: str, request: models.StateRequest) -> dict[str, Any]:
    pipeline = _pipeline(book)
    update = {k: v for k, v in request.model_dump().items() if v is not None}

    # 本页只有上游定稿、还没有本地记录时，把定稿的框一起带上再写状态，
    # 否则写出来的空框记录会顶掉整页定稿。
    base: dict[str, Any] | None = None
    if store.load_boxes(book, page) is None and config.load_settings().get("use_seed", True):
        try:
            seed = await run_in_threadpool(pipeline.seed_boxes, page)
        except Exception:
            seed = None
        if seed:
            boxes, extra = seed
            base = {"boxes": [b.as_dict() for b in boxes], "extra": dict(extra)}

    data = store.update_state(book, page, update, base=base)
    return {"page": page, "state": data.get("state", {}), "rev": data.get("rev", 0)}


@router.get("/{book}/nav")
async def next_page(book: str, after: str = "", mode: str = "undone") -> dict[str, Any]:
    """下一未完成 / 下一待查页。"""
    pipeline = _pipeline(book)
    pages = await run_in_threadpool(pipeline.pages)
    ids = [p["id"] for p in pages]
    start = ids.index(after) + 1 if after in ids else 0
    order = ids[start:] + ids[:start]
    for page_id in order:
        state = store.page_state(book, page_id)
        done = state["done"] or pipeline.seed_done(page_id)
        if mode == "suspect":
            if state["suspect"]:
                return {"page": page_id, "found": True}
        elif mode == "review":
            data = store.load_boxes(book, page_id) or {}
            if (data.get("extra") or {}).get("review"):
                return {"page": page_id, "found": True}
        elif mode == "done":
            if done:
                return {"page": page_id, "found": True}
        else:
            if not done:
                return {"page": page_id, "found": True}
    return {"page": "", "found": False}


@router.post("/{book}/pages/{page}/detect")
async def detect_page(book: str, page: str, request: models.DetectRequest) -> dict[str, Any]:
    pipeline = _pipeline(book)
    try:
        result = await run_in_threadpool(pipeline.detect, page, request.params)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise _to_http(exc) from exc

    ordered = pipeline.order(result.boxes)
    rev = None
    if request.save:
        width, height = await run_in_threadpool(pipeline.page_size, page)
        previous = store.load_boxes(book, page) or {}
        saved = store.save_boxes(
            book,
            page,
            ordered,
            width=width,
            height=height,
            extra=result.meta,
            state=previous.get("state", {}),
        )
        rev = saved["rev"]
    return {
        "book": book,
        "page": page,
        "boxes": [b.as_dict() for b in ordered],
        "meta": result.meta,
        "count": len(ordered),
        "rev": rev,
    }


@router.get("/{book}/pages/{page}/check")
async def check_page(book: str, page: str) -> dict[str, Any]:
    """本页合法性检查（汉字海含编号检查）。没存过框就先按书源检测一份。"""
    pipeline = _pipeline(book)
    saved = store.load_boxes(book, page)
    if saved:
        boxes = [Box.from_dict(b) for b in saved.get("boxes", [])]
        extra = saved.get("extra", {})
    elif config.load_settings().get("use_seed", True) and (
        seed := await run_in_threadpool(pipeline.seed_boxes, page)
    ):
        boxes, extra = pipeline.order(seed[0]), seed[1]
    else:
        try:
            result = await run_in_threadpool(pipeline.detect, page, pipeline.defaults())
        except FileNotFoundError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise _to_http(exc) from exc
        boxes = pipeline.order(result.boxes)
        extra = result.meta

    try:
        width, height = await run_in_threadpool(pipeline.page_size, page)
        report = await run_in_threadpool(
            checks.check_page, book, pipeline, page, boxes, (width, height), extra
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise _to_http(exc) from exc
    report["saved"] = bool(saved)
    return report


@router.get("/{book}/pages/{page}/history")
async def page_history(book: str, page: str) -> dict[str, Any]:
    """本页保存历史（本工具的每次写盘快照）+ 汉字海上游编辑记录（只读）。"""
    _pipeline(book)
    items = store.list_history(book, page)
    upstream = []
    if book == "hzh":
        upstream = await run_in_threadpool(_load_hzh_edits, page)
    return {"book": book, "page": page, "items": items, "upstream": upstream}


def _load_hzh_edits(page: str) -> list[dict[str, Any]]:
    """_zihai/hh/edits.ndjson 里这一页的上游人工改动，取最后 100 条。"""
    import json

    path = config.DATA_DIR / "_zihai" / "hh" / "edits.ndjson"
    if not path.is_file():
        return []
    try:
        target = int(page)
    except (TypeError, ValueError):
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if int(row.get("page") or 0) == target:
                rows.append(
                    {
                        "id": f"upstream:{row.get('id', '')}:{row.get('ts', '')}",
                        "time": row.get("ts", ""),
                        "action": row.get("action", ""),
                        "memo": row.get("memo", ""),
                        "entry": (row.get("after") or row.get("before") or {}).get("entry"),
                        "before": row.get("before"),
                        "after": row.get("after"),
                    }
                )
    return rows[-100:]


@router.get("/{book}/pages/{page}/history/{snapshot_id}")
async def history_item(book: str, page: str, snapshot_id: str) -> dict[str, Any]:
    _pipeline(book)
    data = store.load_history(book, page, snapshot_id)
    if not data:
        raise HTTPException(status_code=404, detail="没有这个历史版本")
    return data


@router.post("/{book}/pages/{page}/history/{snapshot_id}/restore")
async def history_restore(book: str, page: str, snapshot_id: str) -> dict[str, Any]:
    """把历史版本读回内存（是否写盘由前端决定，与校对台「回车才写盘」一致）。"""
    _pipeline(book)
    data = store.load_history(book, page, snapshot_id)
    if not data:
        raise HTTPException(status_code=404, detail="没有这个历史版本")
    return {
        "book": book,
        "page": page,
        "boxes": data.get("boxes", []),
        "count": len(data.get("boxes", [])),
        "time": data.get("time", ""),
    }
